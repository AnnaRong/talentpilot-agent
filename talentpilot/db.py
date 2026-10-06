from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.ensure_dirs()
    connection = sqlite3.connect(settings.db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              goal TEXT NOT NULL,
              status TEXT NOT NULL,
              current_agent TEXT,
              input_path TEXT,
              output_path TEXT,
              result_json TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              agent TEXT NOT NULL,
              status TEXT NOT NULL,
              message TEXT NOT NULL,
              evidence_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
              id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              action TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              status TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS captures (
              id TEXT PRIMARY KEY,
              run_id TEXT,
              source TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recordings (
              id TEXT PRIMARY KEY,
              run_id TEXT,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              directory TEXT NOT NULL,
              video_path TEXT,
              manifest_path TEXT NOT NULL,
              events_path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              stopped_at TEXT
            );
            """
        )


def create_run(kind: str, goal: str, input_path: str | None) -> str:
    run_id = uuid.uuid4().hex
    timestamp = now()
    with db() as connection:
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, kind, goal, "queued", None, input_path, None, None, None, timestamp, timestamp),
        )
    return run_id


def update_run(run_id: str, **values: Any) -> None:
    values["updated_at"] = now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with db() as connection:
        connection.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?",
            (*values.values(), run_id),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "null")
        return result


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [get_run(row["id"]) for row in rows if get_run(row["id"]) is not None]


def add_step(run_id: str, agent: str, status: str, message: str, evidence: Any = None) -> str:
    step_id = uuid.uuid4().hex
    timestamp = now()
    with db() as connection:
        connection.execute(
            "INSERT INTO steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, run_id, agent, status, message, json.dumps(evidence, ensure_ascii=False), timestamp, timestamp),
        )
    return step_id


def list_steps(run_id: str) -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json") or "null")
        items.append(item)
    return items


def add_approval(run_id: str, action: str, payload: Any) -> str:
    approval_id = uuid.uuid4().hex
    with db() as connection:
        connection.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (approval_id, run_id, action, json.dumps(payload, ensure_ascii=False), "pending", "", now(), None),
        )
    return approval_id


def list_approvals(status: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM approvals"
    args: tuple[Any, ...] = ()
    if status:
        query += " WHERE status = ?"
        args = (status,)
    query += " ORDER BY created_at DESC"
    with db() as connection:
        rows = connection.execute(query, args).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        items.append(item)
    return items


def decide_approval(approval_id: str, decision: str, note: str) -> dict[str, Any] | None:
    with db() as connection:
        connection.execute(
            "UPDATE approvals SET status = ?, note = ?, decided_at = ? WHERE id = ?",
            (decision, note, now(), approval_id),
        )
        row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json"))
    return item


def save_capture(run_id: str | None, source: str, payload: Any) -> str:
    capture_id = uuid.uuid4().hex
    with db() as connection:
        connection.execute(
            "INSERT INTO captures VALUES (?, ?, ?, ?, ?)",
            (capture_id, run_id, source, json.dumps(payload, ensure_ascii=False), now()),
        )
    return capture_id
