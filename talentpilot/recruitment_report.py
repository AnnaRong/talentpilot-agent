"""Named recruitment tables from one authorized, immutable snapshot."""
from __future__ import annotations

from collections import defaultdict

from .verified_capture import validate_capture
from .workflows import completed_outcome

COLUMNS = ["岗位", "岗位编号", "用人经理", "筛选人", "hc", "Full", "面试官安排",
           "待入职+已入职", "面试人需求", "候选人", "一面", "一面淘汰", "二面",
           "二面淘汰", "三面", "三面淘汰", "Offer沟通中", "备注"]
ROUNDS = ("一面", "二面", "三面")
STAGES = ("简历初筛", "简历复筛", *ROUNDS, "Offer", "待入职", "已入职")
CANCELLED = {"已取消", "取消", "cancelled"}
REJECTED = {"淘汰", "本轮淘汰", "已淘汰"}
INACTIVE = REJECTED | CANCELLED | {"已放弃", "放弃", "已撤回", "已终止"}


def has_interviews(capture):
    coverage = capture.get("quality", {}).get("coverage", {}).get("interviews", {})
    return coverage.get("state") != "not_collected" and (
        bool(capture.get("interviews")) or coverage.get("state") == "complete")


def stage_of(app):
    stage, status = app.get("current_stage"), app.get("current_status")
    if status in INACTIVE:
        return None
    if stage == "入职":
        entry = status if status in {"待入职", "已入职"} else app.get("entry_status")
        return entry if entry in {"待入职", "已入职"} else None
    return stage if stage in STAGES and status in {"进行中", "待安排", "待面试", "已安排", "待入职", "已入职"} else None


def build_report(capture):
    validate_capture(capture)
    positions = {p["job_id"]: p for p in capture["positions"]}
    people = defaultdict(dict)
    for collection in ("applications", "interviews"):
        for record in capture[collection]:
            job_id, candidate_id = record["job_id"], record["candidate_id"]
            if job_id not in positions:
                continue
            person = people[job_id].setdefault(candidate_id, {
                "id": candidate_id, "name": "", "applications": [], "interviews": []})
            person["name"] = person["name"] or record.get("candidate_name") or ""
            person[collection].append(record)

    groups = []
    for job_id, position in positions.items():
        rows, panel = [], {r: {} for r in ROUNDS}
        stage_people = {s: set() for s in STAGES}
        round_people = {r: set() for r in ROUNDS}
        rejected_people = {r: set() for r in ROUNDS}
        hired = {}
        for candidate_id, person in people[job_id].items():
            name = person["name"] or f"姓名未采集（{candidate_id}）"
            apps = person["applications"]
            interviews = [i for i in person["interviews"] if i.get("status") not in CANCELLED]
            notes = []
            if not apps:
                notes.append("仅面试历史")
            # Multiple application records must not silently choose a current status.
            current = {(a.get("current_stage"), a.get("current_status"), a.get("entry_status")) for a in apps}
            if len(current) > 1:
                notes.append("多条申请状态不一致")
            else:
                for app in apps:
                    stage = stage_of(app)
                    if stage:
                        stage_people[stage].add(candidate_id)
                    if stage in {"待入职", "已入职"}:
                        hired[candidate_id] = f"{name}（{stage}）"
            cells = [name]
            for round_name in ROUNDS:
                own = [i for i in interviews if i.get("round") == round_name]
                current_round = [a for a in apps if a.get("current_stage") == round_name
                                 and a.get("current_status") not in CANCELLED]
                if own or current_round:
                    round_people[round_name].add(candidate_id)
                rejected = any(completed_outcome(i) and i.get("overall_result") == "淘汰" for i in own)
                rejected = rejected or any(a.get("current_status") in REJECTED for a in current_round)
                if rejected:
                    rejected_people[round_name].add(candidate_id)
                statuses = []
                for interview in own:
                    result = interview.get("overall_result") if completed_outcome(interview) else None
                    statuses.append(result or interview.get("status") or "结果未采集")
                    for interviewer in interview.get("interviewers", []):
                        key = interviewer.get("interviewer_id") or interviewer.get("name")
                        if key:
                            panel[round_name][str(key)] = interviewer.get("name") or f"姓名未采集（{key}）"
                statuses.extend(a.get("current_status") or "状态未采集" for a in current_round)
                statuses = list(dict.fromkeys(statuses))
                cells.extend([name + ("\n" + " / ".join(statuses) if statuses else "")
                              if own or current_round else "", name if rejected else ""])
            offer = candidate_id in stage_people["Offer"]
            if apps:
                notes.extend(dict.fromkeys(
                    f"{a.get('current_stage') or '阶段未采集'} · {a.get('current_status') or '状态未采集'}"
                    for a in apps))
            cells.extend([name if offer else "", "；".join(notes)])
            rows.append({"candidate_id": candidate_id, "name": name, "cells": cells,
                         "has_interview": any(candidate_id in round_people[r] for r in ROUNDS)})
        rows.sort(key=lambda r: (not r["has_interview"], r["name"], r["candidate_id"]))
        assignments = "\n".join(f"{r}：{'、'.join(panel[r].values()) or '未采集'}" for r in ROUNDS)
        shared = [position.get("title") or "岗位名称未采集", job_id,
                  position.get("hiring_manager"), position.get("screener"), position.get("hc"),
                  position.get("full"), assignments,
                  "\n".join(hired.values()) or None, position.get("interview_demand")]
        groups.append({"job_id": job_id, "title": shared[0], "shared": shared, "rows": rows,
                       "candidate_count": len(rows), "hired_count": len(hired),
                       "stages": {s: len(ids) for s, ids in stage_people.items()},
                       "rounds": {r: {"count": len(round_people[r]), "rejected": len(rejected_people[r])}
                                  for r in ROUNDS}})
    return {"columns": COLUMNS, "groups": groups, "stages": list(STAGES),
            "captured_at": capture["run"].get("completed_at"), "scope": capture["run"].get("scope"),
            "interviews_collected": has_interviews(capture),
            "coverage": capture.get("quality", {}).get("coverage", {}),
            "external_actions": 0}
