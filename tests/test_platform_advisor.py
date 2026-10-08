import json
import os
import unittest
from unittest.mock import Mock, patch

from talentpilot import platform_advisor
from talentpilot.config import settings


class PlatformAdvisorTests(unittest.TestCase):
    def test_missing_configuration_never_calls_network(self):
        client = Mock()
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "0"}), patch.object(
                settings, "llm_api_key", ""):
            result = platform_advisor.interview("BOSS直聘", {"job_id": "J1"}, [], client)
        self.assertEqual(result["status"], "awaiting_configuration")
        self.assertEqual(result["llm_calls"], 0)
        client.post.assert_not_called()

    def test_invalid_gateway_configuration_is_not_ready(self):
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "1",
                                     "LLM_ALLOWED_HOST": "other.test"}), patch.object(
                settings, "llm_api_key", "test-only"), patch.object(
                settings, "llm_base_url", "https://gateway.test/v1"):
            result = platform_advisor.status()
        self.assertFalse(result["configured"])
        self.assertIn("批准的 HTTPS LLM_BASE_URL", result["missing"])

    def test_one_question_with_recommendation(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "complete": False, "question": "使用哪个浏览器？",
            "recommended_answer": "使用已有登录态的 Ego Browser。",
            "rationale": "可复用当前授权会话。",
        }, ensure_ascii=False)}}]}
        client = Mock(); client.post.return_value = response
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "1",
                                          "LLM_ALLOWED_HOST": "gateway.test"}), patch.object(
                settings, "llm_api_key", "test-only"), patch.object(
                settings, "llm_base_url", "https://gateway.test/v1"):
            result = platform_advisor.interview("邮箱", {"job_id": "J1", "title": "测试"}, [], client)
        self.assertEqual(result["status"], "question")
        self.assertEqual(result["llm_calls"], 1)
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("candidate", json.dumps(payload))

    def test_rejects_unbounded_or_invalid_model_output(self):
        with self.assertRaises(ValueError):
            platform_advisor._parse({"complete": False, "question": "只有问题"}, 0)
        with self.assertRaises(ValueError):
            platform_advisor._parse({"complete": False, "question": "问题",
                                     "recommended_answer": "推荐", "rationale": "原因"}, 6)

    def test_gateway_error_is_fast_and_actionable(self):
        response = Mock(status_code=404)
        client = Mock(); client.post.return_value = response
        with patch.dict(os.environ, {"LLM_EGRESS_ENABLED": "1",
                                     "LLM_ALLOWED_HOST": "gateway.test"}), patch.object(
                settings, "llm_api_key", "test-only"), patch.object(
                settings, "llm_base_url", "https://gateway.test/v1"):
            with self.assertRaisesRegex(ValueError, "HTTP 404"):
                platform_advisor.interview("邮箱", {"job_id": "J1"}, [], client)


if __name__ == "__main__":
    unittest.main()
