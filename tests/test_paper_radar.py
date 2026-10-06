import unittest

from talentpilot.paper_radar import contact_drafts, parse_jd, run


class PaperRadarTests(unittest.TestCase):
    def fixture(self):
        return {
            "OpenAlex": [
                {"title": "Large Language Models for Test Generation", "abstract": "大模型 自然语言处理 软件测试",
                 "year": 2025, "cited_by_count": 120, "venue": "NeurIPS",
                 "authors": [{"name": "Zhang San", "orcid": "0000-0001-2345-6789", "institutions": ["清华大学"]},
                             {"name": "Li Si", "institutions": ["清华大学"], "corresponding": True}]},
                {"title": "Large Language Models for Test Generation", "abstract": "duplicate record",
                 "year": 2025, "cited_by_count": 120, "venue": "NeurIPS", "doi": "10.1000/test",
                 "authors": [{"name": "Zhang San", "orcid": "0000-0001-2345-6789", "institutions": ["清华大学"]}]},
            ]
        }

    def test_parse_jd_extracts_role_location_and_preferences(self):
        profile = parse_jd("高级机器学习工程师，负责大模型和自然语言处理，工作地点北京，优先清华大学或研究院")
        self.assertEqual(profile["level"], "高级")
        self.assertIn("北京", profile["locations"])
        self.assertIn("大模型", profile["skills"])
        self.assertTrue(profile["institution_preferences"])

    def test_run_deduplicates_scores_and_extracts_roles(self):
        result = run("高级机器学习工程师，负责大模型和自然语言处理，工作地点北京",
                     ["OpenAlex"], 5, source_payloads=self.fixture())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["candidate_count"], 2)
        by_name = {item["name"]: item for item in result["candidates"]}
        self.assertEqual(by_name["Zhang San"]["roles"], ["一作"])
        self.assertIn("通讯作者", by_name["Li Si"]["roles"])
        self.assertEqual(by_name["Zhang San"]["identity_confidence"], "高")

    def test_contact_drafts_are_local_only(self):
        result = run("高级机器学习工程师，负责大模型和自然语言处理，工作地点北京",
                     ["OpenAlex"], 5, source_payloads=self.fixture())
        drafts = contact_drafts(result["candidates"][:1], result["profile"])
        self.assertEqual(len(drafts), 1)
        self.assertIn("需 HR 审阅后发送", drafts[0]["body"])
        self.assertEqual(drafts[0]["channel"], "邮件/私信")


if __name__ == "__main__":
    unittest.main()
