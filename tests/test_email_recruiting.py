import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talentpilot import email_recruiting
from talentpilot.runtime import Runtime


class EmailRecruitingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root / "platform")
        self.content = "姓名：张三\n教育经历\n示例大学 2023-2027\n预计毕业时间：2027年\n".encode()
        self.file = {"name": "个人简历.txt",
                     "content_base64": base64.b64encode(self.content).decode()}

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_preview_and_save_reviewed_filename(self):
        preview = email_recruiting.preview([self.file])["results"][0]
        self.assertEqual(preview["graduation_year"], "2027届")
        self.assertEqual(preview["school"], "示例大学")
        self.assertEqual(preview["candidate_name"], "张三")
        item = {**self.file, **{key: preview[key] for key in
                ("sha256", "graduation_year", "school", "candidate_name")}}
        with patch.object(email_recruiting.settings, "email_resumes_dir", self.root / "resumes"):
            result = email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "AI产品实习生"}, "百度邮箱", True, [item])
            target = Path(result["results"][0]["path"])
            self.assertEqual(target.name, "AI产品实习生+2027届+示例大学+张三.txt")
            self.assertEqual(target.read_bytes(), self.content)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            duplicate = email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "AI产品实习生"}, "百度邮箱", True, [item])
            self.assertTrue(duplicate["results"][0]["duplicate"])

    def test_requires_intent_and_unchanged_preview(self):
        preview = email_recruiting.preview([self.file])["results"][0]
        item = {**self.file, **{key: preview[key] for key in
                ("sha256", "graduation_year", "school", "candidate_name")}}
        with self.assertRaisesRegex(ValueError, "求职意向"):
            email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "岗位"}, "邮箱", False, [item])
        item["sha256"] = hashlib.sha256(b"changed").hexdigest()
        with self.assertRaisesRegex(ValueError, "发生变化"):
            email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "岗位"}, "邮箱", True, [item])

    def test_missing_metadata_stays_for_manual_review(self):
        raw = base64.b64encode(b"SYNTHETIC resume").decode()
        result = email_recruiting.preview([{"name": "resume.txt", "content_base64": raw}])["results"][0]
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["graduation_year"], "")

    @patch("talentpilot.email_recruiting._ego")
    def test_login_handoff_then_agent_downloads_and_saves(self, ego):
        ego.return_value = {"state": "awaiting_login", "task_id": 42,
                            "handoff_completed": True}
        started = email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "AI产品实习生"}, "百度邮箱")
        self.assertEqual(started["status"], "awaiting_login")

        def collected(payload, timeout):
            if payload["operation"] == "start":
                return {"state": "awaiting_login", "task_id": 42, "handoff_completed": True}
            target = Path(payload["download_dir"]) / "resume.txt"
            target.write_bytes(self.content)
            return {"state": "collected", "files": [str(target)],
                    "reviewed": [{"status": "download_requested"}]}

        ego.side_effect = collected
        with patch.object(email_recruiting.settings, "email_resumes_dir", self.root / "agent-resumes"):
            result = email_recruiting.resume_collection(self.runtime.store, "local-admin", started["run_id"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(Path(result["results"][0]["path"]).is_file())

    @patch("talentpilot.email_recruiting._ego")
    def test_agent_skips_unreliable_downloaded_resume(self, ego):
        ego.return_value = {"state": "awaiting_login", "task_id": 43,
                            "handoff_completed": True}
        started = email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "岗位"}, "QQ邮箱")

        def collected(payload, timeout):
            target = Path(payload["download_dir"]) / "unknown.txt"
            target.write_text("SYNTHETIC resume")
            return {"state": "collected", "files": [str(target)], "reviewed": []}

        ego.side_effect = collected
        result = email_recruiting.resume_collection(self.runtime.store, "local-admin", started["run_id"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["skipped"]), 1)

    @patch("talentpilot.email_recruiting._ego")
    def test_only_one_pending_login_task_per_actor(self, ego):
        ego.return_value = {"state": "awaiting_login", "task_id": 44,
                            "handoff_completed": True}
        email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "岗位"}, "Gmail")
        with self.assertRaisesRegex(ValueError, "已有邮箱登录任务"):
            email_recruiting.start_collection(self.runtime.store, "local-admin",
                {"job_id": "J2", "title": "岗位2"}, "Gmail")
        ego.return_value = {"state": "cancelled", "task_id": 44}
        cancelled = email_recruiting.cancel_collection(self.runtime.store, "local-admin",
                                                        self._latest_run_id())
        self.assertEqual(cancelled["status"], "cancelled")

    def _latest_run_id(self):
        with self.runtime.store.transaction() as cx:
            return cx.execute("SELECT id FROM email_collection_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
