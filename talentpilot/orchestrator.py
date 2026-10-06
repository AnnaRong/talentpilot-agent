from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import db
from .config import settings
from .exports import build_ruliu_import_csv, build_workbook
from .recording import recordings
from .reports import build_audit_report, save_report
from .resume import screen_directory

_threads: dict[str, threading.Thread] = {}


def _candidate_id(item: dict[str, Any], index: int) -> str:
    for key in ("candidate_id", "applicant_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Candidate {index} requires a stable candidate_id/applicant_id; names and filenames are not IDs")


def build_batch_plan(
    candidates: list[dict[str, Any]],
    first_job_id: str = "J103039",
    first_job_name: str = "AI测试开发工程师",
    second_job_id: str = "J103043",
    second_job_name: str = "AI测试开发工程师",
    first_count: int = 8,
    second_count: int = 12,
) -> dict[str, Any]:
    expected = first_count + second_count
    if len(candidates) < expected:
        raise ValueError(f"need at least {expected} candidates, got {len(candidates)}")
    selected = candidates[:expected]
    identifiers = [_candidate_id(item, index + 1) for index, item in enumerate(selected)]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("candidate identifiers are duplicated")

    def group(start: int, count: int, job_id: str, job_name: str, label: str) -> dict[str, Any]:
        members = []
        for offset, item in enumerate(selected[start:start + count], start=start + 1):
            members.append({
                "source_index": offset,
                "candidate_id": _candidate_id(item, offset),
                "candidate": item,
                "status": "needs_live_italent_context",
                "missing_before_write": [
                    "applicant_id",
                    "apply_id",
                    "phase_id/status_id from current job process",
                    "current state fingerprint",
                ],
            })
        return {
            "label": label,
            "count": count,
            "job": {"job_id": job_id, "job_name": job_name},
            "members": members,
            "write_enabled": False,
        }

    return {
        "schema": "talentpilot-recommendation-plan/v1",
        "selected_count": expected,
        "input_count": len(candidates),
        "groups": [
            group(0, first_count, first_job_id, first_job_name, "前八份"),
            group(first_count, second_count, second_job_id, second_job_name, "后十二份"),
        ],
        "next_step": "resolve live iTalent IDs, run duplicate/lock checks, obtain approval, then write and read back",
    }


def _load_json(path: str) -> dict[str, Any] | list[dict[str, Any]]:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def _step(run_id: str, agent: str, message: str, evidence: Any = None) -> None:
    db.update_run(run_id, current_agent=agent, status="running")
    db.add_step(run_id, agent, "completed", message, evidence)


def execute(run_id: str, payload: dict[str, Any]) -> None:
    try:
        kind = payload["kind"]
        db.update_run(run_id, status="running", current_agent="Orchestrator")
        _step(run_id, "Orchestrator", f"accepted {kind} task")
        if kind == "audit":
            input_path = payload.get("input_path")
            if not input_path:
                db.add_approval(run_id, "provide_italent_capture", {
                    "message": "请导入当前授权 iTalent 会话的 capture.json 后继续",
                    "required_fields": ["metadata", "positions", "candidates", "interviews", "offers", "coverage"],
                })
                db.update_run(run_id, status="awaiting_input", current_agent="iTalent Audit Agent")
                return
            capture = _load_json(input_path)
            if not isinstance(capture, dict):
                raise ValueError("Audit input must be a structured evidence object")
            if capture.get("schema_version") != "talentpilot-ledger/2":
                from .schemas import ITalentCapture
                ITalentCapture.model_validate(capture)
            _step(run_id, "iTalent Audit Agent", "loaded iTalent evidence", {
                "positions": len(capture.get("positions", [])),
                "applications": len(capture.get("applications", capture.get("candidates", []))),
                "interviews": len(capture.get("interviews", [])),
            })
            report = build_audit_report(capture)
            path = save_report(report, f"{run_id}-recruitment-audit.json")
            db.update_run(run_id, status="completed", current_agent="Report Agent",
                          output_path=str(path), result_json=json.dumps(report, ensure_ascii=False))
            return
        if kind == "recommendation_plan":
            input_path = payload.get("candidate_path") or payload.get("input_path")
            if not input_path:
                raise ValueError("candidate_path or input_path is required")
            data = _load_json(input_path)
            candidates = data.get("candidates", data) if isinstance(data, dict) else data
            plan = build_batch_plan(candidates, **payload.get("options", {}))
            output = settings.plans_dir
            output.mkdir(parents=True, exist_ok=True)
            plan_path = output / f"{run_id}-recommendation-plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _step(run_id, "Batch Validator", "validated stable candidate identifiers", {"count": plan["selected_count"]})
            _step(run_id, "Batch Planner", "created requested order split; no matching or safety checks performed", plan["groups"])
            approval_id = db.add_approval(run_id, "recommend_to_italent", {
                "plan_path": str(plan_path),
                "write_enabled": False,
                "required_before_write": ["live applicant_id/apply_id", "phase/status", "lock check", "read-back path"],
            })
            result = {**plan, "approval_id": approval_id, "plan_path": str(plan_path)}
            db.update_run(run_id, status="awaiting_approval", current_agent="Approval Agent",
                          output_path=str(plan_path), result_json=json.dumps(result, ensure_ascii=False))
            return
        if kind == "resume_screen":
            directory = payload.get("input_path")
            keywords = payload.get("options", {}).get("keywords", [])
            result = screen_directory(directory, keywords)
            path = save_report(result, f"{run_id}-resume-screen.json")
            _step(run_id, "Resume Parser Agent", "parsed and hashed local resumes", {"count": result["count"]})
            _step(run_id, "CIAD Agent", "deduplicated resumes by SHA-256")
            db.update_run(run_id, status="completed", current_agent="Report Agent",
                          output_path=str(path), result_json=json.dumps(result, ensure_ascii=False))
            return
        if kind == "paper_radar":
            db.update_run(run_id, status="awaiting_input", current_agent="Paper Radar Agent",
                          result_json=json.dumps({"status": "awaiting_source_adapter"}, ensure_ascii=False))
            return
        if kind == "recruitment_workflow":
            raise ValueError("Descriptive-only workflow retired. Use the persistent /api/platform/jobs runtime.")
        raise ValueError(f"unsupported run kind: {kind}")
    except Exception as error:
        db.update_run(run_id, status="failed", error=str(error), current_agent="Evaluator")


def start(payload: dict[str, Any]) -> str:
    run_id = db.create_run(payload["kind"], payload["goal"], payload.get("input_path") or payload.get("candidate_path"))
    thread = threading.Thread(target=execute, args=(run_id, payload), daemon=True)
    _threads[run_id] = thread
    thread.start()
    return run_id


def run_details(run_id: str) -> dict[str, Any] | None:
    run = db.get_run(run_id)
    if run is None:
        return None
    run["steps"] = db.list_steps(run_id)
    return run


def wait_for_run(run_id: str, timeout: float = 60.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    terminal = {"completed", "failed", "awaiting_input", "awaiting_approval"}
    while time.monotonic() < deadline:
        result = run_details(run_id)
        if result is None or result["status"] in terminal:
            return result
        time.sleep(0.05)
    return run_details(run_id)
