"""Durable local schedules and versioned position panels. No ATS writes."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from .platform_store import digest, utcnow
from .workflows import parse_time


def initialize(store):
    with store.transaction() as cx:
        cx.executescript("""
          CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, dataset_id TEXT NOT NULL,
            kind TEXT NOT NULL, options BLOB NOT NULL, interval_seconds INTEGER NOT NULL,
            enabled INTEGER NOT NULL, next_due REAL NOT NULL, last_job TEXT, last_error TEXT);
          CREATE TABLE IF NOT EXISTS panels (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, department TEXT NOT NULL,
            revision INTEGER NOT NULL, payload BLOB NOT NULL, confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL, UNIQUE(job_id,department));
          CREATE TABLE IF NOT EXISTS panel_history (
            panel_id TEXT NOT NULL, revision INTEGER NOT NULL, payload BLOB NOT NULL,
            actor TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(panel_id,revision));
        """)


def save_panel(store, actor, dataset_id, job_id, department, rounds, expected_revision):
    capture = store.dataset(dataset_id)
    if not any(p["job_id"] == job_id and p.get("department") == department for p in capture["positions"]):
        raise ValueError("Exact position and department must exist in the authorized dataset")
    allowed = {"screening", "first", "second", "third", "final"}
    if not rounds or set(rounds) - allowed:
        raise ValueError("Rounds must use screening/first/second/third/final")
    for members in rounds.values():
        if (not isinstance(members, list) or not members or len(members) > 20
                or any(not isinstance(m, str) or not m.strip() or len(m) > 100 for m in members)
                or len(set(members)) != len(members)):
            raise ValueError("Each round requires unique explicit interviewer IDs")
    with store.transaction() as cx:
        old = cx.execute("SELECT * FROM panels WHERE job_id=? AND department=?", (job_id, department)).fetchone()
        revision = old["revision"] if old else 0
        if revision != expected_revision:
            raise ValueError("Panel changed; reload before confirming")
        panel_id = old["id"] if old else uuid.uuid4().hex
        payload = {"rounds": rounds, "dataset_id": dataset_id}
        cx.execute("INSERT OR REPLACE INTO panels VALUES (?,?,?,?,?,?,?)",
                   (panel_id, job_id, department, revision + 1, store.seal(payload), actor, utcnow()))
        cx.execute("INSERT INTO panel_history VALUES (?,?,?,?,?)",
                   (panel_id, revision + 1, store.seal(payload), actor, utcnow()))
        store.audit(cx, actor, "panel.confirm", panel_id)
    return {"panel_id": panel_id, "revision": revision + 1, "external_actions": 0}


def assignments(store, dataset_id):
    capture = store.dataset(dataset_id)
    positions = {p["job_id"]: p for p in capture["positions"]}
    with store.transaction() as cx:
        panels = {(r["job_id"], r["department"]): dict(r) for r in cx.execute("SELECT * FROM panels")}
    result = []
    for application in capture["applications"]:
        position = positions.get(application["job_id"], {})
        panel = panels.get((application["job_id"], position.get("department")))
        result.append({
            "application_id": application["application_id"], "candidate_id": application["candidate_id"],
            "job_id": application["job_id"], "department": position.get("department"),
            "panel_id": panel["id"] if panel else None, "revision": panel["revision"] if panel else None,
            "rounds": store.unseal(panel["payload"])["rounds"] if panel else {},
            "status": "local_template_applied" if panel else "awaiting_panel",
        })
    return {"assignments": result, "external_actions": 0, "ats_interviewer_assignment": False}


def create_schedule(runtime, actor, dataset_id, kind, options, interval_seconds, enabled=False):
    if kind not in {"audit", "analytics", "locks", "loads", "insights"}:
        raise ValueError("Only read-only deterministic workflows may be scheduled")
    if not 900 <= interval_seconds <= 604800:
        raise ValueError("Interval must be between 15 minutes and 7 days")
    runtime.store.dataset(dataset_id)
    schedule_id = uuid.uuid4().hex
    with runtime.store.transaction() as cx:
        cx.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
                   (schedule_id, actor, dataset_id, kind, runtime.store.seal(options),
                    interval_seconds, int(enabled), time.time()))
        runtime.store.audit(cx, actor, "schedule.create", schedule_id)
    return schedule_id


def dispatch_due(runtime, now=None):
    now = time.time() if now is None else now
    store = runtime.store
    with store.transaction() as cx:
        rows = [dict(r) for r in cx.execute("SELECT * FROM schedules WHERE enabled=1 AND next_due<=?", (now,))]
    for row in rows:
        capture = store.dataset(row["dataset_id"])
        at = parse_time(capture.get("run", {}).get("completed_at"))
        error = None
        if not at or not 0 <= now - at.timestamp() <= 1800:
            error = "Source is older than 30 minutes or undated; refresh and rebind the dataset"
        with store.transaction() as cx:
            user = cx.execute("SELECT * FROM users WHERE id=? AND enabled=1", (row["actor"],)).fetchone()
        if not user:
            error = "Schedule owner is disabled or missing"
        else:
            from .security import authorize_dataset
            try:
                authorize_dataset(store, {"departments": json.loads(user["departments"])}, row["dataset_id"])
                if user["role"] not in ({"admin", "lead"} if row["kind"] in {"audit", "insights"} else {"admin", "lead", "hr"}):
                    error = "Schedule owner no longer has workflow permission"
            except Exception:
                error = "Schedule owner no longer has dataset access"
        job_id = None
        if not error:
            # A deterministic due-slot key makes crash recovery and concurrent dispatch idempotent.
            job_id = runtime.submit(row["kind"], row["actor"], row["dataset_id"],
                                    store.unseal(row["options"]), f"schedule:{row['id']}:{row['next_due']}")
        with store.transaction() as cx:
            changed = cx.execute("""UPDATE schedules SET next_due=?,last_job=?,last_error=?
              WHERE id=? AND next_due=? AND enabled=1""",
                                 (now + row["interval_seconds"], job_id, error, row["id"], row["next_due"])).rowcount
            if changed:
                store.audit(cx, row["actor"], "schedule.blocked" if error else "schedule.dispatched", row["id"])
                if error:
                    alert = {"schedule_id": row["id"], "level": "source_refresh_required", "reason": error}
                    cx.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?)",
                               (digest(alert), row["dataset_id"], store.seal(alert), utcnow()))
    return len(rows)
