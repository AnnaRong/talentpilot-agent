import asyncio
import base64
import copy
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from talentpilot import actions, knowledge, operations
from talentpilot.ledger_export import MAIN, csv_bytes, export_bundle, tables
from talentpilot.paper_mcp import call
from talentpilot.runtime import Runtime
import test_platform
from test_platform import sample


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))
        self.store = self.runtime.store
        self.capture = sample()
        self.dataset = self.store.import_dataset(self.capture, "local-admin")

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def panel(self, revision=0, rounds=None):
        return operations.save_panel(self.store, "local-admin", self.dataset, "J_TEST", "TEST",
                                     rounds or {"first": ["p_test"], "second": ["q_test"]}, revision)

    def test_panel_exact_scope_and_revision(self):
        self.assertEqual(self.panel()["revision"], 1)
        with self.assertRaises(ValueError):
            self.panel()
        self.assertEqual(self.panel(1)["revision"], 2)
        with self.store.transaction() as cx:
            self.assertEqual(cx.execute("SELECT count(*) FROM panel_history").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            operations.save_panel(self.store, "local-admin", self.dataset, "J_TEST", "OTHER", {"first": ["x"]}, 0)

    def test_panel_concurrent_confirmation(self):
        def write():
            try:
                return self.panel()
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: write(), range(4)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_panel_auto_applied_and_missing(self):
        before = operations.assignments(self.store, self.dataset)
        self.assertEqual(before["assignments"][0]["status"], "awaiting_panel")
        self.panel()
        after = operations.assignments(self.store, self.dataset)
        self.assertEqual(after["assignments"][0]["rounds"]["first"], ["p_test"])
        self.assertFalse(after["ats_interviewer_assignment"])
        with self.assertRaises(ValueError):
            self.panel(1, {"first": ["x", "x"]})

    def test_schedule_disabled_default_and_idempotency(self):
        schedule_id = operations.create_schedule(self.runtime, "local-admin", self.dataset, "analytics", {}, 900)
        self.assertEqual(operations.dispatch_due(self.runtime), 0)
        with self.store.transaction() as cx:
            cx.execute("UPDATE schedules SET enabled=1 WHERE id=?", (schedule_id,))
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: operations.dispatch_due(self.runtime), range(4)))
        self.assertEqual(len(self.runtime.jobs()), 1)
        self.runtime.tick()
        self.assertEqual(self.runtime.jobs()[0]["status"], "completed")

    def test_schedule_stale_source_alert_not_fake_success(self):
        capture = copy.deepcopy(self.capture)
        capture["run"]["completed_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        dataset = self.store.import_dataset(capture, "local-admin")
        operations.create_schedule(self.runtime, "local-admin", dataset, "locks", {}, 900, True)
        operations.dispatch_due(self.runtime)
        self.assertEqual(self.runtime.jobs(), [])
        with self.store.transaction() as cx:
            row = cx.execute("SELECT * FROM schedules").fetchone()
            self.assertIn("older than 30 minutes", row["last_error"])
            self.assertEqual(cx.execute("SELECT count(*) FROM alerts").fetchone()[0], 1)

    def test_schedule_revoked_role(self):
        operations.create_schedule(self.runtime, "local-admin", self.dataset, "insights", {}, 900, True)
        with self.store.transaction() as cx:
            cx.execute("UPDATE users SET role='hr' WHERE id='local-admin'")
        operations.dispatch_due(self.runtime)
        self.assertEqual(self.runtime.jobs(), [])

    def test_knowledge_encryption_scope_and_citations(self):
        knowledge.add(self.store, "local-admin", "TEST", "jd:test", "Synthetic JD", "招聘平台 Python 面试流程 证据")
        self.assertEqual(knowledge.search(self.store, ["OTHER"], "招聘")["results"], [])
        result = knowledge.search(self.store, ["TEST"], "招聘")
        self.assertEqual(result["results"][0]["source_id"], "jd:test")
        self.assertIn("招聘", result["results"][0]["quote"])
        self.assertFalse(result["egress"])
        with self.store.transaction() as cx:
            self.assertNotIn(b"Python", cx.execute("SELECT payload FROM knowledge").fetchone()[0])

    def test_csv_contract_safe_formula_and_sources(self):
        self.assertEqual(len(MAIN), 19)
        self.assertTrue(csv_bytes(["x"], [["=HYPERLINK()"]]).decode("utf-8-sig").endswith("'=HYPERLINK()\r\n"))
        self.capture["interviews"][0]["round"] = "一面"
        data = tables(self.capture)
        self.assertEqual(len(data["招聘台账.csv"][1][0]), 19)
        self.assertIn("i_test", data["招聘台账.csv"][1][0][11])
        with zipfile.ZipFile(io.BytesIO(export_bundle(self.capture))) as archive:
            self.assertEqual(len(archive.namelist()), 6)
            self.assertIsNone(json.loads(archive.read("数据口径.json"))["sent_offers"])

    def test_csv_join_does_not_cross_candidate_job(self):
        self.capture["applications"].append({
            "application_id": "other", "candidate_id": "other", "job_id": "J_TEST", "job_uuid": "job_test"})
        self.capture["quality"]["coverage"]["applications"]["declared"] = 2
        self.capture["interviews"][0]["round"] = "一面"
        self.assertEqual(tables(self.capture)["招聘台账.csv"][1][1][11], "无已采集证据")

    def test_csv_approval_one_time(self):
        action_id = actions.propose(self.store, "local-admin", "report.export", self.dataset, {"format": "csv_bundle"})
        with self.store.transaction() as cx:
            hash_value = cx.execute("SELECT payload_hash FROM actions WHERE id=?", (action_id,)).fetchone()[0]
        actions.decide(self.store, action_id, "local-admin", "approved", hash_value)
        result = actions.execute(self.store, action_id, "local-admin")
        self.assertTrue(base64.b64decode(result["content_base64"]).startswith(b"PK"))
        with self.assertRaises(ValueError):
            actions.execute(self.store, action_id, "local-admin")

    def test_mcp_forbidden_download_and_unconfigured(self):
        with self.assertRaises(ValueError):
            asyncio.run(call("fetch_paper", {"identifier": "10.test"}))
        with patch.dict(os.environ, {"INSTSCI_MCP_COMMAND": "/not-installed/instsci-mcp"}):
            with self.assertRaises(ValueError):
                asyncio.run(call("tools/list", {}))

    def test_no_interviews_is_partial(self):
        capture = copy.deepcopy(self.capture)
        capture["interviews"] = []
        capture["quality"]["coverage"]["interviews"]["declared"] = 0
        dataset = self.store.import_dataset(capture, "local-admin")
        self.runtime.submit("analytics", "local-admin", dataset, {}, "test-partial")
        self.runtime.tick()
        self.assertEqual(self.runtime.jobs()[0]["status"], "completed_with_gaps")

    def test_future_and_unknown_timezone_not_in_denominator(self):
        from talentpilot.workflows import analytics
        self.capture["interviews"][0]["scheduled_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertIsNone(analytics(self.capture)["jobs"][0]["pass_rate"])
        self.capture["interviews"][0]["scheduled_at"] = "2026/09/20 10:00:00"
        self.assertIsNone(analytics(self.capture)["jobs"][0]["pass_rate"])

    def test_interview_join_and_timezone(self):
        from talentpilot.italent_browser import combine_interviews
        c = copy.deepcopy(self.capture)
        c["interviews"] = []
        c["quality"]["pages"] = {}
        interview = copy.deepcopy(self.capture["interviews"][0])
        interview["scheduled_at"] = "2026/09/20 10:00:00"
        source = {"interviews": [interview], "declared": 1, "captured": 1,
                  "pages": [{"page": 1}], "scope": "SYNTHETIC", "collected_at": self.capture["run"]["completed_at"]}
        result = combine_interviews(c, source)
        self.assertTrue(result["interviews"][0]["scheduled_at"].endswith("+08:00"))
        self.assertTrue(result["interviews"][0]["in_application_scope"])
        source["interviews"][0]["candidate_id"] = "wrong"
        with self.assertRaises(ValueError):
            combine_interviews(c, source)


class OperationsApiTests(unittest.TestCase):
    setUp = test_platform.ApiTests.setUp
    tearDown = test_platform.ApiTests.tearDown
    login = test_platform.ApiTests.login
    def test_new_controls_and_role_boundaries(self):
        self.login()
        dataset = self.client.post("/api/platform/datasets", json=sample()).json()["dataset_id"]
        body = {"dataset_id": dataset, "job_id": "J_TEST", "department": "TEST",
                "rounds": {"first": ["p_test"]}, "expected_revision": 0}
        self.assertEqual(self.client.post("/api/platform/panels", json=body).status_code, 200)
        result = self.client.get("/api/platform/panels/assignments", params={"dataset_id": dataset}).json()
        self.assertEqual(result["assignments"][0]["revision"], 1)
        schedule = self.client.post("/api/platform/schedules", json={"dataset_id": dataset, "kind": "locks"}).json()
        self.assertEqual(self.client.post(f"/api/platform/schedules/{schedule['schedule_id']}/state",
                                         json={"enabled": False, "dataset_id": dataset}).status_code, 200)
        self.assertEqual(self.client.get("/api/platform/schedules").json()[0]["enabled"], 0)

    def test_platform_advisor_endpoint_is_bounded_and_no_write(self):
        self.login()
        job = {"job_id": "J_TEST", "title": "测试岗位"}
        result = {"status": "question", "complete": False, "question": "使用哪个浏览器？",
                  "recommended_answer": "Ego Browser", "rationale": "复用登录态",
                  "llm_calls": 1, "external_hiring_actions": 0}
        with patch("talentpilot.boss.latest_position", return_value=job), patch(
                "talentpilot.platform_advisor.interview", return_value=result) as interview:
            response = self.client.post("/api/platform/connectors/platform-advisor/interview", json={
                "platform": "BOSS直聘", "job_id": "J_TEST", "answers": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["external_hiring_actions"], 0)
        interview.assert_called_once_with("BOSS直聘", job, [])

    def test_boss_search_endpoint_returns_review_only_ranked_candidates(self):
        self.login()
        job = {"job_id": "J_TEST", "title": "AI产品经理"}
        ranked = {"status": "review_required", "candidates": [{"name": "候选人甲", "score": 90}],
                  "searched": 3, "unique": 2, "eligible": 1, "external_actions": 0}
        content = "search:\n  keywords: [AI产品经理]\ncandidate_filter: {}\ntop_n: 10\n"
        with patch("talentpilot.boss.latest_position", return_value=job), patch(
                "talentpilot.boss_sourcing.search_and_rank", return_value=ranked) as search:
            response = self.client.post("/api/platform/connectors/boss/search",
                                        json={"job_id": "J_TEST", "yaml_content": content})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "review_required")
        self.assertEqual(response.json()["external_actions"], 0)
        search.assert_called_once_with(self.app.state.platform.store, "local-admin", job, content)

    def test_removed_inventory_routes_are_not_exposed(self):
        self.login()
        self.assertEqual(self.client.post("/api/platform/connectors/italent/applications", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/platform/connectors/italent/audit", json={}).status_code, 404)

    def test_invalid_recording_not_saved(self):
        self.login()
        result = self.client.post("/api/platform/recordings", content=b"not a webm",
                                  headers={"Content-Type": "video/webm"})
        self.assertEqual(result.status_code, 422)

    def test_batch_candidate_history_endpoint(self):
        self.login()
        live = {"collected_at": "2026-09-22T00:00:00Z", "results": []}
        positions = {"positions": [{"job_id": "J_TEST", "title": "Test", "department": "TEST"}]}
        with patch("talentpilot.italent_browser.match_candidate_history", return_value=live), \
             patch("talentpilot.resume_matching.high_match_provenance"), \
             patch("talentpilot.platform_api.collect_positions_for_upload", return_value=positions):
            result = self.client.post("/api/platform/candidate-history/match",
                                      json={"job_id": "J_TEST", "files": [{"name": "王五.txt",
                                            "content_base64": base64.b64encode("姓名：王五\n13800138000".encode()).decode()}]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["files"], 1)
        self.assertEqual(result.json()["results"][0]["status"], "not_found")
        self.assertEqual(result.json()["results"][0]["decision"], "create_and_advance")
        blocked = self.client.post(f"/api/platform/candidate-history/{result.json()['batch_id']}/execute",
                                   json={"fingerprint": result.json()["fingerprint"]})
        self.assertEqual(blocked.json()["status"], "blocked")
        self.assertEqual(blocked.json()["external_actions"], 0)

    def test_email_preview_and_save_endpoint(self):
        self.login()
        content = "姓名：李雷\n教育经历\n示例大学\n预计毕业年份：2027\n".encode()
        encoded = base64.b64encode(content).decode()
        preview = self.client.post("/api/platform/connectors/email/preview",
            json={"files": [{"name": "简历.txt", "content_base64": encoded}]})
        self.assertEqual(preview.status_code, 200)
        row = preview.json()["results"][0]
        with patch("talentpilot.boss.latest_position", return_value={"job_id": "J_TEST", "title": "产品实习生"}), \
             patch("talentpilot.email_recruiting.settings.email_resumes_dir", self.root / "email-resumes"):
            saved = self.client.post("/api/platform/connectors/email/save", json={
                "job_id": "J_TEST", "provider": "百度邮箱", "intent_confirmed": True,
                "files": [{"name": "简历.txt", "content_base64": encoded,
                           "sha256": row["sha256"], "graduation_year": "2027届",
                           "school": "示例大学", "candidate_name": "李雷"}]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["results"][0]["filename"],
                         "产品实习生+2027届+示例大学+李雷.txt")

    def test_unmatched_resume_cannot_enter_upload_preview(self):
        with patch("talentpilot.platform_api.collect_positions_for_upload") as positions, \
             patch("talentpilot.italent_browser.match_candidate_history") as history:
            result = self.client.post("/api/platform/candidate-history/match", json={
                "job_id": "J1234567890",
                "files": [{"name": "synthetic.txt",
                           "content_base64": base64.b64encode(b"SYNTHETIC ONLY").decode()}]})
        self.assertEqual(result.status_code, 422)
        positions.assert_not_called()
        history.assert_not_called()

    def test_local_matching_api_copies_and_serves_review(self):
        job = {"state": "verified_job_jd", "job_id": "J123", "title": "测试岗位",
               "duties": "负责 Python SQL Excel React Vue 数据分析相关开发实践与工作。",
               "qualifications": "要求具备相关技能和项目经历，所有内容仅用于合成测试。",
               "collected_at": "2026-09-24T00:00:00+00:00"}
        from talentpilot.config import settings
        folder = settings.resume_archive_dir / "实习生" / "测试岗位" / "20260924"
        folder.mkdir(parents=True)
        source = folder / "sample.txt"
        source.write_text(job["duties"] * 5)
        jev = {"score": 100, "high": True, "model": "synthetic-jev", "confidence": 1,
               "required_skills_probability": 1, "hard_requirements_probability": 1,
               "seniority": "matched"}
        with patch("talentpilot.italent_browser.collect_job_jd", return_value=job) as collect, \
             patch("talentpilot.jev_matching.evaluate", return_value=jev):
            response = self.client.post("/api/platform/resume-match/preview", json={
                "job_id": "J123", "source_folder": "实习生/测试岗位"})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["high_count"], 1)
        collect.assert_called_once_with("J123")
        self.assertTrue(source.is_file())
        self.assertEqual(result["external_actions"], 0)
        report = self.client.get(f"/api/platform/resume-match/runs/{result['run_id']}/report")
        self.assertEqual(report.status_code, 200)
        self.assertIn("自动匹配结果", report.text)
        file = self.client.get(f"/api/platform/resume-match/runs/{result['run_id']}/简历/20260924/sample.txt")
        self.assertEqual(file.content, source.read_bytes())
        denied = self.client.get(f"/api/platform/resume-match/runs/{result['run_id']}/岗位JD.json")
        self.assertEqual(denied.status_code, 409)

    def test_email_agent_login_handoff_endpoint(self):
        self.login()
        job = {"job_id": "J_TEST", "title": "产品实习生"}
        with patch("talentpilot.boss.latest_position", return_value=job), \
             patch("talentpilot.email_recruiting.start_collection",
                   return_value={"run_id": "a" * 32, "status": "awaiting_login"}) as start:
            response = self.client.post("/api/platform/connectors/email/collections",
                                        json={"job_id": "J_TEST", "provider": "百度邮箱"})
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with(self.app.state.platform.store, "local-admin", job, "百度邮箱")
        with patch("talentpilot.email_recruiting.resume_collection",
                   return_value={"status": "completed", "count": 1}) as resume:
            response = self.client.post("/api/platform/connectors/email/collections/" + "a" * 32 + "/resume", json={})
        self.assertEqual(response.json()["count"], 1)
        resume.assert_called_once()
        with patch("talentpilot.email_recruiting.cancel_collection",
                   return_value={"status": "cancelled"}) as cancel:
            response = self.client.post("/api/platform/connectors/email/collections/" + "a" * 32 + "/cancel", json={})
        self.assertEqual(response.json()["status"], "cancelled")
        cancel.assert_called_once()
