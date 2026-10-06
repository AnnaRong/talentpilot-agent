"""Batch resume identity extraction and read-only iTalent history matching."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

from .resume import extract_text

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
MOBILE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?(1[3-9]\d{9})(?!\d)")
LABELLED_NAME = re.compile(
    r"(?:^|[\n|])\s*(?:姓名|name)\s*[:：]?\s*([\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z .'-]{1,50})", re.I
)


def _name(text: str, path: Path) -> str | None:
    match = LABELLED_NAME.search(text[:5000])
    if match:
        return " ".join(match.group(1).split()).strip(" -_")
    stem = re.sub(r"(?i)(个人)?简历|resume|cv|应聘|候选人", "", path.stem)
    stem = re.split(r"[_()（）【】\[\]-]\s*|\d{4,}", stem)[0].strip()
    if re.fullmatch(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z .'-]{1,50}", stem):
        return " ".join(stem.split())
    return None


def extract_identity(path: Path) -> dict:
    if path.suffix.lower() not in SUPPORTED or not path.is_file() or path.stat().st_size > 10_000_000:
        raise ValueError("仅支持不超过10MB的 PDF、Word、TXT 或 Markdown 简历")
    text = extract_text(path)
    emails = list(dict.fromkeys(value.casefold() for value in EMAIL.findall(text)))
    mobiles = list(dict.fromkeys(MOBILE.findall(text)))
    return {
        "file_id": hashlib.sha256(path.read_bytes()).hexdigest(),
        "filename": path.name,
        "name": _name(text, path),
        "email": emails[0] if emails else None,
        "mobile": mobiles[0] if mobiles else None,
        "identity_status": "strong" if emails or mobiles else "name_only" if _name(text, path) else "insufficient",
    }


def match_directory(directory: Path, search: Callable[[list[dict]], dict], target_job_id: str | None = None) -> dict:
    paths = sorted((p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED),
                   key=lambda p: p.name.casefold())
    if not paths:
        raise ValueError("没有可处理的简历")
    if len(paths) > 100:
        raise ValueError("单批最多处理100份简历")
    identities, errors, seen = [], [], set()
    for path in paths:
        try:
            identity = extract_identity(path)
            if identity["file_id"] in seen:
                identity["identity_status"] = "duplicate_file"
            else:
                seen.add(identity["file_id"])
            identities.append(identity)
        except Exception as error:
            errors.append({"filename": path.name, "status": "parse_failed", "reason": str(error)})
    searchable = [i for i in identities if i["identity_status"] not in {"insufficient", "duplicate_file"}]
    live = search(searchable) if searchable else {"results": [], "collected_at": None}
    by_file = {row["file_id"]: row for row in live.get("results", [])}
    results = []
    for identity in identities:
        found = by_file.get(identity["file_id"], {})
        records = found.get("applications", [])
        distinct = {r.get("candidate_id") for r in records if r.get("candidate_id")}
        basis = found.get("match_basis")
        if identity["identity_status"] == "duplicate_file":
            status = "duplicate_file"
        elif identity["identity_status"] == "insufficient":
            status = "identity_insufficient"
        elif basis == "name" and (len(distinct) != 1 or found.get("name_conflict")):
            status = "manual_review"
        elif records:
            status = "history_found"
        else:
            status = "not_found"
        same_job = bool(target_job_id and any(r.get("job_id") == target_job_id or
                        target_job_id in str(r.get("job_title") or "") for r in records))
        locked = any(r.get("locked") is True for r in records)
        decision = ("skip_same_job" if same_job else "skip_locked" if locked else
                    "review_identity" if status in {"manual_review", "identity_insufficient", "parse_failed"} else
                    "update_and_recommend" if status == "history_found" else "create_and_advance")
        results.append({
            "file_id": identity["file_id"], "filename": identity["filename"],
            "candidate_name": found.get("candidate_name") or identity.get("name"),
            "status": status, "match_basis": basis, "searched_by": found.get("searched_by"),
            "applications": records, "decision": decision,
        })
    return {
        "status": "completed", "files": len(paths), "parsed": len(identities),
        "history_found": sum(r["status"] == "history_found" for r in results),
        "manual_review": sum(r["status"] in {"manual_review", "identity_insufficient"} for r in results),
        "ready": sum(r["decision"] in {"update_and_recommend", "create_and_advance"} for r in results),
        "same_job_skipped": sum(r["decision"] == "skip_same_job" for r in results),
        "locked_skipped": sum(r["decision"] == "skip_locked" for r in results),
        "results": results + errors, "collected_at": live.get("collected_at"),
        "external_actions": 0,
        "limitation": "未查询到表示本次授权的候选人信息查询未返回记录，不等于全公司范围从未投递。",
    }
