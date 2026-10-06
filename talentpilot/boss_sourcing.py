"""Read-only BOSS candidate search, filtering and review ranking."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .candidate_filter_config import filter_candidates, parse_candidate_filter_config
from .platform_store import digest, utcnow


class BossCliUnavailable(RuntimeError):
    pass


def initialize(store) -> None:
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS boss_sourcing_batches (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, job_id TEXT NOT NULL,
          payload BLOB NOT NULL, payload_hash TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL)""")
        cx.execute("""CREATE TABLE IF NOT EXISTS boss_candidate_pool (
          candidate_id TEXT PRIMARY KEY, payload BLOB NOT NULL, first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL)""")
        cx.execute("""CREATE TABLE IF NOT EXISTS boss_sourcing_schedules (
          actor TEXT NOT NULL, job_id TEXT NOT NULL, position BLOB NOT NULL, yaml_content BLOB NOT NULL,
          enabled INTEGER NOT NULL, hour INTEGER NOT NULL, minute INTEGER NOT NULL,
          last_run_date TEXT, last_status TEXT, last_error TEXT,
          PRIMARY KEY(actor,job_id))""")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def parse_profile(content: str) -> dict[str, Any]:
    try:
        root = _mapping(yaml.safe_load(content), "YAML document")
    except yaml.YAMLError as error:
        raise ValueError(f"unable to parse sourcing YAML: {error}") from error
    search = _mapping(root.get("search"), "search")
    keywords = search.get("keywords", [])
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 12 or any(
            not isinstance(value, str) or not value.strip() for value in keywords):
        raise ValueError("search.keywords must contain 1 to 12 strings")
    status = search.get("status", [])
    if not isinstance(status, list) or any(not isinstance(value, str) for value in status):
        raise ValueError("search.status must be a list of strings")
    scoring = _mapping(root.get("scoring"), "scoring")
    required = scoring.get("required_keywords", [])
    bonus = scoring.get("bonus_keywords", [])
    if any(not isinstance(group, list) or any(not isinstance(value, str) for value in group)
           for group in (required, bonus)):
        raise ValueError("scoring keywords must be lists of strings")
    top_n = root.get("top_n", 15)
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 50:
        raise ValueError("top_n must be between 1 and 50")
    schedule = _mapping(root.get("schedule"), "schedule")
    hour = schedule.get("hour", 11)
    minute = schedule.get("minute", 0)
    if (isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23
            or isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 59):
        raise ValueError("schedule hour/minute are invalid")
    return {
        "keywords": [value.strip() for value in keywords],
        "city": str(search.get("city") or "").strip(),
        "experience": str(search.get("experience") or "").strip(),
        "status": [value.strip() for value in status if value.strip()],
        "required_keywords": [value.strip() for value in required if value.strip()],
        "bonus_keywords": [value.strip() for value in bonus if value.strip()],
        "top_n": top_n,
        "schedule": {"enabled": bool(schedule.get("enabled", False)),
                     "hour": hour, "minute": minute},
        "filter": parse_candidate_filter_config(content),
    }


def _run_boss(args: list[str]) -> str:
    executable = shutil.which("boss")
    if not executable:
        raise BossCliUnavailable("boss-cli 未安装，请先安装固定版本 @viyzhu/boss-cli-fork@0.8.1")
    try:
        result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        raise BossCliUnavailable("BOSS 搜索超时；本次结果未保存") from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "boss-cli 执行失败").strip().splitlines()[-1]
        raise BossCliUnavailable(detail[:400])
    return result.stdout


def _number(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, re.I)
    return int(match.group(1)) if match else None


def parse_search_output(output: str, keyword: str) -> list[dict[str, Any]]:
    candidates = []
    blocks = re.split(r"(?m)^(?=\d+\.\s+)", output)
    for block in blocks:
        match = re.match(r"(\d+)\.\s*([^｜\n]+)(.*)", block, re.S)
        if not match:
            continue
        index, name, body = int(match.group(1)), match.group(2).strip(), match.group(3).strip()
        first = body.splitlines()[0] if body else ""
        fields = [value.strip() for value in first.split("｜") if value.strip()]
        active = next((value for value in fields if "活跃" in value or "在线" in value), "")
        status = next((value for value in ("离职-随时到岗", "在职-暂不考虑",
                                           "在职-考虑机会", "在职-月内到岗") if value in block), "")
        salaries = re.findall(r"(\d+)\s*[-~—至]\s*(\d+)\s*[Kk]", block)
        salary_max = max((int(high) for _, high in salaries), default=None)
        text = re.sub(r"(?m)^\s*(摘要|亮点|期望|经历|教育):\s*", "", block).strip()
        # The CLI does not expose a stable candidate ID in list output. Use the
        # normalized visible identity for cross-keyword dedup, and keep the
        # source index only for same-page follow-up. Same-name ambiguity remains
        # a review concern and is never used for an external action here.
        identity = "|".join((name.casefold(), str(_number(r"(\d{2})岁", block) or ""),
                             str(_number(r"(\d+(?:\.\d+)?)年(?:经验)?", block) or "")))
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        candidates.append({
            "candidate_id": fingerprint, "source_index": index, "name": name,
            "search_keyword": keyword, "active_text": active, "status": status,
            "age": _number(r"(\d{2})岁", block),
            "experience_years": _number(r"(\d+(?:\.\d+)?)年(?:经验)?", block),
            "salary_max_k": salary_max, "text": text,
        })
    return candidates


def _score(candidate: dict[str, Any], required: list[str], bonus: list[str]) -> dict[str, Any]:
    text = candidate["text"].casefold()
    required_hits = [value for value in required if value.casefold() in text]
    bonus_hits = [value for value in bonus if value.casefold() in text]
    required_score = 70 * len(required_hits) / max(1, len(required)) if required else 70
    bonus_score = 30 * len(bonus_hits) / max(1, len(bonus)) if bonus else 0
    return {**candidate, "score": round(required_score + bonus_score, 1),
            "score_evidence": {"required": required_hits, "bonus": bonus_hits}}


def _historical_candidates(store) -> list[dict[str, Any]]:
    with store.transaction() as cx:
        rows = cx.execute("SELECT payload FROM boss_candidate_pool").fetchall()
    return [store.unseal(row["payload"]) for row in rows]


def configure_schedule(store, actor: str, position: dict[str, Any], yaml_content: str) -> dict[str, Any]:
    profile = parse_profile(yaml_content)
    schedule = profile["schedule"]
    with store.transaction() as cx:
        cx.execute("""INSERT INTO boss_sourcing_schedules
          (actor,job_id,position,yaml_content,enabled,hour,minute,last_run_date,last_status,last_error)
          VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL)
          ON CONFLICT(actor,job_id) DO UPDATE SET position=excluded.position,
          yaml_content=excluded.yaml_content,enabled=excluded.enabled,hour=excluded.hour,minute=excluded.minute""",
                   (actor, position["job_id"], store.seal(position), store.seal(yaml_content),
                    int(schedule["enabled"]), schedule["hour"], schedule["minute"]))
        store.audit(cx, actor, "boss.sourcing.schedule_configured", position["job_id"])
    return schedule


def search_and_rank(store, actor: str, position: dict[str, Any], yaml_content: str,
                    *, configure: bool = True) -> dict[str, Any]:
    if not position.get("job_id") or not position.get("title"):
        raise ValueError("必须绑定准确岗位")
    profile = parse_profile(yaml_content)
    if configure:
        configure_schedule(store, actor, position, yaml_content)
    all_rows = []
    for keyword in profile["keywords"]:
        args = ["search", keyword, "--job", position["title"]]
        if profile["city"]:
            args.extend(["--city", profile["city"]])
        if profile["experience"]:
            args.extend(["--exp", profile["experience"]])
        if profile["status"]:
            args.extend(["--status", ",".join(profile["status"])])
        all_rows.extend(parse_search_output(_run_boss(args), keyword))

    historical = _historical_candidates(store)
    unique = {row["candidate_id"]: row for row in historical}
    for row in all_rows:
        unique.setdefault(row["candidate_id"], row)
    filtered = filter_candidates(unique.values(), profile["filter"])
    ranked = sorted(
        (_score(dict(candidate), profile["required_keywords"], profile["bonus_keywords"])
         for candidate in filtered.included),
        key=lambda candidate: (-candidate["score"], candidate["name"]),
    )[:profile["top_n"]]
    batch_id = uuid.uuid4().hex
    payload = {
        "batch_id": batch_id, "job": position, "searched": len(all_rows),
        "unique": len(unique), "historical_scanned": len(historical),
        "eligible": len(filtered.included),
        "filtered": [{"candidate_id": item.candidate["candidate_id"],
                      "name": item.candidate.get("name"), "reasons": list(item.reasons)}
                     for item in filtered.excluded],
        "candidates": ranked, "status": "review_required",
        "created_at": utcnow(), "external_actions": 0,
    }
    fingerprint = digest({"job_id": position["job_id"], "yaml": yaml_content,
                          "candidate_ids": [row["candidate_id"] for row in ranked]})
    with store.transaction() as cx:
        for row in all_rows:
            cx.execute("""INSERT INTO boss_candidate_pool VALUES (?,?,?,?)
              ON CONFLICT(candidate_id) DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen""",
                       (row["candidate_id"], store.seal(row), payload["created_at"], payload["created_at"]))
        cx.execute("INSERT INTO boss_sourcing_batches VALUES (?,?,?,?,?,?,?)",
                   (batch_id, actor, position["job_id"], store.seal(payload), fingerprint,
                    payload["status"], payload["created_at"]))
        store.audit(cx, actor, "boss.sourcing.review_created", batch_id)
    return {**payload, "fingerprint": fingerprint}


def dispatch_due(store, now: datetime | None = None) -> int:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        raise ValueError("scheduled time must include a timezone")
    current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    today = current.date().isoformat()
    with store.transaction() as cx:
        rows = cx.execute("SELECT * FROM boss_sourcing_schedules WHERE enabled=1").fetchall()
    ran = 0
    for row in rows:
        if row["last_run_date"] == today or (current.hour, current.minute) < (row["hour"], row["minute"]):
            continue
        with store.transaction() as cx:
            claimed = cx.execute("""UPDATE boss_sourcing_schedules SET last_run_date=?,last_status='running',last_error=NULL
              WHERE actor=? AND job_id=? AND (last_run_date IS NULL OR last_run_date<>?)""",
                                 (today, row["actor"], row["job_id"], today))
        if claimed.rowcount != 1:
            continue
        try:
            result = search_and_rank(store, row["actor"], store.unseal(row["position"]),
                                     store.unseal(row["yaml_content"]), configure=False)
            with store.transaction() as cx:
                cx.execute("UPDATE boss_sourcing_schedules SET last_status='completed' WHERE actor=? AND job_id=?",
                           (row["actor"], row["job_id"]))
                store.audit(cx, row["actor"], "boss.sourcing.schedule_completed", result["batch_id"])
            ran += 1
        except Exception as error:
            with store.transaction() as cx:
                cx.execute("UPDATE boss_sourcing_schedules SET last_status='failed',last_error=? WHERE actor=? AND job_id=?",
                           (str(error)[:400], row["actor"], row["job_id"]))
    return ran
