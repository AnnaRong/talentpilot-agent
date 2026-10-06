from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from . import db, orchestrator
from .config import settings
from .exports import build_ruliu_import_csv, build_workbook
from .recording import recordings
from .reports import build_audit_report, save_report
from .verified_capture import capture_status, read_latest
from .schemas import (
    ApprovalDecision,
    BatchRequest,
    ITalentCapture,
    RecordingStart,
    RecordingStep,
    RecordingStop,
    RunCreate,
)
from .security import safe_input

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"ok": True, "agent": "iTalentPilot Agent", "version": "0.6.0"}


@router.get("/status")
def status():
    snapshot = capture_status(settings.data_dir / "live-captures")
    return {
        "agent": "iTalentPilot Agent",
        "snapshot": snapshot,
        "components": {
            "orchestrator": "durable_local_runtime",
            "sqlite": "ready",
            "italent": snapshot["state"],
            "italent_connection_mode": "authorized_browser_handoff_and_capture_import",
            "model": "default_audit_deterministic_optional_analyst",
            "recording": "event_log_only_video_unverified",
            "excel_export": "ready",
            "ruliu_publish": "connector_not_configured",
        },
    }


@router.get("/italent/verified")
def verified_snapshot():
    try:
        capture = read_latest(settings.data_dir / "live-captures")
    except (ValueError, OSError, TypeError, KeyError) as error:
        raise HTTPException(409, str(error))
    if capture is None:
        raise HTTPException(404, "No validated snapshot available.")
    return capture


@router.get("/exports/verified")
def verified_workbook(request: Request):
    capture = read_latest(settings.data_dir / "live-captures")
    if capture is None:
        raise HTTPException(404, "No validated snapshot available.")
    current_hash = hashlib.sha256((settings.data_dir / "live-captures" / "verified-latest.json").read_bytes()).hexdigest()
    matches = []
    for manifest_path in settings.exports_dir.glob("*/workbook-manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_sha256") == current_hash:
            matches.extend(manifest_path.parent.glob("*.xlsx"))
    if len(matches) != 1:
        raise HTTPException(409, "Exactly one source-matched verified workbook is required")
    path = matches[0].resolve()
    if not path.is_relative_to(settings.exports_dir.resolve()):
        raise HTTPException(403, "Export path outside allowed directory")
    store = request.app.state.platform.store
    with store.transaction() as cx:
        store.audit(cx, request.state.actor["id"], "verified_workbook.download", current_hash)
    return FileResponse(path, filename=path.name)


@router.post("/runs")
def create_run(payload: RunCreate):
    if payload.kind in {"recruitment_workflow", "paper_radar", "export_excel", "publish_ruliu"}:
        raise HTTPException(409, "Legacy placeholder disabled. Use /console for real workflows and connector gaps.")
    if payload.input_path:
        payload.input_path = str(safe_input(payload.input_path))
    if payload.candidate_path:
        payload.candidate_path = str(safe_input(payload.candidate_path))
    payload.options.pop("output_dir", None)
    return {"run_id": orchestrator.start(payload.model_dump()), "status": "queued"}


@router.get("/runs")
def list_runs():
    return db.list_runs()


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    result = orchestrator.run_details(run_id)
    if result is None:
        raise HTTPException(404, "run not found")
    return result


@router.get("/approvals")
def approvals(status: str | None = None):
    return db.list_approvals(status)


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, payload: ApprovalDecision):
    raise HTTPException(409, "Legacy approval lacks identity and payload binding. Recreate it in /console.")


@router.post("/italent/captures")
def import_capture(payload: ITalentCapture):
    capture = payload.model_dump()
    capture_id = db.save_capture(None, "authorized_browser_capture", capture)
    report = build_audit_report(capture)
    path = save_report(report, f"italent-capture-{capture_id}-audit.json")
    return {"capture_id": capture_id, "report_path": str(path), "report": report}


@router.post("/batch/plan")
def batch_plan(payload: BatchRequest):
    plan = orchestrator.build_batch_plan(**payload.model_dump())
    return plan


@router.post("/reports/from-file")
def report_from_file(path: str):
    source = safe_input(path)
    if not source.is_file():
        raise HTTPException(404, "capture file not found")
    capture = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(capture, dict):
        raise HTTPException(422, "Expected a structured evidence object")
    if capture.get("schema_version") != "talentpilot-ledger/2":
        ITalentCapture.model_validate(capture)
    report = build_audit_report(capture)
    output = save_report(report)
    return {"path": str(output), "report": report}


@router.post("/recordings/start")
def start_recording(payload: RecordingStart):
    return recordings.start(payload.task_id, "招聘操作录频")


@router.post("/recordings/{recording_id}/step")
def recording_step(recording_id: str, payload: RecordingStep):
    if payload.screenshot_path:
        raise HTTPException(409, "Arbitrary screenshot path disabled; verified task recording adapter required")
    try:
        return recordings.capture_step(
            recording_id,
            payload.action,
            payload.details,
            payload.screenshot_path,
        )
    except KeyError:
        raise HTTPException(404, "recording not found")
    except ValueError as error:
        raise HTTPException(409, str(error))


@router.post("/recordings/{recording_id}/stop")
def stop_recording(recording_id: str, payload: RecordingStop):
    try:
        return recordings.stop(recording_id, payload.status)
    except KeyError:
        raise HTTPException(404, "recording not found")


@router.get("/recordings")
def list_recordings():
    return recordings.list()


@router.get("/recordings/{recording_id}")
def get_recording(recording_id: str):
    result = recordings.get(recording_id)
    if result is None:
        raise HTTPException(404, "recording not found")
    return result


@router.post("/exports/excel")
def export_excel(payload: ITalentCapture):
    raise HTTPException(409, "Unverified legacy exporter retired. Use the source-matched verified workbook.")


@router.post("/exports/ruliu-csv")
def export_ruliu_csv(payload: ITalentCapture):
    raise HTTPException(409, "Legacy CSV lacks verified joins. Use scoped platform report export; Ruliu is not connected.")
