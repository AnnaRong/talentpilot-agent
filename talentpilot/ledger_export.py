"""Portable evidence tables for Excel/office import, without inferred hiring outcomes."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict

from .verified_capture import validate_capture
from .workflows import analytics, insights

MAIN = ["部门", "小组", "岗位", "岗位编号", "用人经理", "筛选人", "hc", "GAP",
        "面试官安排", "面试人需求", "候选人", "一面", "一面淘汰", "二面", "二面淘汰",
        "三面", "三面淘汰", "Offer沟通中", "待入职+已入职"]


def csv_bytes(headers, rows):
    target = io.StringIO(newline="")
    writer = csv.writer(target)
    writer.writerow(headers)
    for row in rows:
        # Spreadsheet formula injection must never be activated on import.
        writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                         else v for v in row])
    return target.getvalue().encode("utf-8-sig")


def tables(capture):
    validate_capture(capture)
    by_app = defaultdict(list)
    for interview in capture["interviews"]:
        by_app[(interview["application_id"], interview["candidate_id"], interview["job_id"])].append(interview)
    jobs = {p["job_id"]: p for p in capture["positions"]}
    rows = []
    for app in capture["applications"]:
        position = jobs.get(app["job_id"], {})
        own = by_app[(app["application_id"], app["candidate_id"], app["job_id"])]
        def evidence(round_name, rejected=False):
            items = [i for i in own if i.get("round") == round_name and
                     (not rejected or (i.get("status") == "已完成" and i.get("overall_result") == "淘汰"))]
            return "\n".join(f"{i.get('scheduled_at', '')} {i.get('status', '')}/{i.get('overall_result', '')} [{i['interview_id']}]"
                             for i in items) or "无已采集证据"
        rows.append([position.get("department", "未知"), position.get("group", "未采集"),
                     position.get("title", "岗位清单外"), app["job_id"], position.get("hiring_manager", "未采集"),
                     position.get("screener", "未采集"), position.get("hc", "未采集"), "未知",
                     "；".join(sorted({str(p["interviewer_id"]) for i in own for p in i.get("interviewers", [])
                                      if p.get("interviewer_id")})) or "未采集",
                     "未采集", app.get("candidate_name") or app["candidate_id"],
                     evidence("一面"), evidence("一面", True), evidence("二面"), evidence("二面", True),
                     evidence("三面"), evidence("三面", True),
                     app.get("current_status", "未知") if app.get("current_stage") == "Offer" else "非当前阶段",
                     app.get("current_status", "未知") if app.get("current_stage") == "入职" else "非当前阶段"])
    feedback = [e for group in insights(capture)["groups"] for e in group["evidence"]]
    report = analytics(capture)
    return {
        "招聘台账.csv": (MAIN, rows),
        "候选人流程.csv": (["申请ID", "候选人ID", "岗位编号", "当前阶段", "当前状态", "源页码"],
                      [[a["application_id"], a["candidate_id"], a["job_id"], a.get("current_stage"),
                        a.get("current_status"), a.get("source_page")] for a in capture["applications"]]),
        "面试明细.csv": (["安排ID", "申请ID", "候选人ID", "岗位编号", "轮次", "预约时间", "状态", "综合结论", "当前申请范围"],
                     [[i["interview_id"], i["application_id"], i["candidate_id"], i["job_id"],
                       i.get("round"), i.get("scheduled_at"), i.get("status"), i.get("overall_result"),
                       i.get("in_application_scope")] for i in capture["interviews"]]),
        "面评明细.csv": (["安排ID", "申请ID", "候选人ID", "岗位编号", "面试官ID", "个人结论", "面评正文", "评价ID"],
                     [[e["source"]["interview_id"], e["source"]["application_id"], e["source"]["candidate_id"],
                       e["source"]["job_id"], e["interviewer_id"], e["result"], e["quote"],
                       e["source"].get("evaluation_id")] for e in feedback]),
        "岗位统计.csv": (["岗位编号", "岗位", "部门", "已采集申请", "申请总量", "明确通过", "明确淘汰", "面试通过率"],
                     [[p["job_id"], p["title"], p["department"], p["captured_application_count"],
                       "未知", p["passed"], p["rejected"], p["pass_rate"]] for p in report["jobs"]]),
    }


def export_bundle(capture):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
        omitted = []
        for name, (headers, rows) in tables(capture).items():
            if not rows:
                omitted.append(name)
                continue
            archive.writestr(name, csv_bytes(headers, rows))
        archive.writestr("数据口径.json", json.dumps({
            "run": capture["run"], "quality": capture["quality"],
            "sent_offers": analytics(capture)["sent_offers"],
            "omitted_empty_evidence_tables": omitted,
            "notes": ["CSV可用Excel打开，不是xlsx；主表固定19列。",
                      "未采集不等于0；当前阶段不等于历史通过。", "每个申请一行；主表面试证据关联申请+候选人+岗位。"],
        }, ensure_ascii=False, indent=2))
    return result.getvalue()
