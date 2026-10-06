import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from talentpilot.boss import (create_plan, graduation_eligibility, graduation_policy,
                              require_graduation_eligibility,
                              latest_position, position_health, resume_directory)
from talentpilot.platform_store import utcnow
from talentpilot.runtime import Runtime


class BossPlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_health_requires_empty_later_rounds_and_sparse_first(self):
        position = {"first_interview_count": 2, "second_interview_count": 0,
                    "third_interview_count": 0}
        self.assertTrue(position_health(position, 3)["priority"])
        position["second_interview_count"] = 1
        self.assertFalse(position_health(position, 3)["priority"])

    def test_plan_is_local_and_quota_bounded(self):
        position = {"job_id": "J1", "title": "AI/产品", "department": "TEST",
                    "first_interview_count": 1, "second_interview_count": 0,
                    "third_interview_count": 0, "collected_at": "2026-09-22T00:00:00Z"}
        criteria = {"recruitment_type": "实习生", "education": "本科",
                    "internship_keywords": ["AI", "产品"],
                    "min_internships": 1, "greeting_limit": 12, "sparse_first_max": 3}
        result = create_plan(self.runtime.store, "local-admin", position, criteria)
        self.assertEqual(result["status"], "awaiting_boss_connection")
        self.assertEqual(result["external_actions"], 0)
        self.assertEqual(result["criteria"]["graduation_policy"]["minimum_year"],
                         datetime.now(ZoneInfo("Asia/Shanghai")).year + 1)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        self.assertTrue(result["resume_directory"].endswith(f"Desktop/简历/AI-产品-J1/{today}"))
        with self.runtime.store.transaction() as cx:
            self.assertEqual(cx.execute("SELECT count(*) FROM boss_plans").fetchone()[0], 1)
        criteria["greeting_limit"] = 201
        with self.assertRaises(ValueError):
            create_plan(self.runtime.store, "local-admin", position, criteria)

    def test_graduation_year_is_a_fail_closed_outreach_rule(self):
        self.assertEqual(graduation_policy("校招", 2026)["exact_year"], 2027)
        self.assertTrue(graduation_eligibility("校招", "2027届", 2026)["eligible"])
        self.assertFalse(graduation_eligibility("校招", "2028届", 2026)["eligible"])
        self.assertTrue(graduation_eligibility("实习生", 2028, 2026)["eligible"])
        self.assertFalse(graduation_eligibility("实习生", 2026, 2026)["eligible"])
        self.assertTrue(graduation_eligibility("社招", "2026届", 2026)["eligible"])
        self.assertFalse(graduation_eligibility("社招", "2027届", 2026)["eligible"])
        self.assertFalse(graduation_eligibility("实习生", "未知", 2026)["eligible"])
        with self.assertRaisesRegex(ValueError, "禁止打招呼"):
            require_graduation_eligibility("校招", "2028届", 2026)

    def test_resume_path_sanitizes_job_text(self):
        self.assertEqual(resume_directory("AI/产品", "J:1", "2026-09-22").name, "2026-09-22")
        self.assertEqual(resume_directory("AI/产品", "J:1", "2026-09-22").parent.name, "AI-产品-J-1")

    def test_latest_position_uses_audited_server_inventory(self):
        inventory = {"collected_at": utcnow(), "positions": [{"job_id": "J1", "title": "Test",
                     "first_interview_count": 1, "second_interview_count": 0,
                     "third_interview_count": 0}]}
        with self.runtime.store.transaction() as cx:
            self.runtime.store.event(cx, None, "italent.inventory.collected", "sourcing", inventory)
        self.assertEqual(latest_position(self.runtime.store, "J1")["title"], "Test")
        with self.assertRaises(ValueError):
            latest_position(self.runtime.store, "J2")


if __name__ == "__main__":
    unittest.main()
