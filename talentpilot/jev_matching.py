"""TypeSafe Jev client for privacy-reduced resume-to-JD decisions."""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

import httpx

from .config import settings
from .resume import mask_pii

API_HOST = "api.typesafe.ai"
FIT_LEVELS = [
    "No meaningful evidence that the resume matches the job description",
    "Weak overlap; only isolated terms or broadly related experience",
    "Partial match; some relevant evidence but several important gaps",
    "Moderate match; relevant experience with material gaps or limited depth",
    "Strong match; direct evidence for most important duties and qualifications",
    "Exceptional match; extensive, specific evidence across the job's core requirements",
]


class JevConfigurationError(ValueError):
    pass


class JevServiceError(ValueError):
    pass


def configured() -> bool:
    return bool(settings.typesafe_api_key) and os.getenv("TYPESAFE_EGRESS_ENABLED", "0") == "1"


def deidentify(text: str, candidate_name: str | None = None) -> str:
    value = mask_pii(text)
    value = re.sub(r"(?<!\d)(?:\d[ -]?){17}[\dXx](?!\d)", "[ID]", value)
    value = re.sub(r"https?://\S+", "[URL]", value)
    value = re.sub(r"(?im)^\s*(?:姓名|name)\s*[:：]?\s*[^\n|]{1,60}", "姓名：[NAME]", value)
    if candidate_name and 2 <= len(candidate_name) <= 60:
        value = value.replace(candidate_name, "[NAME]")
    return value[:24_000]


def verify_deidentified(original: str, masked: str, candidate_name: str | None = None) -> None:
    sensitive = [
        *re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", original),
        *re.findall(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", original),
        *re.findall(r"(?<!\d)(?:\d[ -]?){17}[\dXx](?!\d)", original),
        *re.findall(r"https?://\S+", original),
    ]
    if candidate_name:
        sensitive.append(candidate_name)
    if any(value and value in masked for value in sensitive):
        raise JevConfigurationError("简历脱敏校验失败，未发送到 Jev")


def _endpoint() -> str:
    base = settings.typesafe_base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.hostname != API_HOST or parsed.username or parsed.password:
        raise JevConfigurationError("TypeSafe 地址必须是官方 HTTPS API")
    return base + "/systemone"


def _payload(jd: dict, resume_text: str) -> dict:
    return {
        "state": {"job": {"title": jd.get("title"), "duties": jd.get("duties"),
                           "qualifications": jd.get("qualifications")}, "resume": resume_text},
        "model": settings.typesafe_model,
        "questions": {
            "overall_fit": {"type": "score", "criteria": FIT_LEVELS,
                "instructions": "Rate how strongly the resume evidence matches this exact job's duties and qualifications. Treat absent evidence as a gap. Do not infer protected traits or facts not present in resume."},
            "required_skills": {"type": "noul",
                "instructions": "Does resume provide concrete evidence for the core skills explicitly required by job, rather than merely repeating a title or isolated keywords?",
                "criteria": {"true": "Projects, work, achievements, or detailed use support the core skills", "false": "Core skills are absent, unsupported, or only mentioned"}},
            "hard_requirements": {"type": "noul",
                "instructions": "Does resume satisfy every explicit must-have constraint in job.qualifications that can be checked from the resume, including graduation timing, education, discipline, or experience? Unverifiable must-haves count against true.",
                "criteria": {"true": "All checkable must-have constraints are supported", "false": "At least one must-have is contradicted or not evidenced"}},
            "seniority": {"type": "choice", "instructions": "Compare resume seniority with the job level.",
                "criteria": {"underqualified": "Materially below required level", "matched": "Appropriate for required level", "overqualified": "Materially above required level", "unclear": "Insufficient evidence"}},
        },
    }


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise JevServiceError(f"Jev 返回的 {name} 无效")
    return float(value)


def _parse(body: dict) -> dict:
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise JevServiceError("Jev 返回格式无效")
    answers = body["answers"]
    fit, skills = answers.get("overall_fit") or {}, answers.get("required_skills") or {}
    requirements, seniority = answers.get("hard_requirements") or {}, answers.get("seniority") or {}
    raw_score = fit.get("score")
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)) or not 0 <= float(raw_score) <= 5:
        raise JevServiceError("Jev 返回的匹配分无效")
    fit_confidence = _number(fit.get("confidence"), "匹配置信度")
    seniority_confidence = _number(seniority.get("confidence"), "级别置信度")
    skill_probability = _number(skills.get("noul"), "技能概率")
    requirement_probability = _number(requirements.get("noul"), "硬条件概率")
    choice = seniority.get("choice")
    if choice not in {"underqualified", "matched", "overqualified", "unclear"}:
        raise JevServiceError("Jev 返回的岗位级别无效")
    score, confidence = round(float(raw_score) * 20, 2), round(min(fit_confidence, seniority_confidence), 4)
    high = (score > 70 and skill_probability >= .7 and requirement_probability >= .7
            and confidence >= settings.typesafe_confidence_threshold and choice in {"matched", "overqualified"})
    model = body.get("model")
    if not isinstance(model, str) or not model.startswith("jev-"):
        raise JevServiceError("TypeSafe 未返回可验证的 Jev 模型版本")
    return {"score": score, "high": high, "model": model,
            "requested_model": settings.typesafe_model, "fit_confidence": round(fit_confidence, 4),
            "confidence": confidence, "required_skills_probability": round(skill_probability, 4),
            "hard_requirements_probability": round(requirement_probability, 4), "seniority": choice,
            "seniority_confidence": round(seniority_confidence, 4),
            "probabilities": {"overall_fit": fit.get("probabilities"), "seniority": seniority.get("probabilities")},
            "usage": body.get("usage") if isinstance(body.get("usage"), dict) else {}}


def evaluate(jd: dict, resume_text: str, candidate_name: str | None = None, client=None) -> dict:
    if not configured():
        raise JevConfigurationError("Jev 尚未启用：请配置 TYPESAFE_API_KEY 和 TYPESAFE_EGRESS_ENABLED=1")
    own_client = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        masked = deidentify(resume_text, candidate_name)
        verify_deidentified(resume_text, masked, candidate_name)
        response = client.post(_endpoint(), headers={"Authorization": "Bearer " + settings.typesafe_api_key},
                               json=_payload(jd, masked))
        if response.status_code in {401, 403}:
            raise JevConfigurationError("TypeSafe API Key 无效或无 Jev 权限")
        if response.status_code != 200:
            raise JevServiceError(f"Jev 服务返回 HTTP {response.status_code}")
        try:
            return _parse(response.json())
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise JevServiceError("Jev 返回格式无效") from error
    except httpx.HTTPError as error:
        raise JevServiceError("Jev 服务连接失败") from error
    finally:
        if own_client:
            client.close()


def status() -> dict:
    return {"configured": configured(), "model": settings.typesafe_model, "provider": "TypeSafe AI",
            "egress_enabled": os.getenv("TYPESAFE_EGRESS_ENABLED", "0") == "1",
            "pii_reduction": "name_phone_email_id_url"}


def direction(result: dict) -> dict:
    """Turn validated Jev signals into bounded resume-direction guidance."""
    adjustments = []
    if result["required_skills_probability"] < .7:
        adjustments.append("补充与岗位核心技能直接对应的项目、工作或成果证据")
    if result["hard_requirements_probability"] < .7:
        adjustments.append("重新核对学历、毕业时间、专业或经验等岗位硬条件")
    if result["seniority"] == "underqualified":
        adjustments.append("优先寻找级别要求更匹配的岗位，或补足当前岗位要求的职责深度")
    elif result["seniority"] == "overqualified":
        adjustments.append("确认目标岗位级别是否偏低，并突出与目标职责直接相关的经历")
    elif result["seniority"] == "unclear":
        adjustments.append("在简历中明确职责范围、项目复杂度和个人贡献")
    if result["score"] <= 70:
        adjustments.append("扩大相邻岗位方向，同时保留与当前岗位 JD 的逐项证据核对")
    return {
        "direction": "继续当前方向" if result["high"] else "调整后再匹配",
        "adjustments": adjustments or ["当前方向证据较完整，继续人工核对具体岗位与个人意向"],
        "decision_boundary": "Jev 只比较简历证据与当前 JD，不作录用或职业决策",
    }
