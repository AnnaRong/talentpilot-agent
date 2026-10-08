import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from talentpilot import autonomous
from talentpilot.runtime import Runtime


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_daily_schedule_is_once_and_defaults_to_eleven(self):
        store = self.runtime.store
        before = datetime(2026, 10, 7, 10, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
        after = datetime(2026, 10, 7, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(autonomous.dispatch_due(store, before), 0)
        self.assertEqual(autonomous.dispatch_due(store, after), 1)
        self.assertEqual(autonomous.dispatch_due(store, after), 0)
        overview = autonomous.overview(store, "local-admin")
        self.assertTrue(overview["enabled"])
        self.assertEqual(overview["schedule"], "11:00")
        self.assertEqual(len(overview["runs"]), 1)

    def test_first_step_submits_durable_live_collection(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        with patch("talentpilot.live_runs.submit", return_value="live-test") as submit:
            self.assertTrue(autonomous.tick(self.runtime))
        submit.assert_called_once()
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["status"], "waiting_for_inventory")
        self.assertEqual(run["checkpoint"]["live_run_id"], "live-test")

    def test_failed_inventory_is_recorded_and_pipeline_continues(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='waiting_for_inventory',checkpoint=? WHERE id=?",
                       (self.runtime.store.seal({"live_run_id": "live-test"}), run_id))
            cx.execute("""INSERT INTO live_runs
              (id,request_key,actor,status,checkpoint,error,created_at,updated_at)
              VALUES ('live-test','key','local-admin','waiting_for_user',?,'login',?,?)""",
                       (self.runtime.store.seal({}), "2026-10-08T00:00:00+00:00", "2026-10-08T00:00:00+00:00"))
        self.assertTrue(autonomous.tick(self.runtime))
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["step"], 1)
        self.assertEqual(run["steps"][0]["status"], "needs_attention")
        self.assertEqual(len(run["steps"]), 8)

    def test_remaining_steps_match_numbered_console_workflow(self):
        self.assertEqual(autonomous.STEPS, (
            "北森只读", "人才库筛选与推荐", "论文雷达 · 高端被动人才", "文件整理与清洗",
            "批量核验与上传", "候选人锁提醒", "招聘漏斗", "招聘台账",
        ))
        self.assertEqual(autonomous.SOURCING_STEPS, (
            "根据 JD 配置条件", "BOSS直聘", "公司系统与人才库", "海外", "QQ邮箱",
        ))

    def test_sourcing_monitor_exposes_all_existing_sources(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        checkpoint = {"step_statuses": {"1": "completed_with_gaps"}, "details": {"1": {
            "substeps": {"0": {"status": "completed", "detail": "configured"},
                         "4": {"status": "ready", "detail": "Chrome"}}}}}
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='queued',step=2,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal(checkpoint), run_id))
        step = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]["steps"][1]
        self.assertEqual([item["name"] for item in step["substeps"]], list(autonomous.SOURCING_STEPS))
        self.assertEqual(step["substeps"][-1]["status"], "ready")

    def test_rerun_step_two_copies_context_and_queues_that_step(self):
        original = autonomous.run_now(self.runtime.store, "local-admin")
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='completed',step=8,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal({"positions": [{"job_id": "J1"}], "gaps": ["第2步旧缺口"]}), original))
        run_id = autonomous.rerun_step(self.runtime.store, "local-admin", 1)
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["step"], 1)
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["checkpoint"]["positions"], [{"job_id": "J1"}])
        self.assertEqual(run["checkpoint"]["gaps"], [])


if __name__ == "__main__":
    unittest.main()
