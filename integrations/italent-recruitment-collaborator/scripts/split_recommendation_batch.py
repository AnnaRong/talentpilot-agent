#!/usr/bin/env python3
"""Create a deterministic 8/12 iTalent recommendation plan without sending it.

The input may be a JSON array, {"candidates": [...]}, a JSONL file, or a
directory of resume files. The script intentionally does not call iTalent.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


def _natural_key(value: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _load(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        files = sorted(
            (item for item in path.iterdir() if item.is_file() and item.suffix.casefold() in {".pdf", ".docx", ".txt"}),
            key=lambda item: _natural_key(item.name),
        )
        return [{"source": str(item), "name": item.stem, "source_index": index + 1} for index, item in enumerate(files)]

    if path.suffix.casefold() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if path.suffix.casefold() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("candidates", data.get("items"))
    if not isinstance(data, list):
        raise ValueError("input must be a list or an object with candidates/items")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("each candidate must be an object")
    return data


def _candidate_id(item: dict[str, Any], index: int) -> str:
    for key in ("candidate_id", "applicant_id", "apply_id", "id", "source"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"input-{index:03d}"


def build_plan(
    items: list[dict[str, Any]],
    *,
    first_job_id: str,
    first_job_name: str,
    second_job_id: str,
    second_job_name: str,
    first_count: int,
    second_count: int,
) -> dict[str, Any]:
    expected = first_count + second_count
    if len(items) < expected:
        raise ValueError(f"need at least {expected} candidates, got {len(items)}")
    selected = items[:expected]
    ids = [_candidate_id(item, index + 1) for index, item in enumerate(selected)]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate identifiers are duplicated; provide stable unique IDs")

    def group(start: int, count: int, job_id: str, job_name: str, label: str) -> dict[str, Any]:
        members = []
        for offset, item in enumerate(selected[start : start + count], start=start + 1):
            members.append(
                {
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
                }
            )
        return {
            "label": label,
            "count": count,
            "job": {"job_id": job_id, "job_name": job_name},
            "members": members,
            "write_enabled": False,
        }

    return {
        "schema": "italent-recommendation-plan/v1",
        "created_by": "italent-recruitment-collaborator",
        "selection_rule": f"first {expected} input items; first {first_count} -> {first_job_id}; remaining {second_count} -> {second_job_id}",
        "input_count": len(items),
        "selected_count": expected,
        "groups": [
            group(0, first_count, first_job_id, first_job_name, "前八份"),
            group(first_count, second_count, second_job_id, second_job_name, "后十二份"),
        ],
        "next_step": "Use baidu-italent to resolve live apply/process/phase/status IDs, generate dry-run, obtain current-turn authorization, then execute and read back per candidate.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--first-job-id", default="J103039")
    parser.add_argument("--first-job-name", default="AI测试开发工程师")
    parser.add_argument("--second-job-id", default="J103043")
    parser.add_argument("--second-job-name", default="AI测试开发工程师")
    parser.add_argument("--first-count", type=int, default=8)
    parser.add_argument("--second-count", type=int, default=12)
    args = parser.parse_args()
    if args.first_count <= 0 or args.second_count <= 0:
        parser.error("counts must be positive")
    items = _load(args.input)
    plan = build_plan(
        items,
        first_job_id=args.first_job_id,
        first_job_name=args.first_job_name,
        second_job_id=args.second_job_id,
        second_job_name=args.second_job_name,
        first_count=args.first_count,
        second_count=args.second_count,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "recommendation-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for group in plan["groups"]:
        (args.output_dir / f"{group['label']}-{group['job']['job_id']}.json").write_text(
            json.dumps(group, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"output_dir": str(args.output_dir), "selected_count": plan["selected_count"], "groups": [g["count"] for g in plan["groups"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
