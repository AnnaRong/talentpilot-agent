from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request

from .config import ROOT, settings


def login(store, user_id, secret):
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM users WHERE id=? AND enabled=1", (user_id,)).fetchone()
        provided = hashlib.sha256(secret.encode()).hexdigest()
        if not hmac.compare_digest(provided, row["secret_hash"] if row else "0" * 64):
            raise HTTPException(401, "Invalid local credentials")
        token = secrets.token_urlsafe(32)
        cx.execute("INSERT INTO sessions VALUES (?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 28800))
        store.audit(cx, user_id, "session.login", user_id)
        return token


def identity(request: Request):
    token = request.cookies.get("talentpilot_session", "")
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:]
    store = request.app.state.platform.store
    with store.transaction() as cx:
        row = cx.execute("""
          SELECT u.id,u.role,u.departments FROM sessions s JOIN users u ON u.id=s.user_id
          WHERE s.token_hash=? AND s.expires>? AND u.enabled=1
        """, (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    if not row:
        raise HTTPException(401, "Local login required")
    return {"id": row["id"], "role": row["role"], "departments": json.loads(row["departments"])}


def require(actor, roles):
    if actor["role"] not in roles:
        raise HTTPException(403, "Role not authorized")


def authorize_dataset(store, actor, dataset_id):
    capture = store.dataset(dataset_id)
    if "*" not in actor["departments"]:
        departments = {p.get("department") for p in capture["positions"]}
        if not departments.issubset(set(actor["departments"])):
            raise HTTPException(403, "Dataset crosses authorized department scope")
        known_jobs = {p["job_id"] for p in capture["positions"]}
        referenced_jobs = {row["job_id"] for key in ("applications", "interviews") for row in capture[key]}
        if not referenced_jobs.issubset(known_jobs):
            raise HTTPException(403, "Historical records lack verified department ownership")
    return capture


def safe_input(path: str) -> Path:
    source = Path(path).expanduser()
    if not source.is_absolute():
        source = ROOT / source
    source = source.resolve()
    allowed = [settings.data_dir / "imports", settings.data_dir / "live-captures", ROOT / "examples"]
    if not any(source.is_relative_to(root.resolve()) for root in allowed):
        raise HTTPException(403, "Input must be under data/imports, data/live-captures or examples")
    if not source.exists():
        raise HTTPException(404, "Input not found")
    return source
