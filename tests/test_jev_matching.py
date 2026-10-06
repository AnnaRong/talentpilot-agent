import json
import os
import unittest
from unittest.mock import Mock, patch

from talentpilot import jev_matching
from talentpilot.config import settings


def response_body(score=4.0, fit_confidence=.9, skills=.85, requirements=.8,
                  seniority="matched", seniority_confidence=.88):
    return {"model": "jev-1.13.0", "answers": {
        "overall_fit": {"score": score, "confidence": fit_confidence, "probabilities": {"4": 1}},
        "required_skills": {"noul": skills}, "hard_requirements": {"noul": requirements},
        "seniority": {"choice": seniority, "confidence": seniority_confidence,
                      "probabilities": {seniority: 1}}}, "usage": {"input_tokens": 123}}


class JevMatchingTests(unittest.TestCase):
    def test_deidentify_removes_direct_identifiers(self):
        text = "姓名：张三\n手机 13800138000 邮箱 real@example.com 身份证 110101199001011234 https://private.test/a"
        masked = jev_matching.deidentify(text, "张三")
        for value in ("张三", "13800138000", "real@example.com", "110101199001011234", "private.test"):
            self.assertNotIn(value, masked)
        jev_matching.verify_deidentified(text, masked, "张三")

    def test_evaluate_uses_pinned_jev_and_structured_gate(self):
        response = Mock(status_code=200)
        response.json.return_value = response_body()
        client = Mock(); client.post.return_value = response
        with patch.dict(os.environ, {"TYPESAFE_EGRESS_ENABLED": "1"}), \
             patch.object(settings, "typesafe_api_key", "test-only"), \
             patch.object(settings, "typesafe_base_url", "https://api.typesafe.ai/v1"), \
             patch.object(settings, "typesafe_model", "jev-1.13.0"):
            result = jev_matching.evaluate({"title": "测试", "duties": "工作", "qualifications": "要求"},
                                           "姓名：张三\nPython项目", "张三", client)
        self.assertTrue(result["high"]); self.assertEqual(result["score"], 80)
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "jev-1.13.0")
        self.assertNotIn("张三", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(set(payload["questions"]),
                         {"overall_fit", "required_skills", "hard_requirements", "seniority"})

    def test_gates_fail_closed(self):
        for body in (response_body(requirements=.69), response_body(fit_confidence=.49),
                     response_body(seniority="underqualified")):
            self.assertFalse(jev_matching._parse(body)["high"])

    def test_missing_config_and_wrong_host_never_call(self):
        client = Mock()
        with patch.dict(os.environ, {"TYPESAFE_EGRESS_ENABLED": "0"}), patch.object(settings, "typesafe_api_key", ""):
            with self.assertRaises(jev_matching.JevConfigurationError):
                jev_matching.evaluate({}, "resume", client=client)
        with patch.dict(os.environ, {"TYPESAFE_EGRESS_ENABLED": "1"}), \
             patch.object(settings, "typesafe_api_key", "test-only"), \
             patch.object(settings, "typesafe_base_url", "https://evil.test/v1"):
            with self.assertRaises(jev_matching.JevConfigurationError):
                jev_matching.evaluate({}, "resume", client=client)
        client.post.assert_not_called()

    def test_malformed_response_is_rejected(self):
        with self.assertRaises(jev_matching.JevServiceError):
            jev_matching._parse({"answers": {}})
        with self.assertRaises(jev_matching.JevServiceError):
            jev_matching._parse({**response_body(), "model": "not-jev"})

    def test_direction_uses_validated_jev_signals(self):
        result = jev_matching._parse(response_body(score=3, skills=.4, requirements=.5,
                                                   seniority="underqualified"))
        guidance = jev_matching.direction(result)
        self.assertEqual(guidance["direction"], "调整后再匹配")
        self.assertTrue(any("核心技能" in item for item in guidance["adjustments"]))
        self.assertTrue(any("级别" in item for item in guidance["adjustments"]))
