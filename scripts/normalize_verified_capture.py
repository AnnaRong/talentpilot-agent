from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from talentpilot.verified_capture import validate_capture


def normalize(root: Path) -> dict:
    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    jobs = read("positions.json")
    applications = read("applications.json")
    arrangements = read("interviews.json")
    feedback = read("feedback.json") if (root / "feedback.json").exists() else {"results": []}
    by_app = {a["application_id"]: a for a in applications["applications"]}
    positions = {p["job_id"]: p for p in jobs["positions"]}
    detail_by_key = {}
    detail_by_slot = {}
    inspected_apps = {r["application_id"] for r in feedback["results"] if "error" not in r}
    def slot_key(application_id, candidate_id, job_id, detail, person):
        return (
            application_id, candidate_id, job_id, detail["round"],
            detail["scheduled_at"].replace("/", "-").replace("T", " "),
            str(person["interviewer_id"]),
        )
    for result in feedback["results"]:
        for detail in result.get("details", []):
            for person in detail["interviewers"]:
                key = (
                    result["application_id"], result["candidate_id"], result["job_id"],
                    detail["interview_id"], str(person["interviewer_id"]),
                )
                if key in detail_by_key and detail_by_key[key] != person:
                    raise ValueError("Conflicting feedback evidence")
                detail_by_key[key] = person
                slot = slot_key(result["application_id"], result["candidate_id"], result["job_id"], detail, person)
                detail_by_slot.setdefault(slot, []).append({**person, "detail_interview_id": detail["interview_id"]})
    rows = []
    conflicts = []
    ambiguous = []
    used_details = set()
    now = datetime.now(timezone.utc)
    for interview in arrangements["interviews"]:
        if len(interview["applicants"]) != 1 or len(interview["job_ids"]) != 1:
            raise ValueError("Group interview needs explicit candidate-level mapping")
        applicant = interview["applicants"][0]
        job_id = interview["job_ids"][0]
        row = {k: v for k, v in interview.items() if k not in {"applicants", "job_ids"}}
        row.update(applicant)
        row["job_id"] = job_id
        row["in_application_scope"] = applicant["application_id"] in by_app
        row["in_current_job_scope"] = job_id in positions
        scheduled_at = datetime.fromisoformat(interview["scheduled_at"].replace("/", "-"))
        if scheduled_at.tzinfo is None:
            from zoneinfo import ZoneInfo
            scheduled_at = scheduled_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        row["rate_eligible"] = (
            interview["status"] == "已完成"
            and interview["overall_result"] in ("通过", "淘汰")
            and scheduled_at <= now
        )
        for person in row["interviewers"]:
            key = (
                applicant["application_id"], applicant["candidate_id"], job_id,
                interview["interview_id"], str(person["interviewer_id"]),
            )
            detail = detail_by_key.get(key)
            method = "exact_interview_id"
            if not detail:
                slot = slot_key(applicant["application_id"], applicant["candidate_id"], job_id, interview, person)
                matches = detail_by_slot.get(slot, [])
                if len(matches) == 1:
                    detail = matches[0]
                    method = "unique_application_candidate_job_round_time_interviewer"
                elif len(matches) > 1:
                    ambiguous.append(list(slot))
            if detail:
                detail_key = (*key[:3], detail.get("detail_interview_id", interview["interview_id"]), str(person["interviewer_id"]))
                if detail_key in used_details:
                    raise ValueError("One feedback record maps to multiple arrangements")
                used_details.add(detail_key)
                person["feedback"] = detail["feedback"] or "页面未展示面评正文"
                person["feedback_collected"] = True
                person["detail_result"] = detail["result"]
                person["detail_interview_id"] = detail.get("detail_interview_id", interview["interview_id"])
                person["join_method"] = method
                if detail["result"] and detail["result"] != person["result"]:
                    conflicts.append({"key": list(key), "list": person["result"], "detail": detail["result"]})
            else:
                person["feedback_collected"] = False
                if applicant["application_id"] in inspected_apps:
                    person["feedback"] = "详情未返回可唯一关联的该面试官面评"
                    person["detail_checked"] = True
        rows.append(row)
    source_times = [jobs["collected_at"], applications["collected_at"], arrangements["collected_at"]]
    if feedback.get("collected_at"):
        source_times.append(feedback["collected_at"])
    outside = [i for i in rows if not i["in_current_job_scope"]]
    body_count = sum(
        1 for i in rows for p in i["interviewers"]
        if p["feedback_collected"] and p["feedback"] != "页面未展示面评正文"
    )
    coverage = {
        "positions": {"declared": jobs["declared_total"], "captured": len(jobs["positions"]), "scope": jobs["scope"]},
        "applications": {"declared": applications["declared_total"], "captured": len(applications["applications"]), "scope": applications["scope"]},
        "interviews": {"declared": arrangements["declared_total"], "captured": len(rows), "scope": arrangements["scope"]},
        "feedback": {"requested_applications": feedback.get("requested"), "processed_applications": feedback.get("processed", 0), "text_records": body_count},
        "sent_offers": {"state": "not_collected", "count": None},
    }
    warnings = [
        "岗位清单含19个社招实习流程岗位和8个校园招聘岗位；申请清单仅覆盖社招实习流程。",
        "当前阶段存量不是历史进入量；初筛、复筛历史通过事件未完整采集，不能反推通过率。",
        "Offer进行中与已入职是申请当前状态；逐条Offer发送证据未采集，已发Offer数未知。",
        "用人经理、需求发布人和完整筛选人名单未采集；招聘负责人不能代替这些角色。",
        "面评原文忠实摘录并脱敏；不代表Agent对候选人的评价，评价时间字段未完整采集。",
        f"面试涉及的{feedback.get('processed', 0)}个申请详情已读取；部分详情未返回个人评价，取得{body_count}条非空正文。",
        "安排ID与详情面试ID是不同对象；详情仅在申请、候选人、岗位、轮次、时间、面试官ID全部一致且唯一时关联。",
        f"共有{len(outside)}场面试的岗位不在当前27岗位清单中，仍保留在明细中。",
        "本轮为多页面顺序快照；采集期间业务状态可能变化，不是数据库事务快照。",
    ]
    if conflicts:
        warnings.append(f"列表与详情个人结论存在{len(conflicts)}条差异，保留两份原始结果。")
    capture = {
        "schema_version": "talentpilot-ledger/2",
        "run": {
            "started_at": min(source_times), "completed_at": max(source_times),
            "validated_at": now.isoformat(),
            "viewer_name": "荣正亭", "viewer_source": "iTalent页面；如流身份未核验",
            "scope": "当前27岗位；社招实习流程1500申请；全部时间653面试，分口径汇总",
            "source": "授权浏览器只读页面与本次页面发出的读取接口",
        },
        "positions": jobs["positions"],
        "applications": applications["applications"],
        "interviews": rows,
        "offers": [],
        "quality": {
            "coverage": coverage, "warnings": warnings, "feedback_conflicts": conflicts,
            "ambiguous_feedback_joins": ambiguous,
            "pages": {"applications": applications["pages"], "interviews": arrangements["pages"]},
            "stage_counts": dict(Counter(a["current_stage"] + "|" + a["current_status"] for a in applications["applications"])),
        },
    }
    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]+", "[账号已脱敏]", value)
            return re.sub(r"\b1[3-9]\d{9}\b", "[电话已脱敏]", value)
        return value

    capture = redact(capture)
    validate_capture(capture)
    serialized = json.dumps(capture, ensure_ascii=False, indent=2)
    for forbidden in ("quark_s=", "bc_sign=", "userEmails", "idphoto", "applicantMobile", "<html"):
        if forbidden in serialized:
            raise ValueError(f"Forbidden private payload: {forbidden}")
    (root / "validated-capture.json").write_text(serialized + "\n", encoding="utf-8")
    (root.parent / "verified-latest.json").write_text(serialized + "\n", encoding="utf-8")
    return {
        "positions": len(capture["positions"]),
        "applications": len(capture["applications"]),
        "people": len({a["candidate_id"] for a in capture["applications"]}),
        "interviews": len(rows), "feedback_text": body_count,
        "eligible_results": dict(Counter(i["overall_result"] for i in rows if i["rate_eligible"])),
        "outside_current_jobs": len(outside), "feedback_conflicts": len(conflicts),
    }


if __name__ == "__main__":
    print(json.dumps(normalize(Path(sys.argv[1])), ensure_ascii=False, indent=2))
