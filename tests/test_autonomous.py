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

    def test_pause_and_resume_keep_current_progress(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET step=3,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal({"positions": [{"job_id": "J1"}],
                                                 "source_cycle_completed": True}), run_id))
        paused = autonomous.control(self.runtime.store, "local-admin", "pause")
        self.assertEqual(paused["status"], "paused")
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual((run["status"], run["step"]), ("paused", 3))
        resumed = autonomous.control(self.runtime.store, "local-admin", "resume")
        self.assertEqual(resumed["status"], "queued")
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual((run["status"], run["step"]), ("queued", 3))

    def test_resume_returns_to_unfinished_source_cycle(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='paused',step=3,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal({"positions": [{"job_id": "J1"}]}), run_id))
        autonomous.control(self.runtime.store, "local-admin", "resume")
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual((run["status"], run["step"]), ("queued", 1))

    def test_start_retries_failed_live_collection_instead_of_new_empty_run(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        checkpoint = {"live_run_id": "live-test", "step_statuses": {"0": "needs_attention"},
                      "details": {"0": {"error": "pager changed"}}, "gaps": ["第1步未完成"]}
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='completed_with_gaps',step=8,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal(checkpoint), run_id))
            cx.execute("""INSERT INTO live_runs
              (id,request_key,actor,status,step,checkpoint,error,attempts,created_at,updated_at)
              VALUES ('live-test','retry-key','local-admin','waiting_for_user',1,?,'pager changed',2,?,?)""",
                       (self.runtime.store.seal({"positions": {"at": 1, "value": {"positions": []}}}),
                        "2026-10-09T00:00:00+00:00", "2026-10-09T00:00:00+00:00"))
        self.assertEqual(autonomous.run_now(self.runtime.store, "local-admin"), run_id)
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual((run["status"], run["step"]), ("waiting_for_inventory", 0))
        with self.runtime.store.transaction() as cx:
            live = cx.execute("SELECT status,error FROM live_runs WHERE id='live-test'").fetchone()
        self.assertEqual((live["status"], live["error"]), ("queued", None))

    def test_pause_also_stops_linked_live_collection(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        with patch("talentpilot.live_runs.submit", return_value="live-test"):
            autonomous.tick(self.runtime)
        with self.runtime.store.transaction() as cx:
            cx.execute("""INSERT INTO live_runs
              (id,request_key,actor,status,checkpoint,created_at,updated_at)
              VALUES ('live-test','pause-key','local-admin','queued',?,?,?)""",
                       (self.runtime.store.seal({}), "2026-10-09T00:00:00+00:00", "2026-10-09T00:00:00+00:00"))
        autonomous.control(self.runtime.store, "local-admin", "pause")
        with self.runtime.store.transaction() as cx:
            live = cx.execute("SELECT status FROM live_runs WHERE id='live-test'").fetchone()
        self.assertEqual(live["status"], "paused")
        autonomous.control(self.runtime.store, "local-admin", "resume")
        with self.runtime.store.transaction() as cx:
            live = cx.execute("SELECT status FROM live_runs WHERE id='live-test'").fetchone()
        self.assertEqual(live["status"], "queued")

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

    def test_position_inventory_survives_later_live_collection_failure(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        positions = [{"job_id": "J1", "title": "AI产品经理"}]
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='waiting_for_inventory',checkpoint=? WHERE id=?",
                       (self.runtime.store.seal({"live_run_id": "live-test"}), run_id))
            live_checkpoint = {"positions": {"at": 1, "value": {"positions": positions}}}
            cx.execute("""INSERT INTO live_runs
              (id,request_key,actor,status,step,checkpoint,error,created_at,updated_at)
              VALUES ('live-test','key','local-admin','waiting_for_user',1,?,'applications timed out',?,?)""",
                       (self.runtime.store.seal(live_checkpoint),
                        "2026-10-09T00:00:00+00:00", "2026-10-09T00:00:00+00:00"))
        self.assertTrue(autonomous.tick(self.runtime))
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["checkpoint"]["positions"], positions)
        self.assertEqual(run["steps"][0]["status"], "completed_with_gaps")
        self.assertTrue(run["checkpoint"]["details"]["0"]["positions_available"])
        autonomous.complete_source_cycle(self.runtime.store, "local-admin", run_id,
                                         {"jobs": 1, "completed_jobs": 1})
        with patch("talentpilot.italent_browser.collect_job_jd", side_effect=ValueError("jd unavailable")), \
             patch("talentpilot.email_recruiting.probe_logged_in_mailbox",
                   return_value={"logged_in": False, "detail": "not logged in"}):
            self.assertTrue(autonomous.tick(self.runtime))
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["checkpoint"]["details"]["1"]["positions"], 1)

    def test_legacy_english_timeout_is_translated_for_console(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        checkpoint = {"positions": [{"job_id": "J1"}], "details": {"0": {
            "positions_available": True,
            "error": "Source checkpoints older than 30 minutes. Resume to restart collection.",
        }}}
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='completed_with_gaps',step=8,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal(checkpoint), run_id))
        detail = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]["checkpoint"]["details"]["0"]
        self.assertEqual(detail["error"], "申请或面试清单读取耗时过长；岗位清单已保留。")

    def test_remaining_steps_match_numbered_console_workflow(self):
        self.assertEqual(autonomous.STEPS, (
            "北森只读", "人才库筛选与推荐", "论文雷达 · 高端被动人才", "文件整理与清洗",
            "批量核验与上传", "候选人锁提醒", "招聘漏斗", "招聘台账",
        ))
        self.assertEqual(autonomous.SOURCING_STEPS, (
            "根据 JD 配置条件", "BOSS直聘", "公司系统与人才库", "海外", "QQ邮箱",
        ))

    def test_step_two_waits_for_all_position_source_cycle(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        checkpoint = {"positions": [{"job_id": "J1", "title": "岗位1"}]}
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='queued',step=1,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal(checkpoint), run_id))
        self.assertFalse(autonomous.tick(self.runtime))
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["step"], 1)
        autonomous.complete_source_cycle(self.runtime.store, "local-admin", run_id,
                                         {"jobs": 1, "completed_jobs": 1})
        with patch("talentpilot.italent_browser.collect_job_jd", side_effect=ValueError("unavailable")), \
             patch("talentpilot.email_recruiting.probe_logged_in_mailbox",
                   return_value={"logged_in": False, "detail": "not logged in"}):
            self.assertTrue(autonomous.tick(self.runtime))
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual(run["step"], 2)

    def test_restart_source_cycle_moves_existing_run_back_to_step_two(self):
        run_id = autonomous.run_now(self.runtime.store, "local-admin")
        checkpoint = {"positions": [{"job_id": "J1"}], "source_cycle_completed": True,
                      "step_statuses": {"1": "completed"}, "details": {"1": {"positions": 1}}}
        with self.runtime.store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='paused',step=3,checkpoint=? WHERE id=?",
                       (self.runtime.store.seal(checkpoint), run_id))
        self.assertEqual(autonomous.restart_source_cycle(self.runtime.store, "local-admin"), run_id)
        run = autonomous.overview(self.runtime.store, "local-admin")["runs"][0]
        self.assertEqual((run["status"], run["step"]), ("queued", 1))
        self.assertNotIn("source_cycle_completed", run["checkpoint"])

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
