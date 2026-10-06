"""Talent-pool search and recommendation gates.

The installed iTalent skill remains the source of truth for query mapping. This
module only owns the local web workflow, temporary selection handling and the
write gate; it never stores cookies, resume bodies or downloaded files.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .platform_store import digest, utcnow


SKILL_SCRIPT = Path.home() / ".codex/skills/screen-italent-resumes/scripts/italent_talent_pool.py"
TTL = 1800


class TalentPoolUnavailable(RuntimeError):
    """The authenticated iTalent search/write transport is not available."""


def initialize(store):
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS talent_pool_batches (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, job_id TEXT NOT NULL,
          target_stage TEXT NOT NULL, payload BLOB NOT NULL, payload_hash TEXT NOT NULL,
          status TEXT NOT NULL, expires REAL NOT NULL, created_at TEXT NOT NULL, result BLOB)""")


def normalize_query(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("人才库筛选条件格式不正确")
    school_types = payload.get("school_types", [])
    majors = payload.get("majors", [])
    if isinstance(school_types, str):
        school_types = [v.strip() for v in school_types.replace("，", ",").split(",") if v.strip()]
    if isinstance(majors, str):
        majors = [v.strip() for v in majors.replace("，", ",").split(",") if v.strip()]
    if not isinstance(school_types, list) or not all(isinstance(v, str) and v.strip() for v in school_types):
        raise ValueError("学校类型必须是文本列表")
    if not isinstance(majors, list) or not all(isinstance(v, str) and v.strip() for v in majors):
        raise ValueError("专业关键词必须是文本列表")
    recent_days = payload.get("recent_days")
    if recent_days in (None, ""):
        recent_days = None
    elif isinstance(recent_days, bool) or not isinstance(recent_days, int) or not 1 <= recent_days <= 3650:
        raise ValueError("最近储备天数必须在 1 到 3650 之间")
    limit = payload.get("limit", 20)
    pages = payload.get("pages", 1)
    page_size = payload.get("page_size", 100)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (limit, pages, page_size)):
        raise ValueError("人才库分页参数格式不正确")
    if not 1 <= limit <= 100 or not 1 <= pages <= 10 or page_size not in {15, 30, 60, 100, 300}:
        raise ValueError("人数上限、页数或分页大小超出范围")
    expected_city = str(payload.get("expected_city", "")).strip()
    talent_pool_id = str(payload.get("talent_pool_id", "")).strip()
    return {
        "school_types": sorted(set(v.strip() for v in school_types)),
        "majors": sorted(set(v.strip() for v in majors)),
        "recent_days": recent_days,
        "expected_city": expected_city,
        "include_sub_pools": bool(payload.get("include_sub_pools", False)),
        "talent_pool_id": talent_pool_id,
        "limit": limit,
        "pages": pages,
        "page_size": page_size,
    }


def _run_skill(query: dict[str, Any]) -> dict[str, Any]:
    if not SKILL_SCRIPT.is_file():
        raise TalentPoolUnavailable("人才库筛选 Skill 未安装或路径不可用")
    cookie = os.environ.get("ITALENT_COOKIE", "").strip()
    if not cookie:
        raise TalentPoolUnavailable("未配置当前 iTalent 授权传输；请先在 Ego Browser 完成登录并配置受控连接")
    with tempfile.TemporaryDirectory(prefix="talentpilot-pool-") as directory:
        output = Path(directory) / "selection.json"
        args = [sys.executable, str(SKILL_SCRIPT), "filter", "--headless",
                "--school-types", ",".join(query["school_types"]),
                "--majors", ",".join(query["majors"]),
                "--page-size", str(query["page_size"]), "--pages", str(query["pages"]),
                "--limit", str(query["limit"]), "--output", str(output)]
        if query["recent_days"] is not None:
            args.extend(["--recent-days", str(query["recent_days"])])
        if query["expected_city"]:
            args.extend(["--expected-city", query["expected_city"]])
        if query["include_sub_pools"]:
            args.append("--include-sub-pools")
        if query["talent_pool_id"]:
            args.extend(["--talent-pool-id", query["talent_pool_id"]])
        result = subprocess.run(args, capture_output=True, text=True, timeout=240,
                                env={**os.environ, "ITALENT_COOKIE": cookie})
        if result.returncode or not output.is_file():
            message = (result.stderr or result.stdout or "人才库读取失败").strip().splitlines()[-1]
            raise TalentPoolUnavailable(message[:400])
        data = json.loads(output.read_text(encoding="utf-8"))
    candidates = []
    for row in data.get("candidates", []):
        if not isinstance(row, dict) or not row.get("applicant_id"):
            continue
        candidates.append({
            "applicant_id": str(row["applicant_id"]),
            "name": str(row.get("name") or "待确认"),
            "school": str(row.get("school") or "未返回"),
            "major": str(row.get("major") or "未返回"),
            "education_level": str(row.get("education_level") or "未返回"),
            "expected_work_areas": str(row.get("expected_work_areas") or "未返回"),
            "expected_occupation": str(row.get("expected_occupation") or "未返回"),
            "last_reserve_time": str(row.get("last_reserve_time") or "未返回"),
            "recent_applied_positions": row.get("recent_applied_positions") or [],
            "state": "人才库候选人；申请上下文待推荐前检查",
        })
    return {"candidates": candidates, "criteria": data.get("criteria", query),
            "query_totals": data.get("query_totals", {}), "source": "screen-italent-resumes"}


def search(store, actor: str, job: dict[str, Any], raw_query: dict[str, Any]) -> dict[str, Any]:
    query = normalize_query(raw_query)
    result = _run_skill(query)
    batch_id = uuid.uuid4().hex
    payload = {"job": job, "target_stage": "简历复筛/进行中", "query": query,
               "candidates": result["candidates"], "criteria": result["criteria"],
               "query_totals": result["query_totals"]}
    fingerprint = digest({"batch_id": batch_id, "job_id": job["job_id"],
                          "target_stage": payload["target_stage"],
                          "candidate_ids": sorted(c["applicant_id"] for c in payload["candidates"]),
                          "query": query})
    with store.transaction() as cx:
        cx.execute("INSERT INTO talent_pool_batches VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                   (batch_id, actor, job["job_id"], payload["target_stage"], store.seal(payload),
                    fingerprint, "preview", time.time() + TTL, utcnow()))
        store.audit(cx, actor, "talent_pool.search_preview", batch_id)
    return {"status": "preview", "batch_id": batch_id, "fingerprint": fingerprint,
            "expires_in": TTL, "job": job, "target_stage": payload["target_stage"],
            "candidates": payload["candidates"], "criteria": query,
            "query_totals": payload["query_totals"], "external_actions": 0}


def recommend(store, actor: str, batch_id: str, candidate_ids: list[str], fingerprint: str,
              explicit_confirmation: bool) -> dict[str, Any]:
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM talent_pool_batches WHERE id=?", (batch_id,)).fetchone()
        if not row or row["actor"] != actor or row["status"] != "preview" or row["expires"] < time.time():
            raise ValueError("人才库推荐预览不存在、已过期或已执行")
        if row["payload_hash"] != fingerprint:
            raise ValueError("候选人名单或目标岗位已变化，请重新检索")
        payload = store.unseal(row["payload"])
        available = {candidate["applicant_id"] for candidate in payload["candidates"]}
        selected = list(dict.fromkeys(candidate_ids))
        if not selected or len(selected) > 20 or not set(selected).issubset(available):
            raise ValueError("推荐候选人必须来自当前预览，且单批不超过20人")
        if not explicit_confirmation:
            raise ValueError("推荐到复筛前需要当前轮明确确认")
        result = {"status": "blocked", "external_actions": 0,
                  "reason": "iTalent 推荐写入传输和写后回读尚未完成受控验真；本次未推荐。",
                  "job_id": payload["job"]["job_id"], "target_stage": payload["target_stage"],
                  "candidate_count": len(selected), "candidate_ids": selected,
                  "confirmation_fingerprint": fingerprint,
                  "read_back": False}
        cx.execute("UPDATE talent_pool_batches SET status='blocked',result=? WHERE id=?",
                   (store.seal(result), batch_id))
        store.audit(cx, actor, "talent_pool.recommend_blocked_unverified_connector", batch_id)
    return result
