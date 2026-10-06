from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def validate_capture(capture: dict[str, Any]) -> None:
    if capture.get("schema_version") != "talentpilot-ledger/2":
        raise ValueError("Expected talentpilot-ledger/2; legacy captures are not verified.")
    if not capture.get("positions") or not (capture.get("applications") or capture.get("interviews")):
        raise ValueError("Empty evidence is not a verified recruitment dataset.")
    for collection, fields in (
        ("positions", ("job_uuid", "job_id")),
        ("applications", ("application_id",)),
        ("interviews", ("interview_id", "application_id", "candidate_id")),
    ):
        rows = capture.get(collection)
        if not isinstance(rows, list):
            raise ValueError(f"Missing collection: {collection}")
        keys = [tuple(row.get(field) for field in fields) for row in rows]
        if any(not all(key) for key in keys) or len(set(keys)) != len(keys):
            raise ValueError(f"Missing or duplicate stable keys: {collection}")
    positions = {row["job_id"]: row["job_uuid"] for row in capture["positions"]}
    applications = {}
    for row in capture["applications"]:
        if not all(row.get(key) for key in ("candidate_id", "job_id", "job_uuid")):
            raise ValueError("Application identity is incomplete.")
        if row["job_id"] in positions and positions[row["job_id"]] != row["job_uuid"]:
            raise ValueError("Job code and UUID disagree.")
        applications[row["application_id"]] = row
    for row in capture["interviews"]:
        application = applications.get(row["application_id"])
        if application and any(row.get(k) != application[k] for k in ("candidate_id", "job_id")):
            raise ValueError("Interview identity disagrees with application.")
    coverage = capture.get("quality", {}).get("coverage", {})
    for key, rows in (("positions", capture["positions"]), ("applications", capture["applications"])):
        if coverage.get(key, {}).get("declared") != len(rows):
            raise ValueError(f"Incomplete declared coverage: {key}")
    declared = coverage.get("interviews", {}).get("declared")
    if declared != len({row["interview_id"] for row in capture["interviews"]}):
        raise ValueError("Incomplete interview arrangement coverage.")


def read_latest(root: Path) -> dict[str, Any] | None:
    marker = root / "verified-latest.json"
    if not marker.is_file():
        return None
    capture = json.loads(marker.read_text(encoding="utf-8"))
    validate_capture(capture)
    return capture


def capture_status(root: Path) -> dict[str, Any]:
    try:
        capture = read_latest(root)
    except (ValueError, OSError, TypeError, KeyError) as error:
        return {"state": "validation_failed", "reason": str(error)}
    if capture is None:
        return {"state": "not_verified", "live_connection": False}
    captured_at = capture["run"]["completed_at"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(captured_at)).total_seconds()
    return {
        "state": "verified_snapshot" if 0 <= age <= 86400 else "stale_snapshot",
        "live_connection": False,
        "captured_at": captured_at,
        "scope": capture["run"]["scope"],
        "coverage": capture["quality"]["coverage"],
        "warnings": capture["quality"]["warnings"],
    }
