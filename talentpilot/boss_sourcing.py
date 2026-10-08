"""Read-only BOSS candidate search, filtering and review ranking."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .candidate_filter_config import filter_candidates, parse_candidate_filter_config
from .platform_store import digest, utcnow


class BossCliUnavailable(RuntimeError):
    pass


def _years(text: str) -> tuple[int | None, int | None]:
    values = [int(value) for value in re.findall(r"(\d+)\s*(?:年|年以上|年经验)", text)]
    if not values:
        return None, None
    minimum = min(values)
    exact_range = re.search(r"(\d+)\s*[-~至]\s*(\d+)\s*年", text)
    return (int(exact_range.group(1)), int(exact_range.group(2))) if exact_range else (minimum, None)


def profile_from_jd(jd: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build conservative sourcing criteria from verified duties and qualifications."""
    if jd.get("state") != "verified_job_jd" or not jd.get("job_id") or not jd.get("title"):
        raise ValueError("必须使用已核验的岗位 JD 自动生成寻才条件")
    text = f"{jd.get('duties', '')}\n{jd.get('qualifications', '')}"
    from .resume_matching import keywords as jd_keywords
    terms = jd_keywords(jd)
    title = re.sub(r"^20\d{2}校园招聘-(?:北京|上海|深圳|广州|成都|杭州)-", "", jd["title"])
    title = re.sub(r"-(?:Comate[^-]*组|[^-]*组)-[^-]+$", "", title, flags=re.I)
    title = re.sub(r"实习生$", "", title).strip("-_ ") or jd["title"]
    search_terms = list(dict.fromkeys([title, *terms[:5]]))[:6]
    required = terms[:6] or [title]
    bonus = terms[6:12]
    minimum, maximum = _years(text)
    education = next((value for value in ("博士", "硕士", "本科", "大专") if value in text), None)
    city = next((value for value in ("北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "南京", "西安")
                if value in jd["title"] or value in text), "")
    document = {
        "search": {"keywords": search_terms, "city": city,
                   "experience": f"{minimum}-{maximum}年" if minimum is not None and maximum is not None else
                                 f"{minimum}年以上" if minimum is not None else "",
                   "status": ["在职-考虑机会", "离职-随时到岗"]},
        "candidate_filter": {
            "keywords": {"values": required, "match": "any"},
            "experience": {key: value for key, value in (("min_years", minimum), ("max_years", maximum)) if value is not None},
            "activity": {"max_inactive_days": 30}, "exclude_status": ["暂不考虑"],
        },
        "scoring": {"required_keywords": required, "bonus_keywords": bonus},
        "top_n": 15, "schedule": {"enabled": True, "hour": 11, "minute": 0},
    }
    evidence = {"source": "verified_job_jd", "job_id": jd["job_id"], "title": jd["title"],
                "education_hint": education, "keywords": terms, "inferred": ["30日活跃", "Top 15"],
                "omitted": ["年龄", "薪资"]}
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False), evidence


def configure_from_jd(store, actor: str, position: dict[str, Any], jd: dict[str, Any]) -> dict[str, Any]:
    content, evidence = profile_from_jd(jd)
    schedule = configure_schedule(store, actor, position, content)
    return {"job_id": position["job_id"], "title": position["title"], "schedule": schedule,
            "profile": parse_profile(content), "yaml_content": content, "evidence": evidence}


def configure_all_from_jd(store, actor: str, positions: list[dict[str, Any]], reader=None) -> dict[str, Any]:
    if not positions:
        raise ValueError("当前没有可配置的岗位")
    if reader is None:
        from .italent_browser import collect_job_jd
        reader = collect_job_jd
    results = []
    for position in positions:
        try:
            item = configure_from_jd(store, actor, position, reader(position["job_id"]))
            results.append({"job_id": position["job_id"], "title": position["title"],
                            "status": "configured_from_jd", "keywords": item["profile"]["keywords"],
                            "evidence": item["evidence"]})
        except Exception as error:
            results.append({"job_id": position.get("job_id"), "title": position.get("title"),
                            "status": "failed", "error": str(error)[:240]})
    configured = sum(item["status"] == "configured_from_jd" for item in results)
    return {"positions": len(results), "configured": configured, "failed": len(results) - configured,
            "results": results, "external_actions": 0}


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
        cx.execute("""CREATE TABLE IF NOT EXISTS boss_greeting_previews (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, batch_id TEXT NOT NULL, payload BLOB NOT NULL,
          fingerprint TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, result BLOB)""")


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
    executable = _boss_executable()
    if not executable:
        raise BossCliUnavailable("boss-cli 未安装，请先安装固定版本 @viyzhu/boss-cli-fork@0.8.1")
    try:
        result = subprocess.run([executable, *args], capture_output=True, text=True,
                                timeout=int(os.getenv("BOSS_SEARCH_TIMEOUT", "45")))
    except subprocess.TimeoutExpired as error:
        raise BossCliUnavailable("BOSS 搜索超时；本次结果未保存") from error
    if result.returncode:
        detail = (result.stderr or result.stdout or "boss-cli 执行失败").strip().splitlines()[-1]
        raise BossCliUnavailable(detail[:400])
    return result.stdout


def existing_login_status() -> dict[str, Any]:
    """Inspect the open controlled BOSS browser before opening another window."""
    import httpx
    port = int(os.getenv("BOSS_BROWSER_REMOTE_DEBUGGING_PORT", "53470"))
    try:
        rows = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=1).json()
    except Exception:
        rows = []
    pages = [row for row in rows if row.get("type") == "page" and "zhipin.com" in str(row.get("url", ""))]
    logged_in = any("/web/chat/" in row.get("url", "") for row in pages)
    return {"logged_in": logged_in, "controlled_pages": len(pages),
            "detail": "已复用 BOSS CLI 浏览器登录态" if logged_in else "未发现可受控的 BOSS 登录页面"}


def start_login() -> dict[str, Any]:
    executable = _boss_executable()
    if not executable:
        raise BossCliUnavailable("boss-cli 未安装，请先安装固定版本 @viyzhu/boss-cli-fork@0.8.1")
    process = subprocess.Popen([executable, "login"], start_new_session=True)
    return {"status": "login_window_opened", "pid": process.pid, "external_actions": 0}


def _boss_executable() -> str | None:
    executable = shutil.which("boss")
    if executable:
        return executable
    nvm = Path.home() / ".nvm" / "versions" / "node"
    candidates = sorted(nvm.glob("*/bin/boss"), reverse=True) if nvm.is_dir() else []
    return str(candidates[0]) if candidates else None


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


def _batch(store, actor: str, batch_id: str, fingerprint: str) -> tuple[Any, dict[str, Any]]:
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM boss_sourcing_batches WHERE id=?", (batch_id,)).fetchone()
    if not row or row["actor"] != actor:
        raise ValueError("BOSS 审核清单不存在或不属于当前用户")
    if row["payload_hash"] != fingerprint:
        raise ValueError("候选人清单或岗位已变化，请重新搜索")
    return row, store.unseal(row["payload"])


def _search_args(position: dict[str, Any], profile: dict[str, Any], keyword: str) -> list[str]:
    args = ["search", keyword, "--job", position["title"]]
    if profile["city"]:
        args.extend(["--city", profile["city"]])
    if profile["experience"]:
        args.extend(["--exp", profile["experience"]])
    if profile["status"]:
        args.extend(["--status", ",".join(profile["status"])])
    return args


def _relocate(candidate: dict[str, Any], position: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    keyword = candidate["search_keyword"]
    current = parse_search_output(_run_boss(_search_args(position, profile, keyword)), keyword)
    matches = [row for row in current if row["candidate_id"] == candidate["candidate_id"]]
    if len(matches) != 1:
        raise ValueError(f"候选人列表已变化，无法唯一定位：{candidate['name']}")
    return matches[0]


def _dry_run(candidate: dict[str, Any], position: dict[str, Any]) -> str:
    return _run_boss(["greet", candidate["search_keyword"], "--index",
                      str(candidate["source_index"]), "--job", position["title"], "--dry-run"])


def prepare_greetings(store, actor: str, batch_id: str, candidate_ids: list[str],
                      fingerprint: str) -> dict[str, Any]:
    _, payload = _batch(store, actor, batch_id, fingerprint)
    selected_ids = list(dict.fromkeys(candidate_ids))
    available = {row["candidate_id"]: row for row in payload["candidates"]}
    if not selected_ids or len(selected_ids) > 20 or not set(selected_ids).issubset(available):
        raise ValueError("候选人必须来自当前 Top N，且单次不超过20人")
    profile = parse_profile(payload.get("yaml_content", "")) if payload.get("yaml_content") else None
    if profile is None:
        with store.transaction() as cx:
            schedule = cx.execute("SELECT yaml_content FROM boss_sourcing_schedules WHERE actor=? AND job_id=?",
                                  (actor, payload["job"]["job_id"])).fetchone()
        if not schedule:
            raise ValueError("原筛选配置不存在，请重新搜索")
        profile = parse_profile(store.unseal(schedule["yaml_content"]))
    relocated = []
    for candidate_id in selected_ids:
        current = _relocate(available[candidate_id], payload["job"], profile)
        check = _dry_run(current, payload["job"])
        if "未发出招呼" not in check and "dry-run" not in check.lower():
            raise ValueError(f"候选人定位预检没有确认安全取消：{current['name']}")
        relocated.append(current)
    preview_id = uuid.uuid4().hex
    preview_payload = {"preview_id": preview_id, "batch_id": batch_id, "job": payload["job"],
                       "candidates": relocated, "status": "awaiting_confirmation",
                       "created_at": utcnow(), "external_actions": 0}
    preview_fingerprint = digest({"preview_id": preview_id, "batch_fingerprint": fingerprint,
                                  "candidate_ids": selected_ids, "candidates": relocated})
    with store.transaction() as cx:
        cx.execute("INSERT INTO boss_greeting_previews VALUES (?,?,?,?,?,?,0,?,NULL)",
                   (preview_id, actor, batch_id, store.seal(preview_payload), preview_fingerprint,
                    time.time() + 300, preview_payload["created_at"]))
        store.audit(cx, actor, "boss.greeting.preview_created", preview_id)
    return {**preview_payload, "fingerprint": preview_fingerprint, "expires_in": 300}


def confirm_greetings(store, actor: str, preview_id: str, fingerprint: str,
                      explicit_confirmation: bool) -> dict[str, Any]:
    if not explicit_confirmation:
        raise ValueError("打招呼前需要当前轮明确确认")
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM boss_greeting_previews WHERE id=?", (preview_id,)).fetchone()
    if not row or row["actor"] != actor:
        raise ValueError("打招呼预览不存在或不属于当前用户")
    if row["consumed"]:
        raise ValueError("打招呼预览已执行")
    if row["expires"] <= time.time() or row["fingerprint"] != fingerprint:
        raise ValueError("打招呼预览已过期或发生变化，请重新预检")
    payload = store.unseal(row["payload"])
    with store.transaction() as cx:
        claimed = cx.execute("UPDATE boss_greeting_previews SET consumed=1 WHERE id=? AND consumed=0",
                             (preview_id,))
    if claimed.rowcount != 1:
        raise ValueError("打招呼预览已执行")
    results = []
    for candidate in payload["candidates"]:
        try:
            with store.transaction() as cx:
                schedule = cx.execute("SELECT yaml_content FROM boss_sourcing_schedules WHERE actor=? AND job_id=?",
                                      (actor, payload["job"]["job_id"])).fetchone()
            if not schedule:
                raise ValueError("原筛选配置不存在")
            profile = parse_profile(store.unseal(schedule["yaml_content"]))
            current = _relocate(candidate, payload["job"], profile)
            _dry_run(current, payload["job"])
            output = _run_boss(["greet", current["search_keyword"], "--index",
                                str(current["source_index"]), "--job", payload["job"]["title"]])
            results.append({"candidate_id": candidate["candidate_id"], "name": candidate["name"],
                            "status": "sent", "read_back": output[-400:]})
        except Exception as error:
            results.append({"candidate_id": candidate["candidate_id"], "name": candidate["name"],
                            "status": "failed", "error": str(error)[:400]})
            break
    sent = sum(item["status"] == "sent" for item in results)
    result = {"preview_id": preview_id, "status": "completed" if sent == len(payload["candidates"]) else "partial_failure",
              "sent": sent, "results": results, "external_actions": sent}
    with store.transaction() as cx:
        cx.execute("UPDATE boss_greeting_previews SET result=? WHERE id=?", (store.seal(result), preview_id))
        store.audit(cx, actor, "boss.greeting.executed", preview_id)
    return result


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
