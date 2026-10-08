"""Authenticated iTalent recommendation transport with fail-closed read-back."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class ITalentRecommendationUnavailable(RuntimeError):
    pass


def write_enabled() -> bool:
    return os.getenv("ITALENT_RECOMMEND_WRITE_ENABLED", "").strip() == "1"


def _run(script: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    executable = shutil.which("ego-browser")
    if not executable:
        raise ITalentRecommendationUnavailable("Ego Browser 不可用，无法核验当前 iTalent 会话")
    source = "const TALENTPILOT_INPUT=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    source += Path(__file__).with_name(script).read_text(encoding="utf-8")
    try:
        result = subprocess.run([executable, "nodejs"], input=source, text=True,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ITalentRecommendationUnavailable("iTalent 实时核验超时，未确认任何写入结果") from error
    records = [line.split("TALENTPILOT_RESULT:", 1)[1]
               for line in (result.stdout + "\n" + result.stderr).splitlines()
               if line.startswith("TALENTPILOT_RESULT:")]
    if result.returncode or not records:
        details = (result.stdout + "\n" + result.stderr).lower()
        if "user is controlling" in details:
            raise ITalentRecommendationUnavailable("Ego Browser 当前由用户控制，请交还控制权后重试")
        if "manual login" in details or "awaiting_login" in details:
            raise ITalentRecommendationUnavailable("北森登录已失效，请完成登录后重试")
        raise ITalentRecommendationUnavailable("iTalent 实时推荐核验失败；未执行或确认外部写入")
    try:
        data = json.loads(records[-1])
    except json.JSONDecodeError as error:
        raise ITalentRecommendationUnavailable("iTalent 推荐适配器返回格式无效") from error
    if not isinstance(data, dict):
        raise ITalentRecommendationUnavailable("iTalent 推荐适配器返回格式无效")
    return data


def _payload(job: dict[str, Any], candidates: list[dict[str, Any]], target_stage: str) -> dict[str, Any]:
    required = ("job_id", "title", "department")
    if any(not str(job.get(key) or "").strip() for key in required):
        raise ValueError("目标岗位编号、名称和部门必须完整")
    if target_stage != "简历复筛/进行中":
        raise ValueError("人才库推荐目标必须是简历复筛/进行中")
    if not 1 <= len(candidates) <= 20:
        raise ValueError("单批候选人必须为1至20人")
    clean = []
    for candidate in candidates:
        applicant_id = str(candidate.get("applicant_id") or "").strip()
        name = str(candidate.get("name") or "").strip()
        if not applicant_id or not name:
            raise ValueError("候选人缺少 applicantId 或姓名")
        clean.append({"applicant_id": applicant_id, "name": name})
    return {"job": {key: str(job[key]).strip() for key in required},
            "target_stage": target_stage, "candidates": clean}


def preflight(job: dict[str, Any], candidates: list[dict[str, Any]], target_stage: str) -> dict[str, Any]:
    payload = _payload(job, candidates, target_stage)
    data = _run("italent_recommend_preflight.js", payload, 360)
    if data.get("state") != "ready_for_confirmation":
        raise ITalentRecommendationUnavailable(str(data.get("reason") or "推荐预检未通过")[:400])
    returned = data.get("candidates")
    expected = {item["applicant_id"] for item in payload["candidates"]}
    if (not isinstance(returned, list) or {str(item.get("applicant_id")) for item in returned} != expected
            or any(item.get("status") != "eligible" for item in returned)):
        raise ITalentRecommendationUnavailable("候选人预检结果不完整或存在阻塞")
    target = data.get("target") or {}
    if any(target.get(key) != payload["job"][key] for key in ("job_id", "title", "department")):
        raise ITalentRecommendationUnavailable("预检返回的目标岗位与当前岗位不一致")
    if target.get("stage") != target_stage:
        raise ITalentRecommendationUnavailable("预检返回的目标阶段与简历复筛/进行中不一致")
    return {**data, "status": "ready_for_confirmation", "external_actions": 0}


def execute(preflight_payload: dict[str, Any]) -> dict[str, Any]:
    if not write_enabled():
        raise ITalentRecommendationUnavailable("iTalent 推荐写入开关未启用")
    data = _run("italent_recommend_execute.js", preflight_payload, 600)
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise ITalentRecommendationUnavailable("推荐后没有逐候选人回读结果")
    allowed = {"recommended_verified", "already_at_target", "blocked", "uncertain"}
    if any(item.get("status") not in allowed for item in results):
        raise ITalentRecommendationUnavailable("推荐回读结果包含未知状态")
    verified = sum(item.get("status") == "recommended_verified" for item in results)
    return {"status": "completed" if all(item.get("status") in {"recommended_verified", "already_at_target"}
                                             for item in results) else "partial_or_uncertain",
            "results": results, "external_actions": verified}
