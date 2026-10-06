"""Local durable state. Existing evidence files are never rewritten by this store."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def private_write(path: Path, data: bytes):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        keypath = root / "platform.key"
        if not keypath.exists():
            try:
                private_write(keypath, Fernet.generate_key())
            except FileExistsError:
                pass
        self.cipher = Fernet(keypath.read_bytes())
        self.path = root / "platform.sqlite3"
        with self.transaction() as cx:
            cx.executescript("""
              CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL, role TEXT NOT NULL,
                departments TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY, payload BLOB NOT NULL, created_at TEXT NOT NULL,
                scope TEXT NOT NULL, kind TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS entities (
                dataset_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                payload BLOB NOT NULL, PRIMARY KEY(dataset_id,kind,id));
              CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, actor TEXT NOT NULL, dataset_id TEXT NOT NULL,
                payload BLOB NOT NULL, status TEXT NOT NULL, result BLOB,
                error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL, budget INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0, timeout_seconds INTEGER NOT NULL,
                lease_until REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, type TEXT NOT NULL,
                agent TEXT NOT NULL, payload BLOB NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS memories (
                agent TEXT NOT NULL, subject TEXT NOT NULL, payload BLOB NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(agent,subject));
              CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL,
                dataset_id TEXT NOT NULL, payload BLOB NOT NULL, payload_hash TEXT NOT NULL,
                status TEXT NOT NULL, expires REAL NOT NULL, decided_by TEXT,
                decision_note TEXT, created_at TEXT NOT NULL, result BLOB);
              CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, payload BLOB NOT NULL,
                created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS consents (
                id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, payload BLOB NOT NULL,
                expires REAL NOT NULL, status TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS private_records (
                id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, consent_id TEXT NOT NULL,
                payload BLOB NOT NULL, expires REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS overseas_resumes (
                id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, job_id TEXT NOT NULL,
                job_title TEXT NOT NULL, source TEXT NOT NULL, content_hash TEXT NOT NULL,
                payload BLOB NOT NULL, expires REAL NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(job_id, candidate_id, content_hash));
              CREATE TABLE IF NOT EXISTS boss_plans (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, job_id TEXT NOT NULL,
                payload BLOB NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS email_collection_runs (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, job_id TEXT NOT NULL,
                payload BLOB NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS paper_radar_runs (
                id TEXT PRIMARY KEY, actor TEXT NOT NULL, payload BLOB NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                action TEXT NOT NULL, target TEXT NOT NULL, at TEXT NOT NULL);
            """)
        self.path.chmod(0o600)
        self.bootstrap_design_actor()

    @contextmanager
    def transaction(self):
        cx = sqlite3.connect(self.path, timeout=20)
        cx.row_factory = sqlite3.Row
        try:
            cx.execute("PRAGMA journal_mode=WAL")
            cx.execute("BEGIN IMMEDIATE")
            yield cx
            cx.commit()
        except BaseException:
            cx.rollback()
            raise
        finally:
            cx.close()

    def seal(self, value):
        return self.cipher.encrypt(canonical(value).encode())

    def unseal(self, value):
        return json.loads(self.cipher.decrypt(value)) if value else None

    def bootstrap_design_actor(self):
        with self.transaction() as cx:
            cx.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,1)",
                       ("local-admin", "design-mode-no-password", "admin", '["*"]'))

    def audit(self, cx, actor, action, target):
        cx.execute("INSERT INTO audit(actor,action,target,at) VALUES (?,?,?,?)",
                   (actor, action, target, utcnow()))

    def event(self, cx, job, kind, agent, payload):
        cx.execute("INSERT INTO events(job_id,type,agent,payload,created_at) VALUES (?,?,?,?,?)",
                   (job, kind, agent, self.seal(payload), utcnow()))

    def dataset(self, dataset_id):
        with self.transaction() as cx:
            row = cx.execute("SELECT payload FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise ValueError("Dataset not found")
        return self.unseal(row["payload"])

    def import_dataset(self, capture, actor, kind="verified"):
        from .verified_capture import validate_capture
        validate_capture(capture)
        dataset_id = digest(capture)
        with self.transaction() as cx:
            cx.execute("INSERT OR IGNORE INTO datasets VALUES (?,?,?,?,?)",
                       (dataset_id, self.seal(capture), utcnow(),
                        str(capture.get("run", {}).get("scope", "unspecified")), kind))
            collections = (("positions", "job_id"), ("applications", "application_id"),
                           ("interviews", "interview_id"))
            for collection, key in collections:
                for row in capture[collection]:
                    cx.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?,?)",
                               (dataset_id, collection, str(row[key]), self.seal(row)))
            for candidate_id in {str(r["candidate_id"]) for r in capture["applications"]}:
                cx.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?,?)",
                           (dataset_id, "candidates", candidate_id,
                            self.seal({"candidate_id": candidate_id, "source": "application_identity"})))
            self.audit(cx, actor, "dataset.import", dataset_id)
        return dataset_id
