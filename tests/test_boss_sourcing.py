import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from talentpilot import boss_sourcing
from talentpilot.runtime import Runtime
from fastapi.testclient import TestClient


YAML = """
search:
  keywords: [AI产品经理, 大模型产品]
  city: 北京
  experience: 3-5年
  status: [在职-考虑机会, 离职-随时到岗]
candidate_filter:
  keywords:
    values: [AI]
    match: any
  experience: {min_years: 3, max_years: 6}
  activity: {max_inactive_days: 7}
  age: {max: 35}
  salary: {max_k: 40}
  exclude_status: [暂不考虑]
scoring:
  required_keywords: [产品]
  bonus_keywords: [RAG, Agent]
top_n: 2
schedule:
  enabled: true
  hour: 11
  minute: 0
"""


OUTPUT_ONE = """常规搜索结果（关键词：AI产品经理；当前岗位：AI产品经理；城市：北京）
共 2 人

1. 候选人甲｜今日活跃｜28岁 5年 本科
   摘要: AI 产品经理，负责 RAG 产品
   期望: 北京 30-35K 在职-考虑机会
   经历: 某公司 产品经理
   教育: 测试大学 本科
2. 候选人乙｜30日内活跃｜32岁 4年 本科
   摘要: AI 产品经理
   期望: 北京 25-30K 在职-考虑机会
"""

OUTPUT_TWO = """常规搜索结果（关键词：大模型产品；当前岗位：AI产品经理；城市：北京）
共 2 人

1. 候选人丙｜今日活跃｜30岁 4年 本科
   摘要: AI 产品经理，做过 Agent
   期望: 北京 50-60K 在职-考虑机会
2. 候选人甲｜今日活跃｜28岁 5年 本科
   摘要: AI 产品经理，负责 RAG 产品
   期望: 北京 30-35K 在职-考虑机会
   经历: 某公司 产品经理
   教育: 测试大学 本科
"""


class BossSourcingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))
        self.position = {"job_id": "J_TEST", "title": "AI产品经理", "department": "测试"}

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_parse_search_output_extracts_filter_fields(self):
        rows = boss_sourcing.parse_search_output(OUTPUT_ONE, "AI产品经理")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "候选人甲")
        self.assertEqual(rows[0]["age"], 28)
        self.assertEqual(rows[0]["experience_years"], 5)
        self.assertEqual(rows[0]["salary_max_k"], 35)
        self.assertEqual(rows[0]["status"], "在职-考虑机会")

    @patch("talentpilot.boss_sourcing.existing_login_status",
           return_value={"logged_in": True, "controlled_pages": 1, "detail": "已登录"})
    def test_login_status_api_supports_sequential_sourcing(self, status):
        from talentpilot.server import app
        with TestClient(app) as client:
            response = client.get("/api/platform/connectors/boss/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["logged_in"])
        status.assert_called_once_with()

    def test_verified_jd_generates_conservative_scheduled_profile(self):
        jd = {
            "state": "verified_job_jd", "job_id": "J_TEST", "title": "北京-AI产品经理实习生",
            "duties": "负责大模型产品、RAG和Agent产品设计，完成需求分析与数据分析。",
            "qualifications": "本科及以上学历，具备Python项目经验，有2年以上产品经验优先。",
        }
        content, evidence = boss_sourcing.profile_from_jd(jd)
        profile = boss_sourcing.parse_profile(content)
        self.assertTrue(profile["schedule"]["enabled"])
        self.assertEqual(profile["schedule"]["hour"], 11)
        self.assertIn("AI产品经理", profile["keywords"][0])
        self.assertTrue({"RAG", "Agent", "大模型"} & set(profile["required_keywords"]))
        self.assertEqual(profile["filter"].min_experience_years, 2)
        self.assertIsNone(profile["filter"].max_age)
        self.assertIsNone(profile["filter"].max_salary_k)
        self.assertEqual(evidence["source"], "verified_job_jd")
        self.assertIn("年龄", evidence["omitted"])

    def test_configure_all_from_jd_persists_each_job(self):
        positions = [self.position, {"job_id": "J_OTHER", "title": "前端开发", "department": "测试"}]
        def reader(job_id):
            return {"state": "verified_job_jd", "job_id": job_id,
                    "title": next(p["title"] for p in positions if p["job_id"] == job_id),
                    "duties": "负责JavaScript React前端开发与用户体验优化工作。",
                    "qualifications": "本科及以上学历，具备前端项目经验和工程实践。"}
        result = boss_sourcing.configure_all_from_jd(
            self.runtime.store, "local-admin", positions, reader)
        self.assertEqual(result["configured"], 2)
        with self.runtime.store.transaction() as cx:
            rows = cx.execute("SELECT enabled,hour FROM boss_sourcing_schedules").fetchall()
        self.assertEqual([tuple(row) for row in rows], [(1, 11), (1, 11)])

    def test_search_filters_deduplicates_scores_and_persists_encrypted(self):
        outputs = iter([OUTPUT_ONE, OUTPUT_TWO])
        with patch.object(boss_sourcing, "_run_boss", side_effect=lambda _args: next(outputs)) as run:
            result = boss_sourcing.search_and_rank(
                self.runtime.store, "local-admin", self.position, YAML
            )
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["searched"], 4)
        self.assertEqual(result["unique"], 3)
        self.assertEqual(result["eligible"], 1)
        self.assertEqual(result["candidates"][0]["name"], "候选人甲")
        self.assertGreater(result["candidates"][0]["score"], 0)
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(run.call_count, 2)
        self.assertIn("--city", run.call_args_list[0].args[0])
        with self.runtime.store.transaction() as cx:
            row = cx.execute("SELECT payload FROM boss_sourcing_batches").fetchone()
            schedule = cx.execute("SELECT enabled,hour,minute FROM boss_sourcing_schedules").fetchone()
        self.assertNotIn("候选人甲".encode(), row["payload"])
        self.assertEqual(tuple(schedule), (1, 11, 0))

    def test_new_job_rescans_encrypted_historical_candidate_pool(self):
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, OUTPUT_TWO]):
            boss_sourcing.search_and_rank(self.runtime.store, "local-admin", self.position, YAML)
        next_job = {"job_id": "J_NEW", "title": "新AI岗位", "department": "测试"}
        empty = "常规搜索结果（关键词：AI产品经理）\n共 0 人\n"
        with patch.object(boss_sourcing, "_run_boss", side_effect=[empty, empty]):
            result = boss_sourcing.search_and_rank(self.runtime.store, "local-admin", next_job, YAML)
        self.assertEqual(result["historical_scanned"], 3)
        self.assertEqual(result["eligible"], 1)
        self.assertEqual(result["candidates"][0]["name"], "候选人甲")

    def test_daily_schedule_runs_once_after_configured_time(self):
        boss_sourcing.configure_schedule(self.runtime.store, "local-admin", self.position, YAML)
        now = datetime(2026, 10, 7, 11, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        with patch.object(boss_sourcing, "search_and_rank", return_value={"batch_id": "batch"}) as search:
            self.assertEqual(boss_sourcing.dispatch_due(self.runtime.store, now), 1)
            self.assertEqual(boss_sourcing.dispatch_due(self.runtime.store, now), 0)
        search.assert_called_once_with(self.runtime.store, "local-admin", self.position, YAML, configure=False)
        with self.runtime.store.transaction() as cx:
            row = cx.execute("SELECT last_run_date,last_status FROM boss_sourcing_schedules").fetchone()
        self.assertEqual(tuple(row), ("2026-10-07", "completed"))

    def test_invalid_or_missing_cli_fails_closed(self):
        with patch.object(boss_sourcing, "_run_boss", side_effect=boss_sourcing.BossCliUnavailable("未安装")):
            with self.assertRaisesRegex(boss_sourcing.BossCliUnavailable, "未安装"):
                boss_sourcing.search_and_rank(self.runtime.store, "local-admin", self.position, YAML)
        with self.runtime.store.transaction() as cx:
            self.assertEqual(cx.execute("SELECT count(*) FROM boss_sourcing_batches").fetchone()[0], 0)

    def test_login_opens_visible_cli_without_waiting_for_completion(self):
        process = type("Process", (), {"pid": 4321})()
        with patch.object(boss_sourcing.shutil, "which", return_value="/usr/local/bin/boss"), \
             patch.object(boss_sourcing.subprocess, "Popen", return_value=process) as popen:
            result = boss_sourcing.start_login()
        self.assertEqual(result, {"status": "login_window_opened", "pid": 4321,
                                  "external_actions": 0})
        self.assertEqual(popen.call_args.args[0], ["/usr/local/bin/boss", "login"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_boss_executable_falls_back_to_nvm(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / ".nvm/versions/node/v24.20.0/bin/boss"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n")
            with patch.object(boss_sourcing.shutil, "which", return_value=None), \
                 patch.object(boss_sourcing.Path, "home", return_value=Path(directory)):
                self.assertEqual(boss_sourcing._boss_executable(), str(executable))

    def test_existing_login_checks_controlled_browser_first(self):
        response = type("Response", (), {"json": lambda _self: [
            {"type": "page", "url": "https://www.zhipin.com/web/chat/index"}]})()
        with patch("httpx.get", return_value=response):
            result = boss_sourcing.existing_login_status()
        self.assertTrue(result["logged_in"])
        self.assertEqual(result["controlled_pages"], 1)

    def test_greet_preview_rechecks_current_search_and_runs_dry_run(self):
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, OUTPUT_TWO]):
            batch = boss_sourcing.search_and_rank(self.runtime.store, "local-admin", self.position, YAML)
        target = batch["candidates"][0]
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE,
                "dry-run：已点取消，未发出招呼"]) as run:
            preview = boss_sourcing.prepare_greetings(
                self.runtime.store, "local-admin", batch["batch_id"],
                [target["candidate_id"]], batch["fingerprint"]
            )
        self.assertEqual(preview["status"], "awaiting_confirmation")
        self.assertEqual(preview["candidates"][0]["name"], "候选人甲")
        self.assertEqual(preview["external_actions"], 0)
        self.assertEqual(run.call_args_list[-1].args[0][-1], "--dry-run")

    def test_greet_confirmation_rechecks_and_sends_once_without_retry(self):
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, OUTPUT_TWO]):
            batch = boss_sourcing.search_and_rank(self.runtime.store, "local-admin", self.position, YAML)
        target = batch["candidates"][0]
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, "dry-run ok"]):
            preview = boss_sourcing.prepare_greetings(
                self.runtime.store, "local-admin", batch["batch_id"],
                [target["candidate_id"]], batch["fingerprint"]
            )
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, "dry-run ok",
                "已向候选人甲打招呼"]) as run:
            result = boss_sourcing.confirm_greetings(
                self.runtime.store, "local-admin", preview["preview_id"],
                preview["fingerprint"], True
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["external_actions"], 1)
        self.assertNotIn("--dry-run", run.call_args_list[-1].args[0])
        with self.assertRaisesRegex(ValueError, "已执行"):
            boss_sourcing.confirm_greetings(self.runtime.store, "local-admin",
                                             preview["preview_id"], preview["fingerprint"], True)

    def test_greet_stops_when_candidate_identity_or_confirmation_changes(self):
        with patch.object(boss_sourcing, "_run_boss", side_effect=[OUTPUT_ONE, OUTPUT_TWO]):
            batch = boss_sourcing.search_and_rank(self.runtime.store, "local-admin", self.position, YAML)
        target = batch["candidates"][0]
        changed = OUTPUT_ONE.replace("候选人甲｜今日活跃｜28岁 5年",
                                     "另一候选人｜今日活跃｜29岁 6年")
        with patch.object(boss_sourcing, "_run_boss", return_value=changed):
            with self.assertRaisesRegex(ValueError, "候选人列表已变化"):
                boss_sourcing.prepare_greetings(self.runtime.store, "local-admin",
                    batch["batch_id"], [target["candidate_id"]], batch["fingerprint"])


if __name__ == "__main__":
    unittest.main()
