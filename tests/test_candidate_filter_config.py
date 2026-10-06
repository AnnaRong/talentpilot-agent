import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from talentpilot.candidate_filter_config import (
    CandidateFilterConfig,
    filter_candidates,
    load_candidate_filter_config,
    parse_candidate_filter_config,
)


class CandidateFilterConfigTests(unittest.TestCase):
    def write_yaml(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "candidate-filter.yaml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_yaml_config(self):
        path = self.write_yaml(
            """
candidate_filter:
  keywords:
    values: [Python, AI]
    match: all
  experience:
    min_years: 2
    max_years: 5
  activity:
    max_inactive_days: 30
"""
        )

        config = load_candidate_filter_config(path)

        self.assertEqual(config.keywords, ("Python", "AI"))
        self.assertEqual(config.keyword_match, "all")
        self.assertEqual(config.min_experience_years, 2)
        self.assertEqual(config.max_experience_years, 5)
        self.assertEqual(config.max_inactive_days, 30)

    def test_filters_by_keywords_experience_and_activity(self):
        config = CandidateFilterConfig(
            keywords=("Python", "AI"),
            keyword_match="all",
            min_experience_years=2,
            max_experience_years=5,
            max_inactive_days=30,
        )
        candidates = [
            {
                "candidate_id": "qualified",
                "text": "Built Python services and AI evaluation tools",
                "experience_years": 3,
                "last_active_at": "2026-10-01T00:00:00Z",
            },
            {
                "candidate_id": "keyword-miss",
                "text": "Python backend engineer",
                "experience_years": 3,
                "last_active_at": "2026-10-01T00:00:00Z",
            },
            {
                "candidate_id": "experience-miss",
                "text": "Python and AI engineer",
                "experience_years": 1,
                "last_active_at": "2026-10-01T00:00:00Z",
            },
            {
                "candidate_id": "inactive",
                "text": "Python and AI engineer",
                "experience_years": 3,
                "last_active_at": "2026-08-01T00:00:00Z",
            },
        ]

        result = filter_candidates(
            candidates, config, now=datetime(2026, 10, 6, tzinfo=timezone.utc)
        )

        self.assertEqual([item["candidate_id"] for item in result.included], ["qualified"])
        excluded = {item.candidate["candidate_id"]: item.reasons for item in result.excluded}
        self.assertEqual(excluded["keyword-miss"], ("keywords_not_matched",))
        self.assertEqual(excluded["experience-miss"], ("experience_below_minimum",))
        self.assertEqual(excluded["inactive"], ("candidate_inactive",))

    def test_keyword_any_mode_is_case_insensitive(self):
        config = CandidateFilterConfig(keywords=("Python", "AI"), keyword_match="any")
        result = filter_candidates(
            [{"candidate_id": "one", "text": "PYTHON developer"}], config
        )
        self.assertEqual([item["candidate_id"] for item in result.included], ["one"])

    def test_missing_enabled_filter_fields_are_excluded(self):
        config = CandidateFilterConfig(
            min_experience_years=1, max_inactive_days=30
        )
        result = filter_candidates(
            [{"candidate_id": "missing", "text": "Python"}],
            config,
            now=datetime(2026, 10, 6, tzinfo=timezone.utc),
        )
        self.assertEqual(
            result.excluded[0].reasons,
            ("experience_missing", "activity_missing"),
        )

    def test_rejects_invalid_yaml_config(self):
        path = self.write_yaml(
            """
candidate_filter:
  keywords:
    values: [Python]
    match: sometimes
  experience:
    min_years: 5
    max_years: 2
"""
        )
        with self.assertRaisesRegex(ValueError, "keyword match"):
            load_candidate_filter_config(path)

    def test_extended_yaml_filters_age_salary_status_and_activity_text(self):
        config = parse_candidate_filter_config(
            """
candidate_filter:
  age: {min: 24, max: 35}
  salary: {max_k: 40}
  exclude_status: [暂不考虑]
  activity: {max_inactive_days: 7}
"""
        )
        result = filter_candidates([
            {"candidate_id": "ok", "text": "AI 产品经理", "age": 28,
             "salary_max_k": 35, "status": "在职-考虑机会", "active_text": "今日活跃"},
            {"candidate_id": "old", "text": "AI 产品经理", "age": 40,
             "salary_max_k": 35, "status": "在职-考虑机会", "active_text": "今日活跃"},
            {"candidate_id": "costly", "text": "AI 产品经理", "age": 28,
             "salary_max_k": 45, "status": "在职-考虑机会", "active_text": "今日活跃"},
            {"candidate_id": "closed", "text": "AI 产品经理", "age": 28,
             "salary_max_k": 35, "status": "在职-暂不考虑", "active_text": "今日活跃"},
            {"candidate_id": "stale", "text": "AI 产品经理", "age": 28,
             "salary_max_k": 35, "status": "在职-考虑机会", "active_text": "30日内活跃"},
        ], config)
        self.assertEqual([item["candidate_id"] for item in result.included], ["ok"])
        excluded = {item.candidate["candidate_id"]: item.reasons for item in result.excluded}
        self.assertEqual(excluded["old"], ("age_above_maximum",))
        self.assertEqual(excluded["costly"], ("salary_above_maximum",))
        self.assertEqual(excluded["closed"], ("status_excluded",))
        self.assertEqual(excluded["stale"], ("candidate_inactive",))


if __name__ == "__main__":
    unittest.main()
