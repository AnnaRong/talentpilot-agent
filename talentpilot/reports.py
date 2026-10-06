from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import settings
from .verified_capture import validate_capture


def _count_explicit(items: list[dict[str, Any]], result: str) -> int:
    return sum(1 for item in items if str(item.get("result", "")).strip() == result)


def build_audit_report(capture: dict[str, Any]) -> dict[str, Any]:
    if capture.get("schema_version") == "talentpilot-ledger/2":
        validate_capture(capture)
        interviews = capture["interviews"]
        eligible = [row for row in interviews if row.get("rate_eligible")]
        passed = sum(row["overall_result"] == "通过" for row in eligible)
        rejected = sum(row["overall_result"] == "淘汰" for row in eligible)
        return {
            "schema": "talentpilot-audit/v2",
            "metadata": capture["run"],
            "summary": {
                "position_count": len(capture["positions"]),
                "application_count": len(capture["applications"]),
                "candidate_count": len({row["candidate_id"] for row in capture["applications"]}),
                "interview_count": len(interviews),
                "completed_interview_count": sum(row["status"] == "已完成" for row in interviews),
                "explicit_pass_count": passed,
                "explicit_reject_count": rejected,
                "pass_rate": passed / (passed + rejected) if passed + rejected else None,
                "offer_count": None,
                "offer_count_status": "not_collected",
                "feedback_text_count": capture["quality"]["coverage"]["feedback"]["text_records"],
            },
            "coverage": capture["quality"]["coverage"],
            "gaps": capture["quality"]["warnings"],
        }
    if "applications" in capture or "run" in capture:
        raise ValueError("Unrecognized ledger schema; refusing to generate an empty report.")
    positions = capture.get("positions") or []
    candidates = capture.get("candidates") or []
    interviews = capture.get("interviews") or []
    offers = capture.get("offers") or []
    coverage = capture.get("coverage") or {}
    completed = [
        item for item in interviews
        if str(item.get("status", "")).lower() in {"completed", "已完成", "通过", "淘汰"}
    ]
    passed = _count_explicit(completed, "通过")
    rejected = _count_explicit(completed, "淘汰")
    denominator = passed + rejected
    report = {
        "schema": "talentpilot-audit/v1",
        "metadata": capture.get("metadata") or {},
        "summary": {
            "position_count": len(positions),
            "candidate_count": len(candidates),
            "interview_count": len(interviews),
            "completed_interview_count": len(completed),
            "scheduled_interview_count": sum(
                1 for item in interviews if str(item.get("status", "")).lower() in {"scheduled", "已安排", "未评价"}
            ),
            "offer_count": sum(
                1 for item in offers if str(item.get("status", "")).lower() in {"sent", "已发放", "已发送"}
            ) if coverage.get("offers") in {"complete", "完整"} else None,
            "explicit_pass_count": passed,
            "explicit_reject_count": rejected,
            "pass_rate": passed / denominator if denominator else None,
        },
        "positions": positions,
        "candidate_journeys": candidates,
        "interviews": interviews,
        "offers": offers,
        "feedback": {
            "rejected": [item for item in interviews if item.get("result") == "淘汰"],
            "passed": [item for item in interviews if item.get("result") == "通过"],
            "not_displayed": [
                item for item in interviews
                if not item.get("feedback") and not item.get("feedback_summary")
            ],
        },
        "coverage": coverage,
        "gaps": [
            f"{key}: {value}"
            for key, value in coverage.items()
            if str(value).lower() not in {"complete", "完整", "available"}
        ],
    }
    return report


def save_report(report: dict[str, Any], name: str = "recruitment-audit.json") -> Path:
    settings.ensure_dirs()
    path = settings.reports_dir / name
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
