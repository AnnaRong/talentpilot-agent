import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from talentpilot import actions, privacy, workflows
from talentpilot.italent_browser import _collect, add_interview_counts
from talentpilot.config import settings
from talentpilot.model_agent import explain_metrics
from talentpilot.platform_store import digest
from talentpilot.runtime import AGENTS, Context, Runtime
from talentpilot.security import authorize_dataset, safe_input


def sample():
    return {
        "schema_version": "talentpilot-ledger/2",
        "run": {"completed_at": datetime.now(timezone.utc).isoformat(), "scope": "SYNTHETIC TEST ONLY"},
        "positions": [{"job_id": "J_TEST", "job_uuid": "job_test", "department": "TEST", "title": "Test"}],
        "applications": [{"application_id": "a_test", "candidate_id": "c_test", "job_id": "J_TEST",
                          "job_uuid": "job_test", "current_stage": "一面", "current_status": "进行中"}],
        "interviews": [
            {"interview_id": "i_test", "application_id": "a_test", "candidate_id": "c_test",
             "job_id": "J_TEST", "round": "first", "status": "已完成", "overall_result": "通过",
             "in_application_scope": True, "scheduled_at": "2026-09-21T10:00:00+08:00",
             "interviewers": [
                 {"interviewer_id": "p_test", "result": "通过", "feedback": "Synthetic evidence A",
                  "feedback_collected": True, "evaluation_id": "e_test"},
                 {"interviewer_id": "q_test", "result": "淘汰", "feedback": "Synthetic evidence B",
                  "feedback_collected": True, "evaluation_id": "f_test"}]},
        ],
        "quality": {"coverage": {"positions": {"declared": 1}, "applications": {"declared": 1},
                                  "interviews": {"declared": 1}}, "warnings": ["synthetic"]},
    }


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root)
        self.store = self.runtime.store
        self.capture = sample()
        self.dataset_id = self.store.import_dataset(self.capture, "test")

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def submit(self, kind="audit", key="test-request", **kwargs):
        return self.runtime.submit(kind, "test", self.dataset_id, {}, key, **kwargs)

    def test_pipeline_and_real_gap(self):
        self.submit()
        self.runtime.tick()
        job = self.runtime.jobs()[0]
        self.assertEqual(job["status"], "completed_with_gaps")
        self.assertEqual(job["used"], 4)
        self.assertEqual(job["result"]["analytics"]["jobs"][0]["passed"], 1)
        self.assertIsNone(job["result"]["analytics"]["sent_offers"])
        self.assertEqual(job["result"]["insights"]["groups"][0]["classification"], "disagreement")
        self.assertEqual(job["result"]["locks"]["status"], "awaiting_source")

    def test_position_overview_counts_distinct_candidates_by_round(self):
        positions = {"positions": [{"job_id": "J_TEST", "hc": "3"}],
                     "declared": 1, "captured": 1, "collected_at": "jobs-at"}
        interview = sample()["interviews"][0]
        interviews = {"interviews": [
            {**interview, "interview_id": "i1", "round": "一面"},
            {**interview, "interview_id": "i2", "round": "一面"},
            {**interview, "interview_id": "i3", "candidate_id": "c2", "round": "二面"},
            {**interview, "interview_id": "i4", "job_id": "J_OTHER", "round": "三面"},
        ], "declared": 4, "captured": 4, "collected_at": "interviews-at"}
        result = add_interview_counts(positions, interviews)
        self.assertEqual(result["positions"][0]["hc"], "3")
        self.assertEqual([result["positions"][0][field] for field in
                          ("first_interview_count", "second_interview_count", "third_interview_count")],
                         [1, 1, 0])
        self.assertEqual(result["interviews_captured"], 4)

    def test_interview_collection_failure_explains_partial_success(self):
        completed = Mock(returncode=1, stdout="", stderr="Error: Invalid interview page")
        with patch("talentpilot.italent_browser.shutil.which", return_value="ego-browser"), \
             patch("talentpilot.italent_browser.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(ValueError, "岗位读取成功，但面试人数读取失败"):
                _collect("collect_interviews.js", "verified_interview_inventory", 1)

    def test_idempotency_concurrent(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.submit(), range(8)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(ValueError):
            self.submit(kind="loads")

    def test_worker_claim_exclusive(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=3) as pool:
            claims = list(pool.map(lambda _: self.runtime.claim(), range(3)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_restart_recovers_expired_lease(self):
        job = self.submit()
        self.runtime.claim()
        with self.store.transaction() as cx:
            cx.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, job))
        runtime2 = Runtime(self.root)
        runtime2.tick()
        self.assertEqual(runtime2.jobs()[0]["attempts"], 2)
        self.assertEqual(runtime2.jobs()[0]["status"], "completed_with_gaps")

    def test_retry_exhaustion(self):
        self.submit()
        self.runtime.claim()
        with self.store.transaction() as cx:
            cx.execute("UPDATE jobs SET attempts=2,lease_until=?", (time.time() - 1,))
        self.assertIsNone(self.runtime.claim())
        self.assertEqual(self.runtime.jobs()[0]["status"], "failed")

    def test_tool_budget_and_permission(self):
        self.submit(budget=1)
        self.runtime.tick()
        self.assertEqual(self.runtime.jobs()[0]["status"], "failed")
        context = Context(self.runtime, {"id": "missing"}, AGENTS["analytics"], time.monotonic() + 30)
        with self.assertRaises(PermissionError):
            context.call("recommend", self.capture, {})

    def test_timeout(self):
        job = self.submit()
        with self.assertRaises(TimeoutError):
            self.runtime.check_budget(job, time.monotonic() - 1)

    def test_candidate_identity_no_filename_fallback(self):
        from talentpilot.orchestrator import build_batch_plan
        with self.assertRaises(ValueError):
            build_batch_plan([{"filename": f"{i}.pdf"} for i in range(20)])

    def test_snapshot_and_memory_encrypted(self):
        self.submit()
        self.runtime.tick()
        with self.store.transaction() as cx:
            blob = cx.execute("SELECT payload FROM datasets").fetchone()[0]
            self.assertNotIn(b"Synthetic evidence A", blob)
            self.assertEqual(len(cx.execute("SELECT * FROM memories").fetchall()), 4)
        self.assertEqual(self.store.dataset(self.dataset_id), self.capture)
        self.assertEqual((self.root / "platform.key").stat().st_mode & 0o777, 0o600)

    def test_department_boundary(self):
        with self.assertRaises(HTTPException):
            authorize_dataset(self.store, {"departments": ["OTHER"]}, self.dataset_id)
        authorize_dataset(self.store, {"departments": ["TEST"]}, self.dataset_id)

    def test_historical_unknown_department_denied(self):
        capture = copy.deepcopy(self.capture)
        historical = copy.deepcopy(capture["interviews"][0])
        historical.update(interview_id="old_interview", application_id="old_application",
                          candidate_id="old_candidate", job_id="unknown_historical_job")
        capture["interviews"].append(historical)
        capture["quality"]["coverage"]["interviews"]["declared"] = 2
        dataset_id = self.store.import_dataset(capture, "test")
        with self.assertRaises(HTTPException):
            authorize_dataset(self.store, {"departments": ["TEST"]}, dataset_id)
        authorize_dataset(self.store, {"departments": ["*"]}, dataset_id)

    def test_path_boundary(self):
        with self.assertRaises(HTTPException):
            safe_input("/etc/passwd")

    def approval(self, kind="report.export", payload=None):
        action_id = actions.propose(self.store, "test", kind, self.dataset_id, payload or {})
        with self.store.transaction() as cx:
            row = dict(cx.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())
        return action_id, row

    def test_approval_hash_expiry_and_replay(self):
        action_id, row = self.approval()
        with self.assertRaises(ValueError):
            actions.execute(self.store, action_id, "test")
        with self.assertRaises(ValueError):
            actions.decide(self.store, action_id, "lead", "approved", "wrong")
        actions.decide(self.store, action_id, "lead", "approved", row["payload_hash"])
        with self.assertRaises(ValueError):
            actions.decide(self.store, action_id, "lead", "rejected", row["payload_hash"])
        self.assertEqual(actions.execute(self.store, action_id, "lead")["status"], "executed")
        with self.assertRaises(ValueError):
            actions.execute(self.store, action_id, "lead")

    def test_approval_expired(self):
        action_id, row = self.approval()
        with self.store.transaction() as cx:
            cx.execute("UPDATE actions SET expires=? WHERE id=?", (time.time() - 1, action_id))
        with self.assertRaises(ValueError):
            actions.decide(self.store, action_id, "lead", "approved", row["payload_hash"])

    def test_approval_tampering(self):
        action_id, row = self.approval()
        with self.store.transaction() as cx:
            cx.execute("UPDATE actions SET payload=? WHERE id=?", (self.store.seal({"tampered": True}), action_id))
        with self.assertRaises(ValueError):
            actions.decide(self.store, action_id, "lead", "approved", row["payload_hash"])

    def test_external_action_is_blocked_even_approved(self):
        action_id, row = self.approval("boss.outreach", {"candidate_ids": ["c_test"]})
        actions.decide(self.store, action_id, "lead", "approved", row["payload_hash"])
        result = actions.execute(self.store, action_id, "lead")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["external_actions"], 0)

    def test_scope_needed_for_external_proposal(self):
        for kind in ("talent.advance", "ruliu.publish"):
            with self.assertRaises(ValueError):
                self.approval(kind)

    def test_rejected_never_executes(self):
        action_id, row = self.approval()
        actions.decide(self.store, action_id, "lead", "rejected", row["payload_hash"])
        with self.assertRaises(ValueError):
            actions.execute(self.store, action_id, "lead")

    def test_unknown_offer_and_cancelled_not_pass(self):
        c = copy.deepcopy(self.capture)
        c["interviews"][0]["status"] = "已取消"
        report = workflows.analytics(c)
        self.assertIsNone(report["jobs"][0]["pass_rate"])
        self.assertIsNone(report["sent_offers"])

    def test_unobserved_job_not_zero_population(self):
        self.capture["positions"].append({"job_id": "J_UNSEEN", "job_uuid": "unseen"})
        result = workflows.analytics(self.capture)["jobs"][1]
        self.assertIsNone(result["applications"])
        self.assertEqual(result["captured_application_count"], 0)
        self.assertEqual(result["application_coverage"], "not_observed")

    def test_placeholder_feedback_not_evidence(self):
        for person in self.capture["interviews"][0]["interviewers"]:
            person["feedback"] = "页面未展示面评正文"
        result = workflows.insights(self.capture)
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["missing_or_placeholder_feedback"], 2)

    def test_out_of_scope_interview_not_in_job_rate(self):
        self.capture["interviews"][0]["in_application_scope"] = False
        self.assertEqual(workflows.analytics(self.capture)["jobs"][0]["completed_explicit"], 0)

    def test_locks_thresholds_stale_and_dedup(self):
        now = datetime.now(timezone.utc)
        self.capture["locks"] = [
            {"lock_id": "l1", "candidate_id": "c_test", "job_id": "J_TEST",
             "observed_at": now.isoformat(), "expires_at": (now + timedelta(minutes=40)).isoformat(),
             "state": "locked", "final_scheduled": False},
            {"lock_id": "old", "candidate_id": "c_test", "job_id": "J_TEST",
             "observed_at": (now - timedelta(hours=2)).isoformat(),
             "expires_at": (now + timedelta(minutes=20)).isoformat(), "state": "locked"},
        ]
        dataset_id = self.store.import_dataset(self.capture, "test")
        for i in range(2):
            self.runtime.submit("locks", "test", dataset_id, {}, str(i))
            self.runtime.tick()
        with self.store.transaction() as cx:
            rows = cx.execute("SELECT payload FROM alerts").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.store.unseal(rows[0]["payload"])["level"], "1h")

    def test_talent_pool_requires_all_safety_evidence(self):
        self.capture["talent_pool"] = [{"candidate_id": "new_test", "consent_to_contact": True}]
        result = workflows.recommend(self.capture, {"job_id": "J_TEST"})
        self.assertEqual(result["recommendations"][0]["status"], "needs_review")
        self.assertIsNone(result["recommendations"][0]["draft"])

    def test_talent_existing_application_blocked(self):
        self.capture["talent_pool"] = [
            {"candidate_id": "c_test", "consent_to_contact": True, "locked": False,
             "in_process": False, "blacklisted": False,
             "checked_at": datetime.now(timezone.utc).isoformat(),
             "role_evidence": [{"source_id": "resume_test", "quote": "Synthetic evidence"}]}]
        result = workflows.recommend(self.capture, {"job_id": "J_TEST"})
        self.assertIn("Existing application", " ".join(result["recommendations"][0]["risks"]))

    def test_load_timezone_and_cancellation(self):
        result = workflows.loads(self.capture, {"date": "2026-09-21"})
        self.assertEqual(result["interviewers"][0]["count"], 1)
        self.capture["interviews"][0]["status"] = "已取消"
        self.assertEqual(workflows.loads(self.capture, {"date": "2026-09-21"})["interviewers"], [])

    def test_local_timestamp_requires_explicit_zone(self):
        self.capture["interviews"][0]["scheduled_at"] = "2026/09/21 10:00:00"
        self.assertEqual(workflows.loads(self.capture, {"date": "2026-09-21"})["interviewers"], [])
        result = workflows.loads(self.capture, {"date": "2026-09-21", "source_timezone": "Asia/Shanghai"})
        self.assertEqual(result["interviewers"][0]["count"], 1)
        self.assertEqual(result["timezone_applied_records"], 1)

    def test_privacy_consent_encryption_deletion(self):
        with self.assertRaises(ValueError):
            privacy.collect(self.store, "admin", {"record": {"private": "text"}})
        now = datetime.now(timezone.utc)
        body = {"candidate_id": "private_test", "purpose": "recruiting", "policy_version": "test",
                "region": "test", "consent_evidence": "signed-test-receipt",
                "consent_at": now.isoformat(), "expires_at": (now + timedelta(days=1)).isoformat(),
                "record": {"sensitive_test": "DO_NOT_LEAK"}}
        privacy.collect(self.store, "admin", body)
        with self.store.transaction() as cx:
            row = cx.execute("SELECT payload FROM private_records").fetchone()
        self.assertNotIn(b"DO_NOT_LEAK", row["payload"])
        deleted = privacy.delete(self.store, "admin", "private_test")
        self.assertEqual(deleted["deleted"], 1)
        self.assertFalse(deleted["full_erasure_complete"])

    def test_model_without_egress_never_calls_network(self):
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "0"}):
            result = explain_metrics(self.capture, {})
        self.assertEqual(result["llm_calls"], 0)

    def test_model_tool_loop_and_evaluator(self):
        outputs = [
            {"tool": "lookup_metric", "metric_id": "job_1.applications"},
            {"summary": [{"metric_id": "job_1.applications", "value": 1, "interpretation": "One application"}]},
        ]
        def transport(request):
            self.assertNotIn("c_test", request.content.decode())
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(outputs.pop(0))}}],
                                           "usage": {"total_tokens": 100}})
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "1", "LLM_ALLOWED_HOST": "gateway.test"}), \
             patch.object(settings, "llm_api_key", "test-only"), \
             patch.object(settings, "llm_base_url", "https://gateway.test/v1"), \
             httpx.Client(transport=httpx.MockTransport(transport)) as client:
            result = explain_metrics(self.capture, {}, client)
        self.assertEqual(result["llm_calls"], 2)
        self.assertTrue(result["numeric_citations_verified"])
        self.assertFalse(result["narrative_verified"])

    def test_model_forbidden_tool_rejected(self):
        def transport(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"tool":"send_message"}'}}]})
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "1", "LLM_ALLOWED_HOST": "gateway.test"}), \
             patch.object(settings, "llm_api_key", "test-only"), \
             patch.object(settings, "llm_base_url", "https://gateway.test/v1"), \
             httpx.Client(transport=httpx.MockTransport(transport)) as client:
            with self.assertRaises(ValueError):
                explain_metrics(self.capture, {}, client)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patchers = []
        for key, value in {
            "data_dir": self.root, "db_path": self.root / "legacy.sqlite3",
            "reports_dir": self.root / "reports", "plans_dir": self.root / "plans",
            "recordings_dir": self.root / "recordings", "exports_dir": self.root / "exports",
            "resume_archive_dir": self.root / "resumes",
            "resume_organizer_memory": self.root / "organizer-memory.md",
        }.items():
            patcher = patch.object(settings, key, value)
            patcher.start()
            self.patchers.append(patcher)
        from talentpilot.server import app
        self.client = TestClient(app)
        self.client.__enter__()
        self.app = app

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self.patchers):
            p.stop()
        self.temp.cleanup()

    def login(self):
        self.assertEqual(self.client.get("/api/platform/me").status_code, 200)

    def test_auth_origin_and_legacy_placeholder(self):
        self.assertEqual(self.client.get("/api/platform/jobs").status_code, 200)
        self.assertEqual(self.client.post("/api/platform/login", json={}).status_code, 404)
        self.assertEqual(self.client.get("/api/platform/jobs").status_code, 200)
        self.assertEqual(self.client.post("/api/platform/datasets/latest", json={},
                                         headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"kind": "recruitment_workflow",
                                                            "goal": "test"}).status_code, 409)
        self.assertEqual(self.client.post("/api/reports/from-file", params={"path": "/etc/passwd"}).status_code, 403)

    def test_api_real_end_to_end(self):
        self.login()
        dataset_id = self.client.post("/api/platform/datasets", json=sample()).json()["dataset_id"]
        response = self.client.post("/api/platform/jobs", json={
            "kind": "audit", "dataset_id": dataset_id, "request_key": "api-test-request"})
        self.assertEqual(response.status_code, 200)
        for _ in range(60):
            jobs = self.client.get("/api/platform/jobs").json()
            if jobs and jobs[0]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.05)
        self.assertEqual(jobs[0]["status"], "completed_with_gaps")
        self.assertEqual(jobs[0]["result"]["analytics"]["jobs"][0]["applications"], 1)

    def test_design_mode_has_no_login_or_logout_routes(self):
        self.assertEqual(self.client.post("/api/platform/login", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/platform/logout", json={}).status_code, 404)

    def test_recruitment_report_prefers_complete_snapshot_and_preserves_data(self):
        self.assertIsNone(self.client.get("/api/platform/recruitment-report").json()["dataset_id"])
        capture = sample()
        capture["run"]["completed_at"] = "2026-01-01T00:00:00+00:00"
        original = self.client.post("/api/platform/datasets", json=capture).json()["dataset_id"]
        partial = copy.deepcopy(capture)
        partial["run"]["completed_at"] = "2026-01-02T00:00:00+00:00"
        partial["interviews"] = []
        partial["quality"]["coverage"]["interviews"] = {"declared": 0, "state": "not_collected"}
        self.client.post("/api/platform/datasets", json=partial)
        report = self.client.get("/api/platform/recruitment-report").json()
        self.assertEqual(report["dataset_id"], original)
        self.assertEqual(len(report["columns"]), 18)
        self.assertEqual(report["external_actions"], 0)
        self.assertEqual(len(self.client.get("/api/platform/datasets").json()), 2)
        with patch("talentpilot.platform_api.authorize_dataset", side_effect=HTTPException(403)):
            self.assertEqual(self.client.get("/api/platform/recruitment-report").json()["groups"], [])

    def test_resume_organizer_status_reports_four_categories(self):
        resume = self.root / "resumes" / "社招" / "产品经理" / "20260923" / "张三.pdf"
        resume.parent.mkdir(parents=True)
        resume.write_bytes(b"synthetic")
        settings.resume_organizer_memory.write_text("- 2026-09-23 22:00 CST 本次运行：测试记录\n")
        response = self.client.get("/api/platform/resume-organizer")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual([item["category"] for item in result["categories"]],
                         ["校招", "实习生", "社招", "高端人才"])
        self.assertEqual(result["total_files"], 1)
        self.assertIn("本次运行", result["last_report"])

    def test_legacy_empty_audit_never_completes_successfully(self):
        self.login()
        path = self.root / "imports" / "empty.json"
        path.parent.mkdir()
        path.write_text("{}")
        response = self.client.post("/api/runs", json={
            "kind": "audit", "goal": "empty evidence test", "input_path": str(path)})
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run_id"]
        for _ in range(60):
            result = self.client.get("/api/runs/" + run_id).json()
            if result["status"] in {"failed", "completed"}:
                break
            time.sleep(.01)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.client.post("/api/reports/from-file", params={"path": str(path)}).status_code, 409)

    def test_hr_cannot_view_insights_or_approve(self):
        self.login()
        self.assertEqual(self.client.post("/api/platform/users", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
