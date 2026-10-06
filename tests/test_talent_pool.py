import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talentpilot.runtime import Runtime
from talentpilot import talent_pool


class TalentPoolWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))
        self.job = {"job_id": "J_TEST", "title": "测试岗位", "department": "测试部门"}

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_search_creates_expiring_preview_without_resume_files(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲",
                                "school": "测试大学", "major": "计算机"}],
                "criteria": {}, "query_totals": {"计算机": 1}, "source": "skill"}
        with patch.object(talent_pool, "_run_skill", return_value=live):
            result = talent_pool.search(self.runtime.store, "local-admin", self.job,
                                        {"majors": ["计算机"], "limit": 20})
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(result["target_stage"], "简历复筛/进行中")
        self.assertEqual(result["candidates"][0]["applicant_id"], "candidate-1")
        self.assertEqual(list(Path(self.temp.name).rglob("*.pdf")), [])

    def test_confirmed_recommendation_fails_closed_without_live_write(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲"}],
                "criteria": {}, "query_totals": {}, "source": "skill"}
        with patch.object(talent_pool, "_run_skill", return_value=live):
            preview = talent_pool.search(self.runtime.store, "local-admin", self.job, {})
        result = talent_pool.recommend(self.runtime.store, "local-admin", preview["batch_id"],
                                       ["candidate-1"], preview["fingerprint"], True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["external_actions"], 0)
        self.assertFalse(result["read_back"])
        with self.assertRaises(ValueError):
            talent_pool.recommend(self.runtime.store, "local-admin", preview["batch_id"],
                                  ["candidate-1"], preview["fingerprint"], True)

    def test_recommendation_requires_current_confirmation_and_preview_membership(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲"}],
                "criteria": {}, "query_totals": {}, "source": "skill"}
        with patch.object(talent_pool, "_run_skill", return_value=live):
            preview = talent_pool.search(self.runtime.store, "local-admin", self.job, {})
        with self.assertRaisesRegex(ValueError, "来自当前预览"):
            talent_pool.recommend(self.runtime.store, "local-admin", preview["batch_id"],
                                  ["candidate-2"], preview["fingerprint"], True)
        with self.assertRaisesRegex(ValueError, "明确确认"):
            talent_pool.recommend(self.runtime.store, "local-admin", preview["batch_id"],
                                  ["candidate-1"], preview["fingerprint"], False)


if __name__ == "__main__":
    unittest.main()
