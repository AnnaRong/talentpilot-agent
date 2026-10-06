from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


HEADERS = {
    "招聘台账": [
        "部门", "小组", "岗位名称", "岗位ID", "用人经理", "需求发布人", "筛选人",
        "候选人", "候选人ID", "当前阶段", "初筛结果", "一面结果", "二面结果",
        "三面结果", "终面结果", "Offer状态", "入职状态", "来源", "证据备注",
    ],
    "候选人流程": [
        "候选人", "候选人ID", "岗位名称", "岗位ID", "申请ID", "当前阶段",
        "状态", "来源", "申请时间", "最近更新时间", "证据链接或来源",
    ],
    "面试明细": [
        "候选人", "候选人ID", "岗位ID", "岗位名称", "轮次", "面试官",
        "面试时间", "状态", "结果", "是否进入通过率分母", "来源",
    ],
    "面评明细": [
        "候选人", "候选人ID", "岗位ID", "轮次", "面试官", "结果", "面评",
        "面评摘要", "证据状态", "来源",
    ],
    "Offer与入职": [
        "候选人", "候选人ID", "岗位ID", "岗位名称", "Offer状态", "Offer时间",
        "入职状态", "入职时间", "来源", "证据备注",
    ],
    "完整性与总结": ["指标", "数值", "口径", "完整性说明"],
    "审计日志": ["时间", "Agent", "动作", "状态", "证据", "错误或缺口"],
}


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _position_map(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in capture.get("positions") or []:
        key = _text(item, "job_id", "id", "position_id")
        if key:
            output[key] = item
    return output


def _candidate_row(candidate: dict[str, Any], positions: dict[str, dict[str, Any]]) -> list[str]:
    job_id = _text(candidate, "job_id", "position_id")
    position = positions.get(job_id, {})
    evidence_note = _text(candidate, "evidence_note", "note")
    recruiting_owner = _text(position, "recruiting_owner")
    if recruiting_owner:
        evidence_note = "; ".join(
            part for part in [evidence_note, f"页面招聘负责人={recruiting_owner}"] if part
        )
    return [
        _text(candidate, "department", "dept") or _text(position, "department", "dept"),
        _text(candidate, "group", "team"),
        _text(candidate, "job_name", "position_name", "job") or _text(position, "job_name", "position_name", "name"),
        job_id,
        _text(position, "hiring_manager", "owner", "用人经理"),
        _text(position, "publisher", "requester", "需求发布人"),
        _text(position, "screener", "screeners", "screening_owner", "筛选人"),
        _text(candidate, "name", "candidate_name"),
        _text(candidate, "candidate_id", "applicant_id", "id"),
        _text(candidate, "current_stage", "stage", "status"),
        _text(candidate, "screen_result", "initial_screen_result"),
        _text(candidate, "first_interview_result"),
        _text(candidate, "second_interview_result"),
        _text(candidate, "third_interview_result"),
        _text(candidate, "final_interview_result"),
        _text(candidate, "offer_status"),
        _text(candidate, "onboarding_status", "hire_status"),
        _text(candidate, "source"),
        evidence_note,
    ]


def build_workbook(capture: dict[str, Any], output_path: str | Path) -> Path:
    if "applications" in capture or "run" in capture:
        raise ValueError("Use scripts/build_verified_workbook.mjs for ledger/2; legacy export refused.")
    if not capture.get("candidates") and not capture.get("interviews"):
        raise ValueError("No candidate or interview evidence; empty workbook refused.")
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    positions = _position_map(capture)
    candidates = capture.get("candidates") or []
    candidate_map = {
        _text(item, "candidate_id", "applicant_id", "id"): item
        for item in candidates
        if _text(item, "candidate_id", "applicant_id", "id")
    }
    interviews = capture.get("interviews") or []
    offers = capture.get("offers") or []
    coverage = capture.get("coverage") or {}
    source_label = _text(capture.get("metadata") or {}, "source", "scope") or "未说明"
    workbook = Workbook()
    workbook.remove(workbook.active)
    rows: dict[str, list[list[str]]] = {
        "招聘台账": [_candidate_row(item, positions) for item in candidates],
        "候选人流程": [[
            _text(item, "name", "candidate_name"),
            _text(item, "candidate_id", "applicant_id", "id"),
            _text(item, "job_name", "position_name", "job")
            or _text(positions.get(_text(item, "job_id", "position_id"), {}), "job_name", "position_name", "name"),
            _text(item, "job_id", "position_id"),
            _text(item, "apply_id", "application_id"),
            _text(item, "current_stage", "stage"),
            _text(item, "status"),
            _text(item, "source"),
            _text(item, "applied_at", "created_at"),
            _text(item, "updated_at"),
            _text(item, "evidence_url", "source"),
        ] for item in candidates],
        "面试明细": [],
        "面评明细": [],
        "Offer与入职": [],
        "完整性与总结": [],
        "审计日志": [],
    }
    for item in interviews:
        candidate_id = _text(item, "candidate_id", "applicant_id")
        candidate = candidate_map.get(candidate_id, {})
        position = positions.get(_text(item, "job_id", "position_id"), {})
        result = _text(item, "result")
        completed_explicit = result in {"通过", "淘汰"}
        rows["面试明细"].append([
            _text(item, "candidate_name", "name") or _text(candidate, "name", "candidate_name"),
            candidate_id or _text(candidate, "candidate_id", "applicant_id", "id"),
            _text(item, "job_id", "position_id"),
            _text(item, "job_name", "position_name")
            or _text(candidate, "job_name", "position_name", "job")
            or _text(position, "job_name", "position_name", "name"),
            _text(item, "round", "phase", "stage"),
            _text(item, "interviewer", "interviewers"),
            _text(item, "scheduled_at", "interview_time"),
            _text(item, "status"),
            result,
            "是" if completed_explicit else "否",
            _text(item, "source", "evidence_url") or source_label,
        ])
        rows["面评明细"].append([
            _text(item, "candidate_name", "name") or _text(candidate, "name", "candidate_name"),
            candidate_id or _text(candidate, "candidate_id", "applicant_id", "id"),
            _text(item, "job_id", "position_id"),
            _text(item, "round", "phase", "stage"),
            _text(item, "interviewer", "interviewers"),
            result,
            _text(item, "feedback", "comment"),
            _text(item, "feedback_summary"),
            "已展示" if _text(item, "feedback", "comment", "feedback_summary") else "未展示",
            _text(item, "source", "evidence_url") or source_label,
        ])
    for item in offers:
        candidate_id = _text(item, "candidate_id", "applicant_id")
        candidate = candidate_map.get(candidate_id, {})
        position = positions.get(_text(item, "job_id", "position_id"), {})
        rows["Offer与入职"].append([
            _text(item, "candidate_name", "name") or _text(candidate, "name", "candidate_name"),
            candidate_id or _text(candidate, "candidate_id", "applicant_id", "id"),
            _text(item, "job_id", "position_id"),
            _text(item, "job_name", "position_name")
            or _text(candidate, "job_name", "position_name", "job")
            or _text(position, "job_name", "position_name", "name"),
            _text(item, "status", "offer_status"),
            _text(item, "offer_at", "created_at"),
            _text(item, "onboarding_status", "hire_status"),
            _text(item, "onboarding_at", "hire_at"),
            _text(item, "source", "evidence_url") or source_label,
            _text(item, "note", "evidence_note"),
        ])
    explicit_pass = sum(1 for item in interviews if _text(item, "result") == "通过")
    explicit_reject = sum(1 for item in interviews if _text(item, "result") == "淘汰")
    denominator = explicit_pass + explicit_reject
    rows["完整性与总结"] = [
        ["岗位数", str(len(capture.get("positions") or [])), "capture.positions 去重计数", coverage.get("positions", "未说明")],
        ["候选人数", str(len(candidates)), "capture.candidates 当前可见记录", coverage.get("candidates", "未说明")],
        ["面试记录数", str(len(interviews)), "包含已安排和历史记录", coverage.get("interviews", "未说明")],
        ["已发Offer数", str(sum(1 for item in offers if _text(item, "status", "offer_status") in {"已发放", "已发送", "sent"})), "只认明确已发送状态", coverage.get("offers", "未说明")],
        ["明确通过数", str(explicit_pass), "结果明确等于通过", "可核实"],
        ["明确淘汰数", str(explicit_reject), "结果明确等于淘汰", "可核实"],
        ["面试通过率", f"{explicit_pass / denominator:.2%}" if denominator else "", "通过 / (通过 + 淘汰)，未评价和未来预约不入分母", "可核实" if denominator else "不可用"],
        ["数据缺口", "; ".join(f"{k}: {v}" for k, v in coverage.items() if str(v).lower() not in {"complete", "完整", "available"}) or "未发现明确缺口", "原样保留来源覆盖状态", "需结合当前授权页面复核"],
        ["数据来源", source_label, "metadata.scope 或 metadata.source", "当前为 capture 导入；只有实时授权 iTalent 采集才代表租户当前数据"],
    ]
    rows["审计日志"] = [[
        _text(item, "created_at", "at"),
        _text(item, "agent"),
        _text(item, "action", "event"),
        _text(item, "status"),
        json.dumps(item.get("evidence") or {}, ensure_ascii=False),
        _text(item, "error", "gap"),
    ] for item in capture.get("audit_log", [])]

    for name, headers in HEADERS.items():
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows.get(name, []):
            sheet.append(row)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, sheet.max_row)}"
        header_fill = PatternFill("solid", fgColor="17365D")
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column in range(1, len(headers) + 1):
            letter = get_column_letter(column)
            max_length = max(
                [len(str(sheet.cell(row, column).value or "")) for row in range(1, sheet.max_row + 1)]
                + [len(headers[column - 1])]
            )
            sheet.column_dimensions[letter].width = min(max(max_length + 2, 12), 38)
        if sheet.max_row >= 2:
            ref = f"A1:{get_column_letter(len(headers))}{sheet.max_row}"
            table = Table(displayName=f"Table_{len(workbook.worksheets)}_{sheet.max_row}", ref=ref)
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2",
                showFirstColumn=False,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False,
            )
            sheet.add_table(table)
    workbook.save(output)
    return output


def build_ruliu_import_csv(capture: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    positions = _position_map(capture)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS["招聘台账"])
        writer.writerows(_candidate_row(item, positions) for item in capture.get("candidates") or [])
    return output
