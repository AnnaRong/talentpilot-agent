"""Encrypted, expiring upload previews. Execution stays blocked until live writes are verified."""
from __future__ import annotations

import time
import uuid

from .platform_store import digest, utcnow

TTL = 1800


def initialize(store):
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS upload_batches (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, job_id TEXT NOT NULL, job_title TEXT NOT NULL,
          department TEXT NOT NULL, payload BLOB NOT NULL, payload_hash TEXT NOT NULL,
          status TEXT NOT NULL, expires REAL NOT NULL, created_at TEXT NOT NULL, result BLOB)""")


def create(store, actor, job, result, files):
    eligible = {row["file_id"] for row in result["results"]
                if row.get("decision") in {"update_and_recommend", "create_and_advance"}}
    selected = [{"file_id": item["file_id"], "name": item["name"],
                 "content_base64": item["content_base64"]} for item in files if item["file_id"] in eligible]
    payload = {
        "job": job, "target_stage": "简历复筛/进行中", "channel": "BOSS直聘",
        "acquisition": "主动投递", "candidates": result["results"], "files": selected,
    }
    batch_id = uuid.uuid4().hex
    fingerprint = digest({"batch_id": batch_id, "job": job, "file_ids": sorted(eligible),
                          "target_stage": payload["target_stage"]})
    with store.transaction() as cx:
        cx.execute("INSERT INTO upload_batches VALUES (?,?,?,?,?,?,?,'preview',?,?,NULL)",
                   (batch_id, actor, job["job_id"], job["title"], job.get("department") or "",
                    store.seal(payload), fingerprint, time.time() + TTL, utcnow()))
        store.audit(cx, actor, "resume_upload.preview", batch_id)
    return {"batch_id": batch_id, "fingerprint": fingerprint, "expires_in": TTL}


def execute(store, actor, batch_id, expected_fingerprint):
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM upload_batches WHERE id=?", (batch_id,)).fetchone()
        if not row or row["actor"] != actor or row["status"] != "preview" or row["expires"] < time.time():
            raise ValueError("上传预览不存在、已过期或已执行")
        if row["payload_hash"] != expected_fingerprint:
            raise ValueError("上传名单或目标岗位已变化，请重新核验")
        payload = store.unseal(row["payload"])
        result = {
            "status": "blocked", "external_actions": 0,
            "reason": "北森新建应聘者、更新既有简历及转入简历复筛的写入契约尚未完成受控验真；为避免错误建档，本批次未上传。",
            "job_id": row["job_id"], "target_stage": payload["target_stage"],
            "candidate_count": len(payload["files"]),
        }
        cx.execute("UPDATE upload_batches SET status='blocked',result=? WHERE id=?",
                   (store.seal(result), batch_id))
        store.audit(cx, actor, "resume_upload.blocked_unverified_connector", batch_id)
    return result
