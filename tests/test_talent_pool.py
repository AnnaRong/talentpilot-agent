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

    def test_preflight_binds_current_target_and_candidate_state(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲"}],
                "criteria": {}, "query_totals": {}, "source": "skill"}
        with patch.object(talent_pool, "_run_skill", return_value=live):
            preview = talent_pool.search(self.runtime.store, "local-admin", self.job, {})
        checked = {
            "status": "ready_for_confirmation",
            "target": {"job_id": "J_TEST", "title": "测试岗位",
                       "department": "测试部门", "stage": "简历复筛/进行中"},
            "candidates": [{"applicant_id": "candidate-1", "name": "候选人甲",
                            "status": "eligible", "existing_jobs": []}],
            "checked_at": "2026-10-07T03:00:00Z",
        }
        with patch("talentpilot.italent_recommendation.preflight", return_value=checked):
            result = talent_pool.prepare_recommendation(
                self.runtime.store, "local-admin", preview["batch_id"],
                ["candidate-1"], preview["fingerprint"]
            )
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(len(result["fingerprint"]), 64)
        with self.runtime.store.transaction() as cx:
            row = cx.execute("SELECT payload FROM talent_pool_recommendations").fetchone()
        self.assertNotIn(b"candidate-1", row["payload"])

    def test_preflight_requires_current_preview_membership(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲"}],
                "criteria": {}, "query_totals": {}, "source": "skill"}
        with patch.object(talent_pool, "_run_skill", return_value=live):
            preview = talent_pool.search(self.runtime.store, "local-admin", self.job, {})
        with self.assertRaisesRegex(ValueError, "来自当前预览"):
            talent_pool.prepare_recommendation(
                self.runtime.store, "local-admin", preview["batch_id"],
                ["candidate-2"], preview["fingerprint"]
            )

    def test_execute_is_disabled_by_default_and_consumes_nothing(self):
        recommendation = self._recommendation_preview()
        with patch("talentpilot.italent_recommendation.write_enabled", return_value=False):
            result = talent_pool.execute_recommendation(
                self.runtime.store, "local-admin", recommendation["recommendation_id"],
                recommendation["fingerprint"], True
            )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["external_actions"], 0)
        with self.runtime.store.transaction() as cx:
            row = cx.execute("SELECT consumed FROM talent_pool_recommendations").fetchone()
        self.assertEqual(row["consumed"], 0)

    def test_execute_requires_confirmation_and_verified_read_back(self):
        recommendation = self._recommendation_preview()
        with self.assertRaisesRegex(ValueError, "明确确认"):
            talent_pool.execute_recommendation(
                self.runtime.store, "local-admin", recommendation["recommendation_id"],
                recommendation["fingerprint"], False
            )
        executed = {"status": "completed", "external_actions": 1,
                    "results": [{"applicant_id": "candidate-1",
                                 "status": "recommended_verified", "read_back": True}]}
        with patch("talentpilot.italent_recommendation.write_enabled", return_value=True), \
             patch("talentpilot.italent_recommendation.execute", return_value=executed):
            result = talent_pool.execute_recommendation(
                self.runtime.store, "local-admin", recommendation["recommendation_id"],
                recommendation["fingerprint"], True
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["external_actions"], 1)
        with self.assertRaisesRegex(ValueError, "已执行"):
            talent_pool.execute_recommendation(
                self.runtime.store, "local-admin", recommendation["recommendation_id"],
                recommendation["fingerprint"], True
            )

    def _recommendation_preview(self):
        live = {"candidates": [{"applicant_id": "candidate-1", "name": "候选人甲"}],
                "criteria": {}, "query_totals": {}, "source": "skill"}
        checked = {
            "status": "ready_for_confirmation",
            "target": {"job_id": "J_TEST", "title": "测试岗位",
                       "department": "测试部门", "stage": "简历复筛/进行中"},
            "candidates": [{"applicant_id": "candidate-1", "name": "候选人甲",
                            "status": "eligible", "existing_jobs": []}],
            "checked_at": "2026-10-07T03:00:00Z",
        }
        with patch.object(talent_pool, "_run_skill", return_value=live):
            preview = talent_pool.search(self.runtime.store, "local-admin", self.job, {})
        with patch("talentpilot.italent_recommendation.preflight", return_value=checked):
            return talent_pool.prepare_recommendation(
                self.runtime.store, "local-admin", preview["batch_id"],
                ["candidate-1"], preview["fingerprint"]
            )


if __name__ == "__main__":
    unittest.main()
