import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from talentpilot.resume_registry import evaluate_reapplication, register
from talentpilot.candidate_history import extract_identity, match_directory
from talentpilot.runtime import Runtime
from test_platform import sample


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = Runtime(self.root / "db")
        self.capture = sample()
        self.path = self.root / "resume.txt"
        self.path.write_text("SYNTHETIC resume private@example.test")

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def add(self, candidate="c_test"):
        return register(self.runtime.store, "local-admin", self.capture, candidate, self.path)

    def test_concurrent_dedup_and_encryption(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: self.add(), range(8)))
        self.assertEqual(sum(r["status"] == "registered" for r in rows), 1)
        self.assertEqual(len({r["resume_id"] for r in rows}), 1)
        with self.runtime.store.transaction() as cx:
            raw = cx.execute("SELECT payload FROM resume_versions").fetchone()[0]
        self.assertNotIn(b"private@example.test", raw)
        self.path.write_text("SYNTHETIC updated resume")
        self.assertEqual(self.add()["version"], 2)

    def test_same_text_different_file_and_identity_conflict(self):
        self.add()
        self.path.write_text("  SYNTHETIC   resume private@example.test\n")
        self.assertEqual(self.add()["status"], "duplicate")
        self.capture["applications"].append({**self.capture["applications"][0], "candidate_id": "another"})
        self.assertEqual(self.add("another")["status"], "identity_review_required")

    def test_unknown_candidate_and_empty_text(self):
        with self.assertRaises(ValueError):
            self.add("not-in-dataset")
        self.path.write_text(" ")
        with self.assertRaises(ValueError):
            self.add()

    def test_unknown_current_state_never_allows_reapply(self):
        result = evaluate_reapplication(self.capture, "c_test", "J_TEST")
        self.assertEqual(result["status"], "blocked_or_unknown")
        self.assertFalse(result["can_submit"])

    def test_cooldown_periods_and_three_rejections(self):
        now = datetime.now(timezone.utc)
        app = self.capture["applications"][0]
        app.update(status="rejected_unlocked", rejected_at=(now-timedelta(days=10)).isoformat(), unlocked=True)
        for policy, days in (("standard", 30), ("technical", 60), ("senior", 90)):
            result = evaluate_reapplication(self.capture, "c_test", "J_TEST", policy, now)
            self.assertEqual(result["cooldown_days"], days)
            self.assertFalse(result["can_submit"])
        second = copy.deepcopy(app)
        second["application_id"] = "second"
        self.capture["applications"].append(second)
        self.assertEqual(evaluate_reapplication(self.capture, "c_test", "J_TEST")["cooldown_days"], 180)
        third = copy.deepcopy(app)
        third["application_id"] = "third"
        self.capture["applications"].append(third)
        self.assertEqual(evaluate_reapplication(self.capture, "c_test", "J_TEST")["status"],
                         "special_approval_required")

    def test_batch_history_match_uses_contact_and_preserves_multiple_applications(self):
        directory = self.root / "batch"
        directory.mkdir()
        (directory / "张三-简历.txt").write_text("姓名：张三\n手机 13800138000\n邮箱 test@example.com")
        identity = extract_identity(directory / "张三-简历.txt")
        self.assertEqual(identity["name"], "张三")
        self.assertEqual(identity["mobile"], "13800138000")

        def search(rows):
            self.assertNotIn("text", rows[0])
            return {"collected_at": "2026-09-22T00:00:00Z", "results": [{
                "file_id": rows[0]["file_id"], "match_basis": "email", "candidate_name": "张三",
                "applications": [
                    {"candidate_id": "c1", "job_title": "岗位A", "department": "部门A",
                     "applied_at": "2025-01-02", "outcome": "rejected", "rejected_stage": "一面"},
                    {"candidate_id": "c1", "job_title": "岗位B", "department": "部门B",
                     "applied_at": "2026-03-04", "outcome": "hired", "hired_at": "2026-04-01"},
                ]}]}

        result = match_directory(directory, search, "J_TARGET")
        self.assertEqual(result["history_found"], 1)
        self.assertEqual(len(result["results"][0]["applications"]), 2)
        self.assertEqual(result["results"][0]["decision"], "update_and_recommend")
        self.assertEqual(result["external_actions"], 0)

    def test_name_only_multiple_people_requires_manual_review(self):
        directory = self.root / "ambiguous"
        directory.mkdir()
        (directory / "李雷简历.txt").write_text("李雷\n项目经历")
        result = match_directory(directory, lambda rows: {"results": [{
            "file_id": rows[0]["file_id"], "match_basis": "name", "name_conflict": False,
            "applications": [{"candidate_id": "a"}, {"candidate_id": "b"}]}]})
        self.assertEqual(result["results"][0]["status"], "manual_review")
        self.assertEqual(result["results"][0]["decision"], "review_identity")

    def test_same_job_and_lock_are_skipped(self):
        directory = self.root / "skip"
        directory.mkdir()
        (directory / "赵六.txt").write_text("姓名：赵六\n13800138000")
        file_id = extract_identity(directory / "赵六.txt")["file_id"]
        same = match_directory(directory, lambda _: {"results": [{"file_id": file_id, "match_basis": "mobile",
            "applications": [{"candidate_id": "c", "job_id": "J_TARGET", "locked": True}]}]}, "J_TARGET")
        self.assertEqual(same["results"][0]["decision"], "skip_same_job")
        locked = match_directory(directory, lambda _: {"results": [{"file_id": file_id, "match_basis": "mobile",
            "applications": [{"candidate_id": "c", "job_id": "J_OTHER", "locked": True}]}]}, "J_TARGET")
        self.assertEqual(locked["results"][0]["decision"], "skip_locked")


if __name__ == "__main__":
    unittest.main()
