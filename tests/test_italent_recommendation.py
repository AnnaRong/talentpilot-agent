import json
import subprocess
import unittest
from unittest.mock import patch

from talentpilot import italent_recommendation as recommendation


class ITalentRecommendationAdapterTests(unittest.TestCase):
    def test_preflight_accepts_only_exact_ready_result(self):
        output = {
            "state": "ready_for_confirmation",
            "target": {"job_id": "J1", "title": "测试岗位",
                       "department": "测试部门", "stage": "简历复筛/进行中"},
            "candidates": [{"applicant_id": "A1", "name": "甲",
                            "status": "eligible", "existing_jobs": []}],
        }
        completed = subprocess.CompletedProcess([], 0,
            "TALENTPILOT_RESULT:" + json.dumps(output, ensure_ascii=False) + "\n", "")
        with patch.object(recommendation.shutil, "which", return_value="/usr/bin/ego-browser"), \
             patch.object(recommendation.subprocess, "run", return_value=completed):
            result = recommendation.preflight(
                {"job_id": "J1", "title": "测试岗位", "department": "测试部门"},
                [{"applicant_id": "A1", "name": "甲"}], "简历复筛/进行中"
            )
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(result["candidates"][0]["status"], "eligible")

    def test_preflight_blocks_duplicate_or_unverified_target(self):
        duplicate = {"state": "blocked", "reason": "候选人已在目标岗位",
                     "target": {}, "candidates": []}
        completed = subprocess.CompletedProcess([], 0,
            "TALENTPILOT_RESULT:" + json.dumps(duplicate, ensure_ascii=False) + "\n", "")
        with patch.object(recommendation.shutil, "which", return_value="ego-browser"), \
             patch.object(recommendation.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(recommendation.ITalentRecommendationUnavailable, "已在目标岗位"):
                recommendation.preflight(
                    {"job_id": "J1", "title": "测试岗位", "department": "测试部门"},
                    [{"applicant_id": "A1", "name": "甲"}], "简历复筛/进行中"
                )

    def test_write_switch_defaults_off(self):
        with patch.dict(recommendation.os.environ, {}, clear=True):
            self.assertFalse(recommendation.write_enabled())


if __name__ == "__main__":
    unittest.main()
