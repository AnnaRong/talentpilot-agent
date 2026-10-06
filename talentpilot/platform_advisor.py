"""Bounded LLM interview for choosing a recruitment-platform workflow."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

from .config import settings

PLATFORMS = {"BOSS直聘", "公司系统与人才库", "海外", "邮箱"}


def _configuration_issues() -> list[str]:
    issues = []
    if not settings.llm_api_key:
        issues.append("LLM_API_KEY")
    if os.getenv("LLM_EGRESS_ENABLED", "0") != "1":
        issues.append("LLM_EGRESS_ENABLED=1")
    parsed = urlsplit(settings.llm_base_url)
    allowed_host = os.getenv("LLM_ALLOWED_HOST", "").strip()
    if not allowed_host:
        issues.append("LLM_ALLOWED_HOST")
    elif (parsed.scheme != "https" or parsed.hostname != allowed_host
          or parsed.username or parsed.password):
        issues.append("批准的 HTTPS LLM_BASE_URL")
    return issues


def configured() -> bool:
    return not _configuration_issues()


def status() -> dict:
    return {"configured": configured(), "model": settings.llm_model,
            "egress_enabled": os.getenv("LLM_EGRESS_ENABLED", "0") == "1",
            "api_key_configured": bool(settings.llm_api_key),
            "allowed_host_configured": bool(os.getenv("LLM_ALLOWED_HOST", "").strip()),
            "missing": _configuration_issues(),
            "data_scope": "job_metadata_platform_and_user_answers_only"}


def _endpoint() -> str:
    parsed = urlsplit(settings.llm_base_url)
    if (parsed.scheme != "https" or parsed.hostname != os.getenv("LLM_ALLOWED_HOST", "")
            or parsed.username or parsed.password):
        raise ValueError("LLM 地址必须匹配管理员批准的 HTTPS 域名")
    return settings.llm_base_url.rstrip("/") + "/chat/completions"


def _parse(value: dict, answer_count: int) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("complete"), bool):
        raise ValueError("LLM Grill Me 返回格式无效")
    complete = value["complete"]
    question = value.get("question")
    recommendation = value.get("recommended_answer")
    rationale = value.get("rationale")
    summary = value.get("summary", [])
    adjustments = value.get("adjustments", [])
    if complete:
        if (not isinstance(summary, list) or not 1 <= len(summary) <= 8
                or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in summary)
                or not isinstance(adjustments, list) or len(adjustments) > 8
                or any(not isinstance(item, str) or not item.strip() or len(item) > 300 for item in adjustments)):
            raise ValueError("LLM Grill Me 总结格式无效")
    elif (not isinstance(question, str) or not question.strip() or len(question) > 300
          or not isinstance(recommendation, str) or not recommendation.strip() or len(recommendation) > 300
          or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 500):
        raise ValueError("LLM Grill Me 必须一次返回一个问题和推荐答案")
    if answer_count >= 6 and not complete:
        raise ValueError("LLM Grill Me 超过六轮仍未形成结论")
    return {"complete": complete, "question": question if not complete else None,
            "recommended_answer": recommendation if not complete else None,
            "rationale": rationale if not complete else None,
            "summary": summary if complete else [], "adjustments": adjustments if complete else []}


def interview(platform: str, job: dict, answers: list[str], client=None) -> dict:
    if platform not in PLATFORMS:
        raise ValueError("不支持的招聘平台")
    missing = _configuration_issues()
    if missing:
        return {"status": "awaiting_configuration", "llm_calls": 0, "complete": False,
                "missing": missing}
    if len(answers) > 6 or any(not isinstance(answer, str) or not answer.strip() or len(answer) > 1000
                               for answer in answers):
        raise ValueError("Grill Me 回答必须为一至六条非空文本")
    context = {
        "platform": platform,
        "job": {key: job.get(key) for key in (
            "job_id", "title", "department", "first_interview_count",
            "second_interview_count", "third_interview_count")},
        "answers": answers,
    }
    messages = [
        {"role": "system", "content": (
            "You are the iTalentPilot recruitment workflow interviewer. Use Grill Me discipline: "
            "ask exactly one Chinese question at a time and include your recommended answer. "
            "Resolve branches depth-first in this order: browser/session choice, exact task, current "
            "problems or incorrect assumptions, platform-specific constraints, then readiness. "
            "Do not ask facts already answered. By the fourth answer, finish unless a safety-critical "
            "ambiguity remains; never exceed six answers. You cannot send messages, upload resumes, "
            "change ATS state, or claim a connector works. Return JSON only. While interviewing: "
            '{"complete":false,"question":"...","recommended_answer":"...","rationale":"..."}. '
            "When ready: "
            '{"complete":true,"summary":["..."],"adjustments":["..."]}.')},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    own_client = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=False)
    try:
        response = client.post(_endpoint(), headers={"Authorization": "Bearer " + settings.llm_api_key},
                               json={"model": settings.llm_model, "messages": messages,
                                     "max_completion_tokens": 700,
                                     "response_format": {"type": "json_object"}})
        if response.status_code != 200:
            raise ValueError(f"LLM 网关返回 HTTP {response.status_code}")
        try:
            text = response.json()["choices"][0]["message"]["content"]
            result = _parse(json.loads(text), len(answers))
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("LLM Grill Me 返回格式无效") from error
        return {**result, "status": "ready" if result["complete"] else "question",
                "model": settings.llm_model, "llm_calls": 1,
                "external_hiring_actions": 0}
    except httpx.HTTPError as error:
        raise ValueError("LLM 网关连接失败") from error
    finally:
        if own_client:
            client.close()
