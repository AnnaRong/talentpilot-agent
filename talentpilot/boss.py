"""BOSS outreach planning. Browser reads and sends are separate approval steps."""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .platform_store import utcnow


EDUCATION_LEVELS = {"不限", "大专", "本科", "硕士", "博士"}
RECRUITMENT_TYPES = {"校招", "实习生", "社招"}


def graduation_policy(recruitment_type: str, current_year: int | None = None) -> dict:
    if recruitment_type not in RECRUITMENT_TYPES:
        raise ValueError("请选择校招、实习生或社招")
    current_year = current_year or datetime.now(ZoneInfo("Asia/Shanghai")).year
    campus_year = current_year + 1
    if recruitment_type == "校招":
        return {"recruitment_type": recruitment_type, "exact_year": campus_year,
                "label": f"仅限{campus_year}届"}
    if recruitment_type == "实习生":
        return {"recruitment_type": recruitment_type, "minimum_year": campus_year,
                "label": f"仅限{campus_year}届及以后"}
    return {"recruitment_type": recruitment_type, "maximum_year": current_year,
            "label": f"仅限{current_year}届及以前"}


def graduation_eligibility(recruitment_type: str, graduation_year, current_year: int | None = None) -> dict:
    policy = graduation_policy(recruitment_type, current_year)
    match = re.fullmatch(r"(20[0-9]{2})(?:届)?", str(graduation_year or "").strip())
    if not match:
        return {"eligible": False, "graduation_year": None, "policy": policy,
                "reason": "毕业届别缺失或无法核实，禁止打招呼"}
    year = int(match.group(1))
    eligible = (year == policy.get("exact_year") if "exact_year" in policy else
                year >= policy["minimum_year"] if "minimum_year" in policy else
                year <= policy["maximum_year"])
    return {"eligible": eligible, "graduation_year": year, "policy": policy,
            "reason": "届别符合硬性条件" if eligible else f"{year}届不符合{policy['label']}，禁止打招呼"}


def require_graduation_eligibility(recruitment_type: str, graduation_year, current_year: int | None = None) -> dict:
    result = graduation_eligibility(recruitment_type, graduation_year, current_year)
    if not result["eligible"]:
        raise ValueError(result["reason"])
    return result


def latest_position(store, job_id: str, max_age_seconds: int = 1800) -> dict:
    with store.transaction() as cx:
        row = cx.execute("SELECT payload FROM events WHERE type='italent.inventory.collected' "
                         "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError("请先刷新岗位及面试人数")
    inventory = store.unseal(row["payload"])
    try:
        collected = datetime.fromisoformat(inventory["collected_at"].replace("Z", "+00:00"))
        age = time.time() - collected.timestamp()
    except (KeyError, TypeError, ValueError):
        raise ValueError("岗位采集时间无效，请重新刷新") from None
    if age < -300 or age > max_age_seconds:
        raise ValueError("岗位健康数据已超过30分钟，请重新刷新")
    position = next((item for item in inventory.get("positions", []) if item.get("job_id") == job_id), None)
    if not position:
        raise ValueError("所选岗位不在刚刚读取的岗位中")
    return {**position, "collected_at": inventory["collected_at"]}


def position_health(position: dict, sparse_first_max: int = 3) -> dict:
    if not 0 <= sparse_first_max <= 20:
        raise ValueError("一面人数阈值必须在0至20之间")
    counts = {}
    for key in ("first_interview_count", "second_interview_count", "third_interview_count"):
        value = position.get(key)
        if not isinstance(value, int) or value < 0:
            raise ValueError("请先刷新岗位及面试人数")
        counts[key] = value
    priority = (counts["second_interview_count"] == 0 and
                counts["third_interview_count"] == 0 and
                counts["first_interview_count"] <= sparse_first_max)
    return {**counts, "sparse_first_max": sparse_first_max, "priority": priority,
            "reason": "二面、三面无人推进且一面人数较少" if priority else "当前面试推进不满足优先触达条件"}


def resume_directory(job_title: str, job_id: str, day: str | None = None) -> Path:
    safe_title = re.sub(r'[\/:*?"<>|]+', "-", job_title).strip(" .-") or "未命名岗位"
    safe_job_id = re.sub(r"[^A-Za-z0-9_-]+", "-", job_id).strip("-") or "unknown"
    day = day or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("日期格式必须为YYYY-MM-DD")
    return Path.home() / "Desktop" / "简历" / f"{safe_title}-{safe_job_id}" / day


def create_plan(store, actor: str, position: dict, criteria: dict) -> dict:
    required = {"job_id", "title", "first_interview_count", "second_interview_count",
                "third_interview_count", "collected_at"}
    if not required.issubset(position):
        raise ValueError("岗位健康数据不完整，请重新刷新")
    education = criteria.get("education")
    if education not in EDUCATION_LEVELS:
        raise ValueError("请选择学历要求")
    graduation = graduation_policy(criteria.get("recruitment_type"))
    keywords = [str(value).strip() for value in criteria.get("internship_keywords", []) if str(value).strip()]
    if len(keywords) > 10 or any(len(value) > 40 for value in keywords):
        raise ValueError("实习关键词最多10个，每个不超过40字")
    greeting_limit = criteria.get("greeting_limit")
    if not isinstance(greeting_limit, int) or not 1 <= greeting_limit <= 200:
        raise ValueError("打招呼上限必须在1至200之间")
    min_internships = criteria.get("min_internships")
    if not isinstance(min_internships, int) or not 0 <= min_internships <= 5:
        raise ValueError("实习经历段数必须在0至5之间")
    health = position_health(position, criteria.get("sparse_first_max", 3))
    plan_id = uuid.uuid4().hex
    payload = {
        "plan_id": plan_id, "job_id": position["job_id"], "job_title": position["title"],
        "department": position.get("department"), "health": health,
        "criteria": {"education": education, "internship_keywords": keywords,
                     "min_internships": min_internships, "greeting_limit": greeting_limit,
                     "recruitment_type": graduation["recruitment_type"],
                     "graduation_policy": graduation},
        "position_collected_at": position["collected_at"],
        "resume_directory": str(resume_directory(position["title"], position["job_id"])),
        "status": "awaiting_boss_connection" if health["priority"] else "not_prioritized",
        "created_at": utcnow(), "external_actions": 0,
    }
    with store.transaction() as cx:
        cx.execute("INSERT INTO boss_plans VALUES (?,?,?,?,?,?)",
                   (plan_id, actor, position["job_id"], store.seal(payload), payload["status"], payload["created_at"]))
        store.audit(cx, actor, "boss.plan.created", plan_id)
    return payload
