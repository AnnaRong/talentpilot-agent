"""Encrypted local resume version registry. Source identity is never guessed."""
from __future__ import annotations

import hashlib
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

from .platform_store import utcnow
from .resume import extract_text
from .workflows import parse_time


def initialize(store):
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS resume_versions (
          id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, version INTEGER NOT NULL,
          file_hash TEXT UNIQUE NOT NULL, text_hash TEXT NOT NULL,
          payload BLOB NOT NULL, created_at TEXT NOT NULL,
          UNIQUE(candidate_id,version), UNIQUE(candidate_id,text_hash))""")


def register(store, actor, capture, candidate_id, path):
    if candidate_id not in {a["candidate_id"] for a in capture["applications"]}:
        raise ValueError("Candidate ID must already exist in the authorized dataset")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError("Only regular resume files up to 10MB are accepted")
    raw = path.read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()
    text = extract_text(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != file_hash:
        raise ValueError("Resume changed during parsing; retry with an immutable file")
    normalized = " ".join(unicodedata.normalize("NFKC", text).split())
    if not normalized:
        raise ValueError("No extractable text; image/OCR needs separate authorized processing")
    text_hash = hashlib.sha256(normalized.encode()).hexdigest()
    with store.transaction() as cx:
        old = cx.execute("SELECT * FROM resume_versions WHERE file_hash=? OR text_hash=?",
                         (file_hash, text_hash)).fetchall()
        if any(row["candidate_id"] != candidate_id for row in old):
            store.audit(cx, actor, "resume.identity_conflict", candidate_id)
            return {"status": "identity_review_required", "merged": False, "external_actions": 0}
        if old:
            return {"status": "duplicate", "resume_id": old[0]["id"],
                    "version": old[0]["version"], "external_actions": 0}
        version = cx.execute("SELECT COALESCE(MAX(version),0)+1 FROM resume_versions WHERE candidate_id=?",
                             (candidate_id,)).fetchone()[0]
        resume_id = uuid.uuid4().hex
        cx.execute("INSERT INTO resume_versions VALUES (?,?,?,?,?,?,?)",
                   (resume_id, candidate_id, version, file_hash, text_hash,
                    store.seal({"text": text, "filename": path.name}), utcnow()))
        store.audit(cx, actor, "resume.version_registered", resume_id)
    return {"status": "registered", "resume_id": resume_id, "version": version,
            "sha256": file_hash, "external_actions": 0,
            "limitation": "Local encrypted copy only; original import file remains on disk"}


def evaluate_reapplication(capture, candidate_id, job_id, policy="standard", now=None):
    days = {"standard": 30, "technical": 60, "senior": 90}
    if policy not in days:
        raise ValueError("Unknown cooldown policy")
    if job_id not in {p["job_id"] for p in capture["positions"]}:
        raise ValueError("Exact job ID required")
    now = now or datetime.now(timezone.utc)
    rows = [a for a in capture["applications"]
            if a["candidate_id"] == candidate_id and a["job_id"] == job_id]
    # Explicit structured history is required. Current UI stage text is not a rejection/unlock event.
    if not rows:
        return {"status": "needs_history_review", "risks": ["No verified application history"], "can_submit": False}
    if any(a.get("status") == "blacklisted" for a in rows):
        return {"status": "blocked", "risks": ["Do-not-contact/application restriction"], "can_submit": False}
    rejected = [a for a in rows if a.get("status") == "rejected_unlocked"]
    if len(rejected) != len(rows):
        return {"status": "blocked_or_unknown", "risks": ["Active, locked or unverified application state"],
                "can_submit": False}
    times = [parse_time(a.get("rejected_at")) for a in rejected]
    if any(t is None or t > now for t in times) or any(a.get("unlocked") is not True for a in rejected):
        return {"status": "needs_history_review", "risks": ["Rejection time or unlock evidence missing"],
                "can_submit": False}
    if len(rejected) >= 3:
        return {"status": "special_approval_required", "rejections": len(rejected), "can_submit": False}
    cooldown = 180 if len(rejected) == 2 else days[policy]
    until = max(times) + timedelta(days=cooldown)
    return {"status": "cooldown_approval_required" if now < until else "resume_update_review_required",
            "cooldown_until": until.isoformat(), "cooldown_days": cooldown, "rejections": len(rejected),
            "can_submit": False, "requires": ["Current lock/duplicate/consent recheck",
                                            "Updated resume evidence", "Exact candidate/job/stage approval"]}
