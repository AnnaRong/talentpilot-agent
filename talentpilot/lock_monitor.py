"""Actor-scoped lock observations and local reminders. Never mutates the ATS."""
from __future__ import annotations

import time
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from .platform_store import digest, utcnow
from .workflows import parse_time

DEFAULTS = {"enabled": False, "interval_minutes": 30, "warning_hours": 24}
FRESH_SECONDS = 1800
SKILL = Path(__file__).resolve().parents[1] / "integrations" / "italent-followed-applicant-relock"


def initialize(store):
    with store.transaction() as cx:
        cx.executescript("""
          CREATE TABLE IF NOT EXISTS lock_monitor_settings (
            actor TEXT PRIMARY KEY, payload BLOB NOT NULL, next_due REAL NOT NULL,
            running_until REAL NOT NULL DEFAULT 0, last_success TEXT, last_error TEXT);
          CREATE TABLE IF NOT EXISTS lock_watches (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, source TEXT NOT NULL,
            candidate_id TEXT NOT NULL, job_id TEXT NOT NULL, payload BLOB NOT NULL,
            UNIQUE(actor,source,candidate_id,job_id));
          CREATE TABLE IF NOT EXISTS lock_notifications (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, watch_id TEXT NOT NULL,
            payload BLOB NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
          CREATE TABLE IF NOT EXISTS lock_recall_previews (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, payload BLOB NOT NULL,
            fingerprint TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
        """)


def _settings(store, actor):
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM lock_monitor_settings WHERE actor=?", (actor,)).fetchone()
    return dict(row) if row else None


def configure(store, actor, enabled, interval_minutes, warning_hours):
    if type(enabled) is not bool or type(interval_minutes) is not int or not 15 <= interval_minutes <= 1440:
        raise ValueError("刷新间隔须为15至1440分钟")
    if type(warning_hours) is not int or not 1 <= warning_hours <= 168:
        raise ValueError("提前提醒须为1至168小时")
    payload = dict(enabled=enabled, interval_minutes=interval_minutes, warning_hours=warning_hours)
    with store.transaction() as cx:
        cx.execute("""INSERT INTO lock_monitor_settings(actor,payload,next_due) VALUES (?,?,?)
          ON CONFLICT(actor) DO UPDATE SET payload=excluded.payload,next_due=excluded.next_due""",
                   (actor, store.seal(payload), time.time()))
        store.audit(cx, actor, "lock_monitor.configure_read_only", actor)
    return payload


def _put(store, cx, actor, source, item):
    key = digest([actor, source, item["candidate_id"], item.get("job_id") or ""])
    cx.execute("""INSERT INTO lock_watches VALUES (?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
               (key, actor, source, item["candidate_id"], item.get("job_id") or "", store.seal(item)))
    return key


def track_upload(store, actor, batch_id, result):
    """A locked upload preview is not a successful upload receipt."""
    count = 0
    with store.transaction() as cx:
        for candidate in result.get("results", []):
            rows = candidate.get("applications", [])
            ids = {r.get("candidate_id") for r in rows if r.get("candidate_id")}
            if candidate.get("status") != "history_found" or len(ids) != 1:
                continue
            if not any(r.get("locked") is True for r in rows):
                continue
            item = {"candidate_id": next(iter(ids)), "name": candidate.get("candidate_name"),
                    "job_id": result["job_id"], "job_title": result.get("job_title"),
                    "locked": True, "unlock_at": None, "observed_at": result.get("collected_at"),
                    "has_permission": None, "deleted": None, "active": True,
                    "provenance": "upload_blocked", "batch_id": batch_id,
                    "lock_label": "核验发现被锁，未上传"}
            _put(store, cx, actor, "upload", item)
            count += 1
        store.audit(cx, actor, "lock_monitor.track_upload_preview", batch_id)
    return count


def classify(item, warning_hours, now=None):
    now = time.time() if now is None else now
    at = parse_time(item.get("observed_at"))
    deadline = parse_time(item.get("unlock_at"))
    seconds = round(deadline.timestamp() - now) if deadline else None
    fresh = bool(at and 0 <= now - at.timestamp() <= FRESH_SECONDS)
    if not item.get("active", True):
        state = "out_of_scope"
    elif not fresh:
        state = "stale"
    elif item.get("has_permission") is False or item.get("deleted") is True:
        state = "unavailable"
    elif item.get("source") == "recruiting" and item.get("current_job_locked") is False:
        state = "lost"
    elif item.get("locked") is False:
        state = "unlocked"
    elif item.get("locked") is not True or seconds is None:
        state = "unknown"
    elif seconds <= 0:
        state = "verify_due"
    elif seconds <= warning_hours * 3600:
        state = "expiring"
    else:
        state = "locked"
    return {"state": state, "remaining_seconds": seconds, "fresh": fresh}


def _notify(store, cx, actor, key, item, warning_hours, now):
    state = classify(item, warning_hours, now)["state"]
    if state not in {"unlocked", "expiring", "verify_due", "lost"}:
        return
    fingerprint = digest([key, state, item.get("unlock_at")])
    payload = {"watch_id": key, "name": item.get("name"), "state": state,
               "source": item["source"], "job_id": item.get("job_id"), "unlock_at": item.get("unlock_at")}
    cx.execute("INSERT OR IGNORE INTO lock_notifications VALUES (?,?,?,?,?,NULL)",
               (fingerprint, actor, key, store.seal(payload), utcnow()))


def overview(store, actor, now=None):
    now = time.time() if now is None else now
    row = _settings(store, actor)
    config = store.unseal(row["payload"]) if row else dict(DEFAULTS)
    with store.transaction() as cx:
        watches = cx.execute("SELECT * FROM lock_watches WHERE actor=?", (actor,)).fetchall()
        items = []
        for watch in watches:
            item = {**store.unseal(watch["payload"]), "id": watch["id"], "source": watch["source"]}
            item.update(classify(item, config["warning_hours"], now))
            items.append(item)
        notifications = [{"id": n["id"], **store.unseal(n["payload"]), "created_at": n["created_at"],
                          "read_at": n["read_at"]} for n in cx.execute(
                              "SELECT * FROM lock_notifications WHERE actor=? ORDER BY created_at DESC LIMIT 100", (actor,))]
    order = {"lost": 0, "unlocked": 0, "verify_due": 1, "expiring": 2, "unknown": 3, "stale": 4, "locked": 5}
    items.sort(key=lambda i: (order.get(i["state"], 6), i["remaining_seconds"] if i["remaining_seconds"] is not None else float("inf")))
    return {"settings": config, "items": items, "notifications": notifications,
            "counts": {state: sum(i["state"] == state for i in items)
                       for state in ("unlocked", "verify_due", "expiring", "unknown", "stale", "locked", "lost")},
            "running": bool(row and row["running_until"] > now),
            "last_success": row["last_success"] if row else None,
            "last_error": row["last_error"] if row else None,
            "next_due": row["next_due"] if row and config["enabled"] else None,
            "server_now": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "skill_installed": (SKILL / "SKILL.md").is_file(),
            "recall_write_enabled": False, "notification_channel": "local_inbox",
            "external_actions": 0}


def acknowledge(store, actor):
    with store.transaction() as cx:
        cx.execute("UPDATE lock_notifications SET read_at=? WHERE actor=? AND read_at IS NULL", (utcnow(), actor))
        store.audit(cx, actor, "lock_monitor.acknowledge", actor)
    return {"status": "read"}


def refresh(store, actor, collector=None, scheduled=False):
    if not _settings(store, actor):
        configure(store, actor, **DEFAULTS)
    now = time.time()
    with store.transaction() as cx:
        user = cx.execute("SELECT * FROM users WHERE id=? AND enabled=1", (actor,)).fetchone()
        if not user or user["role"] != "admin" or "*" not in json.loads(user["departments"]):
            raise ValueError("当前监测仅支持已授权的本地管理员")
        row = cx.execute("SELECT * FROM lock_monitor_settings WHERE actor=?", (actor,)).fetchone()
        config = store.unseal(row["payload"])
        if scheduled and (not config["enabled"] or row["next_due"] > now):
            return None
        if row["running_until"] > now:
            if scheduled:
                return None
            raise ValueError("锁状态正在刷新，请稍后查看")
        cx.execute("UPDATE lock_monitor_settings SET running_until=?,next_due=? WHERE actor=?",
                   (now + 600, now + config["interval_minutes"] * 60, actor))
    try:
        if collector is None:
            from .italent_browser import collect_locks
            collector = collect_locks
        result = collector()
        if result.get("complete") is not True:
            raise ValueError("锁状态分页不完整，未覆盖上次结果")
        observed = parse_time(result.get("collected_at"))
        if not observed or not 0 <= time.time() - observed.timestamp() <= FRESH_SECONDS:
            raise ValueError("锁状态采集时间缺失或已过期")
        records = result.get("records", [])
        keys = [(i.get("source"), i.get("candidate_id"), i.get("job_id") or "") for i in records]
        if len(set(keys)) != len(keys) or any(not c or s not in {"recruiting", "followed"} for s, c, _ in keys):
            raise ValueError("锁记录身份缺失或重复，未覆盖上次结果")
        with store.transaction() as cx:
            owner = cx.execute("SELECT * FROM users WHERE id=? AND enabled=1", (actor,)).fetchone()
            if not owner or owner["role"] != "admin" or "*" not in json.loads(owner["departments"]):
                raise ValueError("采集期间账号权限已变化，本次结果未入库")
            previous = {(r["candidate_id"], r["job_id"]) for r in cx.execute(
                "SELECT candidate_id,job_id FROM lock_watches WHERE actor=? AND source='recruiting'", (actor,))}
            # Keep historical watches, but do not leave vanished rows actionable.
            for old in cx.execute("SELECT * FROM lock_watches WHERE actor=?", (actor,)).fetchall():
                item = store.unseal(old["payload"])
                item["active"] = False
                if old["source"] == "upload":
                    item["active"] = True
                cx.execute("UPDATE lock_watches SET payload=? WHERE id=?", (store.seal(item), old["id"]))
            for record in records:
                if record["source"] == "recruiting" and record.get("current_job_locked") is not True and (
                        record["candidate_id"], record.get("job_id") or "") not in previous:
                    continue
                allowed = ("candidate_id", "name", "job_id", "job_title", "locked", "unlock_at", "lock_label",
                           "has_permission", "deleted", "followed", "recruitment_type", "application_id",
                           "phase", "status", "detail_url", "scope", "current_job_locked")
                item = {k: record.get(k) for k in allowed}
                item.update(observed_at=result["collected_at"], active=True, source=record["source"])
                key = _put(store, cx, actor, record["source"], item)
                _notify(store, cx, actor, key, item, config["warning_hours"], time.time())
            # Only a candidate-level followed observation can update an upload watch.
            followed = {r["candidate_id"]: r for r in records if r["source"] == "followed"}
            for old in cx.execute("SELECT * FROM lock_watches WHERE actor=? AND source='upload'", (actor,)).fetchall():
                observation = followed.get(old["candidate_id"])
                if not observation:
                    continue
                item = store.unseal(old["payload"])
                for key in ("locked", "unlock_at", "has_permission", "deleted", "lock_label", "detail_url"):
                    item[key] = observation.get(key)
                item.update(observed_at=result["collected_at"], source="upload")
                cx.execute("UPDATE lock_watches SET payload=? WHERE id=?", (store.seal(item), old["id"]))
                _notify(store, cx, actor, old["id"], item, config["warning_hours"], time.time())
            cx.execute("UPDATE lock_monitor_settings SET running_until=0,last_success=?,last_error=NULL WHERE actor=?",
                       (result["collected_at"], actor))
            store.event(cx, None, "lock_monitor.refreshed", "locks",
                        {"count": len(records), "coverage": result.get("coverage"), "external_actions": 0})
            store.audit(cx, actor, "lock_monitor.read_only_refresh", actor)
    except Exception:
        with store.transaction() as cx:
            cx.execute("UPDATE lock_monitor_settings SET running_until=0,last_error=? WHERE actor=?",
                       ("采集未完成，请检查北森登录、浏览器控制权或页面字段；旧数据不会视作本次刷新。", actor))
            store.audit(cx, actor, "lock_monitor.refresh_failed", actor)
        raise
    return overview(store, actor)


def dispatch_due(runtime):
    now = time.time()
    with runtime.store.transaction() as cx:
        actors = [dict(r) for r in cx.execute(
            "SELECT * FROM lock_monitor_settings WHERE next_due<=? AND running_until<=?", (now, now))]
    for row in actors:
        if runtime.store.unseal(row["payload"])["enabled"]:
            try:
                refresh(runtime.store, row["actor"], scheduled=True)
            except Exception:
                # This worker only retries reads, never hiring mutations.
                with runtime.store.transaction() as cx:
                    cx.execute("UPDATE lock_monitor_settings SET next_due=? WHERE actor=? AND next_due<=?",
                               (time.time() + 900, row["actor"], now))


def recall_preview(store, actor, watch_id, job_id, target_stage):
    if not job_id or not target_stage.strip():
        raise ValueError("请明确配置承接岗位和目标阶段状态")
    item = next((i for i in overview(store, actor)["items"] if i["id"] == watch_id), None)
    if not item or item["state"] != "unlocked" or item["source"] != "followed":
        raise ValueError("仅实时确认已解锁的关注候选人可生成回捞核验单")
    if item.get("has_permission") is not True or item.get("followed") is not True or item.get("deleted") is not False:
        raise ValueError("关注、权限或删除状态缺少实时证据，请先在北森核实")
    from .boss import latest_position
    job = latest_position(store, job_id)
    payload = {"candidate_id": item["candidate_id"], "name": item.get("name"),
               "job_id": job_id, "job_title": job["title"], "target_stage": target_stage.strip(),
               "observation": digest(item), "source": "followed",
               "checks": ["重新读取关注、删除与权限状态", "实时锁状态与目标岗位重复申请",
                          "确认没有推进中的其他流程", "招聘类型一致", "实时解析岗位、流程、阶段和状态ID",
                          "逐人执行后回读全部目标ID"],
               "status": "pending_live_preflight", "external_actions": 0}
    preview_id = uuid.uuid4().hex
    fingerprint = digest([preview_id, actor, payload])
    with store.transaction() as cx:
        cx.execute("INSERT INTO lock_recall_previews VALUES (?,?,?,?,?,0)",
                   (preview_id, actor, store.seal(payload), fingerprint, time.time() + 300))
        store.audit(cx, actor, "lock_monitor.recall_preview", preview_id)
    return {**payload, "preview_id": preview_id, "fingerprint": fingerprint, "expires_in": 300}


def confirm_recall(store, actor, preview_id, fingerprint):
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM lock_recall_previews WHERE id=? AND actor=?", (preview_id, actor)).fetchone()
        if not row or row["consumed"] or row["expires"] <= time.time() or row["fingerprint"] != fingerprint:
            raise ValueError("回捞核验单已失效，请重新核验")
        cx.execute("UPDATE lock_recall_previews SET consumed=1 WHERE id=?", (preview_id,))
        store.audit(cx, actor, "lock_monitor.recall_blocked_unverified_connector", preview_id)
    return {"status": "blocked", "external_actions": 0,
            "reason": "回捞 Skill 已接入；当前应用尚未验真推荐写入与逐人回读，未执行回捞。"}
