"""Pure, evidence-backed workflows; never writes to a recruiting system."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def analytics(capture):
    stages = Counter((a["job_id"], a.get("current_stage", "unknown"),
                      a.get("current_status", "unknown")) for a in capture["applications"])
    jobs = []
    for position in capture["positions"]:
        rows = [i for i in capture["interviews"]
                if i["job_id"] == position["job_id"] and i.get("in_application_scope") is True]
        eligible = [i for i in rows if completed_outcome(i)]
        passed = sum(i["overall_result"] == "通过" for i in eligible)
        rounds = {}
        for round_name in sorted({str(i.get("round", "unknown")) for i in rows}):
            group = [i for i in eligible if str(i.get("round", "unknown")) == round_name]
            rp = sum(i["overall_result"] == "通过" for i in group)
            rounds[round_name] = {"passed": rp, "rejected": len(group) - rp,
                                  "pass_rate": rp / len(group) if group else None}
        application_count = sum(a["job_id"] == position["job_id"] for a in capture["applications"])
        jobs.append({
            "job_id": position["job_id"], "title": position.get("title"),
            "department": position.get("department"),
            "applications": application_count if application_count else None,
            "captured_application_count": application_count,
            "application_coverage": "observed_records_only" if application_count else "not_observed",
            "application_population_total": None,
            "completed_explicit": len(eligible), "passed": passed,
            "rejected": len(eligible) - passed,
            "pass_rate": passed / len(eligible) if eligible else None, "rounds": rounds,
        })
    offer_coverage = capture.get("quality", {}).get("coverage", {}).get("offers", {})
    complete = isinstance(offer_coverage, dict) and offer_coverage.get("state") == "complete"
    offers = capture.get("offers", [])
    return {
        "status": "partial" if not capture["interviews"] else "evaluated_snapshot",
        "method": "deterministic", "source_scope": capture.get("run", {}).get("scope"),
        "captured_at": capture.get("run", {}).get("completed_at"),
        "jobs": jobs,
        "stage_inventory": [{"job_id": j, "stage": s, "status": t, "count": n}
                            for (j, s, t), n in sorted(stages.items())],
        "sent_offers": sum(o.get("status") in {"sent", "已发送", "已发放"} for o in offers) if complete else None,
        "historical_screening_pass_rate": None,
        "missing": ["Historical screening events not inferred from current stage",
                    "Offer inventory is not evidence of sent Offers"],
    }


def completed_outcome(interview):
    if interview.get("status") != "已完成" or interview.get("overall_result") not in {"通过", "淘汰"}:
        return False
    # Validated legacy captures include the time check; new captures have timezone-aware timestamps.
    if interview.get("rate_eligible") is False:
        return False
    at = parse_time(interview.get("scheduled_at"))
    if at is not None:
        return at <= datetime.now(timezone.utc)
    return interview.get("rate_eligible") is True


def insights(capture):
    groups, distributions, missing_text = [], defaultdict(Counter), 0
    placeholders = {"--", "-", "未展示", "未采集", "页面未展示面评正文", "null", "None"}
    for interview in capture["interviews"]:
        evidence = []
        for person in interview.get("interviewers", []):
            text = str(person.get("feedback") or "").strip()
            if not text or text in placeholders:
                text = ""
                missing_text += 1
            result = person.get("detail_result") or person.get("result")
            interviewer_id = person.get("interviewer_id")
            if not interviewer_id:
                continue
            if interview.get("status") == "已完成" and result in {"通过", "淘汰"}:
                distributions[str(interviewer_id)][result] += 1
            if text and person.get("feedback_collected") is True:
                evidence.append({
                    "interviewer_id": interviewer_id, "result": result, "quote": text,
                    "source": {"interview_id": interview["interview_id"],
                               "application_id": interview["application_id"],
                               "candidate_id": interview["candidate_id"], "job_id": interview["job_id"],
                               "evaluation_id": person.get("evaluation_id"),
                               "detail_interview_id": person.get("detail_interview_id")},
                })
        explicit = {e["result"] for e in evidence if e["result"] in {"通过", "淘汰"}}
        if evidence:
            groups.append({
                "interview_id": interview["interview_id"],
                "application_id": interview["application_id"],
                "classification": "disagreement" if len(explicit) > 1 else
                                  "consensus" if len(evidence) > 1 and len(explicit) == 1 and
                                  all(e["result"] in explicit for e in evidence) else "insufficient_evidence",
                "evidence": evidence,
            })
    return {
        "method": "deterministic_evidence_comparison", "audience": "recruitment_lead",
        "groups": groups,
        "missing_or_placeholder_feedback": missing_text,
        "multi_interviewer_groups": sum(len(g["evidence"]) > 1 for g in groups),
        "comparison_status": "available" if any(len(g["evidence"]) > 1 for g in groups)
                             else "insufficient_multi_interviewer_evidence",
        "distributions": [{"interviewer_id": k, "passed": v["通过"], "rejected": v["淘汰"],
                           "pass_rate": v["通过"] / sum(v.values())} for k, v in distributions.items()],
        "limitations": ["No causal or bias labels", "No cross-job interviewer ranking",
                       "Free-text skill/experience consensus requires human calibration",
                       "Zero detected disagreements is not evidence of agreement when comparisons are unavailable"],
    }


def locks(capture, now=None):
    now = now or datetime.now(timezone.utc)
    if "locks" not in capture:
        return {"status": "awaiting_source", "alerts": [], "missing": ["locks and lock history"]}
    alerts, fresh = [], 0
    for lock in capture["locks"]:
        if not all(lock.get(k) for k in ("lock_id", "candidate_id", "job_id", "observed_at")):
            continue
        observed = parse_time(lock["observed_at"])
        if not observed or not 0 <= (now - observed).total_seconds() <= 1800:
            continue
        fresh += 1
        expires = parse_time(lock.get("expires_at"))
        state, level = lock.get("state"), None
        if state in {"released", "transferred"}:
            level = state
        elif state == "locked" and expires:
            hours = (expires - now).total_seconds() / 3600
            level = "expired" if hours <= 0 else "1h" if hours <= 1 else "24h" if hours <= 24 else None
        if level:
            alerts.append({
                "lock_id": lock["lock_id"], "candidate_id": lock["candidate_id"],
                "job_id": lock["job_id"], "level": level, "expires_at": lock.get("expires_at"),
                "revision": lock.get("revision") or lock.get("expires_at") or lock["observed_at"],
                "escalate_to": "recruitment_lead" if lock.get("final_scheduled") is False else "hr",
            })
    return {"status": "evaluated_snapshot" if fresh else "awaiting_source", "alerts": alerts,
            "fresh_lock_observations": fresh,
            "limitation": "Only observations <=30 minutes old; not a live lock subscription"}


def recommend(capture, options):
    job_id = options.get("job_id")
    if job_id not in {p["job_id"] for p in capture["positions"]}:
        raise ValueError("Exact target job_id required")
    evidence_by_id = {c["candidate_id"]: c for c in capture.get("talent_pool", [])}
    results = []
    for candidate_id, c in evidence_by_id.items():
        reasons = []
        for key, expected in (("locked", False), ("in_process", False), ("blacklisted", False),
                              ("consent_to_contact", True)):
            if c.get(key) is not expected:
                reasons.append(f"{key}: blocked or unknown")
        observed = parse_time(c.get("checked_at"))
        if not observed or not 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 900:
            reasons.append("Safety observations missing or older than 15 minutes")
        if any(a["candidate_id"] == candidate_id and a["job_id"] == job_id
               for a in capture["applications"]):
            reasons.append("Existing application; require explicit history/cooldown review")
        facts = [f for f in c.get("role_evidence", []) if f.get("source_id") and f.get("quote")]
        if not facts:
            reasons.append("No sourced job-relevant evidence")
        results.append({"candidate_id": candidate_id, "job_id": job_id,
                        "status": "needs_review" if reasons else "eligible_for_human_review",
                        "risks": reasons, "evidence": facts,
                        "draft": f"您好，我们正在招聘 {job_id}。方便了解您的求职意向吗？" if not reasons else None})
    return {"status": "prepared" if results else "awaiting_source",
            "recommendations": results, "external_actions": 0,
            "ranking": "No automated hiring ranking; recruiter reviews sourced role evidence"}


def loads(capture, options):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(options.get("timezone", "Asia/Shanghai"))
    day = options.get("date", datetime.now(tz).date().isoformat())
    counts, missing, assumed = defaultdict(set), 0, 0
    for row in capture["interviews"]:
        if row.get("status") in {"已取消", "取消", "cancelled"}:
            continue
        at = parse_time(row.get("scheduled_at"))
        if at is None and options.get("source_timezone"):
            try:
                at = datetime.strptime(str(row.get("scheduled_at")), "%Y/%m/%d %H:%M:%S").replace(
                    tzinfo=ZoneInfo(options["source_timezone"]))
                assumed += 1
            except ValueError:
                pass
        if at is None:
            missing += 1
            continue
        if at.astimezone(tz).date().isoformat() != day:
            continue
        for person in row.get("interviewers", []):
            if person.get("interviewer_id"):
                counts[str(person["interviewer_id"])].add(row["interview_id"])
    return {"date": day, "timezone": str(tz), "scope": "captured_interviews_only",
            "missing_or_timezone_unknown": missing,
            "explicit_source_timezone": options.get("source_timezone"), "timezone_applied_records": assumed,
            "interviewers": [{"interviewer_id": k, "count": len(v), "soft_limit": 3, "hard_limit": 4,
                              "status": "hard_exceeded" if len(v) > 4 else
                                        "soft_exceeded" if len(v) > 3 else "within_limit"}
                             for k, v in sorted(counts.items())],
            "scheduling_enabled": False}


def scorecards(capture):
    """Only explicitly sourced 1..5 scores; never turn narrative into numbers."""
    import math
    dimensions = ("skill", "experience", "communication", "potential", "culture", "risk")
    groups, excluded = [], 0
    for interview in capture["interviews"]:
        observations = defaultdict(list)
        seen = {}
        conflicted = set()
        for person in interview.get("interviewers", []):
            scores = person.get("scores")
            key = (person.get("interviewer_id"), person.get("evaluation_id"))
            if not all(key) or not isinstance(scores, dict) or person.get("score_scale") != "1-5":
                excluded += 1
                continue
            if key in seen:
                if seen[key] != scores:
                    conflicted.add(key)
                continue
            seen[key] = scores
        for key, scores in seen.items():
            if key in conflicted:
                excluded += 1
                continue
            for dimension in dimensions:
                value = scores.get(dimension)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 5:
                    continue
                observations[dimension].append({"score": value, "interviewer_id": key[0], "evaluation_id": key[1]})
        if observations:
            groups.append({
                "interview_id": interview["interview_id"], "application_id": interview["application_id"],
                "candidate_id": interview["candidate_id"], "job_id": interview["job_id"], "round": interview.get("round"),
                "dimensions": {dimension: {
                    "n": len(values), "mean": sum(v["score"] for v in values) / len(values),
                    "spread": max(v["score"] for v in values) - min(v["score"] for v in values),
                    "evidence": values,
                } for dimension, values in observations.items()},
            })
    return {"status": "evaluated_snapshot" if groups else "awaiting_source",
            "audience": "recruitment_lead", "groups": groups, "excluded_cards": excluded,
            "limitations": ["Same interview only; missing dimensions excluded from denominator",
                           "Scores must be supplied with evaluation_id and explicit 1-5 scale",
                           "No interviewer bias labels, automatic rejection or hiring ranking"]}
