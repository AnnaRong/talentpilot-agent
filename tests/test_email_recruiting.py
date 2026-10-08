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
                ("sha256", "candidate_name")}}
        with patch.object(email_recruiting.settings, "email_resumes_dir", self.root / "resumes"):
            result = email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "AI产品实习生"}, "百度邮箱", True, [item])
            target = Path(result["results"][0]["path"])
            self.assertEqual(target.name, "AI产品实习生+张三.txt")
            self.assertEqual(target.read_bytes(), self.content)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            duplicate = email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "AI产品实习生"}, "百度邮箱", True, [item])
            self.assertTrue(duplicate["results"][0]["duplicate"])

    def test_requires_intent_and_unchanged_preview(self):
        preview = email_recruiting.preview([self.file])["results"][0]
        item = {**self.file, **{key: preview[key] for key in
                ("sha256", "candidate_name")}}
        with self.assertRaisesRegex(ValueError, "求职意向"):
            email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "岗位"}, "邮箱", False, [item])
        item["sha256"] = hashlib.sha256(b"changed").hexdigest()
        with self.assertRaisesRegex(ValueError, "发生变化"):
            email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J1", "title": "岗位"}, "邮箱", True, [item])

    def test_missing_name_stays_for_manual_review(self):
        raw = base64.b64encode(b"SYNTHETIC resume").decode()
        result = email_recruiting.preview([{"name": "resume.txt", "content_base64": raw}])["results"][0]
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["graduation_year"], "")

    @patch("talentpilot.email_recruiting.subprocess.run")
    @patch("talentpilot.email_recruiting.shutil.which", return_value="ego-browser")
    @patch("talentpilot.email_recruiting.Path.read_text", return_value="")
    def test_login_handoff_brings_ego_lite_to_front(self, _read, _which, run):
        run.side_effect = [
            type("Result", (), {"stdout": 'TALENTPILOT_EMAIL_RESULT:{"state":"awaiting_login"}',
                                  "stderr": ""})(),
            type("Result", (), {})(),
        ]
        with patch.object(email_recruiting.sys, "platform", "darwin"):
            result = email_recruiting._ego({"operation": "start"}, 10)
        self.assertEqual(result["state"], "awaiting_login")
        self.assertEqual(run.call_args_list[1].args[0], ["open", "-a", "ego lite"])

    def test_job_and_name_are_enough_for_filename(self):
        raw = base64.b64encode("姓名：李四\n个人简历".encode()).decode()
        item = {"name": "简历.pdf", "content_base64": raw,
                "sha256": hashlib.sha256(base64.b64decode(raw)).hexdigest(),
                "candidate_name": "李四"}
        with patch.object(email_recruiting.settings, "email_resumes_dir", self.root / "pdf-resumes"):
            result = email_recruiting.save(self.runtime.store, "local-admin",
                {"job_id": "J2", "title": "产品经理"}, "QQ邮箱", True, [item])
        self.assertEqual(result["results"][0]["filename"], "产品经理+李四.pdf")

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
        first = email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "岗位"}, "Gmail")
        ego.return_value = {"state": "awaiting_login", "task_id": 44,
                            "handoff_completed": True}
        resumed = email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "岗位"}, "Gmail")
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(ego.call_args.args[0]["operation"], "show")
        with self.assertRaisesRegex(ValueError, "已有邮箱登录任务"):
            email_recruiting.start_collection(self.runtime.store, "local-admin",
                {"job_id": "J2", "title": "岗位2"}, "Gmail")
        ego.return_value = {"state": "cancelled", "task_id": 44}
        cancelled = email_recruiting.cancel_collection(self.runtime.store, "local-admin",
                                                        self._latest_run_id())
        self.assertEqual(cancelled["status"], "cancelled")

    @patch("talentpilot.email_recruiting._ego")
    def test_login_check_gates_resume_scan(self, ego):
        ego.return_value = {"state": "awaiting_login", "task_id": 45,
                            "handoff_completed": True}
        started = email_recruiting.start_collection(self.runtime.store, "local-admin",
            {"job_id": "J1", "title": "岗位"}, "QQ邮箱")
        ego.return_value = {"state": "login_checked", "task_id": 45,
                            "logged_in": False, "handoff_completed": True}
        waiting = email_recruiting.check_collection_login(
            self.runtime.store, "local-admin", started["run_id"])
        self.assertEqual(waiting["status"], "awaiting_login")
        ego.return_value = {"state": "login_checked", "task_id": 45,
                            "logged_in": True, "handoff_completed": True}
        ready = email_recruiting.check_collection_login(
            self.runtime.store, "local-admin", started["run_id"])
        self.assertEqual(ready["status"], "logged_in")

    def test_qq_inbox_url_is_not_treated_as_login_page(self):
        script = email_recruiting.AUTOMATION.read_text()
        self.assertIn("qqInboxUrl", script)
        self.assertIn("https://wx.mail.qq.com/home/index?", script)
        self.assertIn("sid=", script)

    @patch("talentpilot.email_recruiting.subprocess.run")
    @patch("talentpilot.email_recruiting.shutil.which", return_value="ego-browser")
    def test_probe_reuses_verified_qq_mailbox(self, _which, run):
        run.return_value = type("Result", (), {"returncode": 0,
            "stdout": 'TALENTPILOT_MAIL_PROBE:{"logged_in":true,"url":"https://wx.mail.qq.com/home/index?sid=test#/list/1"}',
            "stderr": ""})()
        result = email_recruiting.probe_logged_in_mailbox()
        self.assertTrue(result["logged_in"])
        self.assertIn("已核验", result["detail"])

    def test_external_login_confirmation_keeps_no_session_secret(self):
        result = email_recruiting.confirm_external_login(
            self.runtime.store, "local-admin", "QQ邮箱", "https://wx.mail.qq.com/home/index")
        self.assertFalse(result["credentials_stored"])
        saved = email_recruiting.confirmed_login(self.runtime.store, "QQ邮箱")
        self.assertEqual(saved["state"], "user_confirmed_visible_login")
        self.assertNotIn("url", saved)

    def _latest_run_id(self):
        with self.runtime.store.transaction() as cx:
            return cx.execute("SELECT id FROM email_collection_runs ORDER BY created_at DESC LIMIT 1").fetchone()[0]


if __name__ == "__main__":
    unittest.main()
