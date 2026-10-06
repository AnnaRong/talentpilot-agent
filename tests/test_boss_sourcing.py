import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from talentpilot import boss_sourcing
from talentpilot.runtime import Runtime


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


if __name__ == "__main__":
    unittest.main()
