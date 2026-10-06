"""Read-only, visible Ego job inventory. No cookies, signatures or ATS mutations."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections import defaultdict
from pathlib import Path

from .platform_store import utcnow

_lock = threading.Lock()


def _collect(script, state, timeout, payload=None):
    executable = shutil.which("ego-browser")
    if not executable:
        raise ValueError("Ego browser CLI unavailable. Configure the authorized browser on this computer.")
    if not _lock.acquire(blocking=False):
        raise ValueError("A browser inventory is already running")
    try:
        source = Path(__file__).with_name(script).read_text()
        if payload is not None:
            source = "const TALENTPILOT_INPUT=" + json.dumps(payload, ensure_ascii=False) + ";\n" + source
        try:
            result = subprocess.run([executable, "nodejs"], input=source, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError("Browser read timed out; no inventory committed. Inspect the visible task page.") from None
        # Do not disclose arbitrary browser diagnostics, URLs or session parameters.
        records = [line.split("TALENTPILOT_RESULT:", 1)[1] for line in (result.stdout + "\n" + result.stderr).splitlines()
                   if line.startswith("TALENTPILOT_RESULT:")]
        if result.returncode or not records:
            details = (result.stdout + "\n" + result.stderr).lower()
            if "manual login" in details or "awaiting_login" in details:
                raise ValueError("北森登录已失效，请在 Ego Browser 登录后重试；未更新数据。")
            if "user is controlling" in details:
                raise ValueError("Ego Browser 当前由用户控制，请交还控制权后重试；未更新数据。")
            if script == "collect_interviews.js":
                raise ValueError("岗位读取成功，但面试人数读取失败；请检查 Ego Browser 的北森面试页后重试。未更新数据。")
            raise ValueError("北森只读采集失败，请检查 Ego Browser 登录和控制权后重试；未更新数据。")
        data = json.loads(records[-1])
        if data.get("state") != state:
            raise ValueError(data.get("reason", "Inventory incomplete"))
        data["collected_at"] = utcnow()
        data["external_actions"] = 0
        names = {"collect_positions.js": "TalentPilot 岗位只读连接",
                 "collect_applications.js": "TalentPilot 申请只读连接",
                 "collect_interviews.js": "TalentPilot 面试只读连接",
                 "collect_locks.js": "TalentPilot 锁状态只读连接",
                 "collect_job_jd.js": "TalentPilot 岗位JD只读匹配",
                 "match_candidate_history.js": "TalentPilot 候选人历史只读匹配"}
        cleanup = "cliLog(await completeTaskSpace(" + json.dumps(names[script]) + ", {keep:false}));"
        try:
            closed = subprocess.run([executable, "nodejs"], input=cleanup, text=True,
                                    capture_output=True, timeout=15)
            data["browser_cleanup_completed"] = closed.returncode == 0 and "done: true" in (closed.stdout + closed.stderr)
        except subprocess.TimeoutExpired:
            data["browser_cleanup_completed"] = False
        return data
    finally:
        _lock.release()


def collect_positions():
    data = _collect("collect_positions.js", "verified_job_inventory", 90)
    if not data.get("positions") or len({p["job_id"] for p in data["positions"]}) != data["declared"]:
        raise ValueError("Job inventory coverage mismatch")
    data.update(source="Authorized live iTalent job grid", applications_refreshed=False)
    return data


def collect_locks():
    return _collect("collect_locks.js", "verified_lock_inventory", 480)


def collect_job_jd(job_id):
    import re
    if not re.fullmatch(r"J\d+", job_id):
        raise ValueError("岗位编号必须是北森 J 编号")
    data = _collect("collect_job_jd.js", "verified_job_jd", 150, {"job_id": job_id})
    if data.get("job_id") != job_id:
        raise ValueError("JD 岗位编号与目标不一致，未保存")
    return data


def collect_applications():
    data = _collect("collect_applications.js", "verified_application_inventory", 240)
    if not data.get("applications") or len({a["application_id"] for a in data["applications"]}) != data["declared"]:
        raise ValueError("Application coverage mismatch")
    jobs = {}
    for row in data["applications"]:
        existing = jobs.get(row["job_id"])
        if existing and existing["job_uuid"] != row["job_uuid"]:
            raise ValueError("Job code maps to multiple UUIDs")
        jobs[row["job_id"]] = {"job_id": row["job_id"], "job_uuid": row["job_uuid"],
                              "title": row["job_title"], "department": None}
    # The new snapshot intentionally contains no historical interview/Offer data.
    # Mixing yesterday's evaluations into today's inventory would conceal its age.
    return {
        "schema_version": "talentpilot-ledger/2",
        "run": {"completed_at": data["collected_at"], "scope": data["scope"],
                "source": "Current authorized iTalent UI pagination; application inventory only"},
        "positions": list(jobs.values()), "applications": data["applications"], "interviews": [], "offers": [],
        "quality": {
            "coverage": {"positions": {"declared": len(jobs), "scope": "Jobs observed in applications only"},
                         "applications": {"declared": data["declared"], "captured": data["captured"],
                                          "scope": data["scope"]},
                         "interviews": {"declared": 0, "state": "not_collected"},
                         "offers": {"state": "not_collected"}},
            "pages": {"applications": data["pages"]},
            "warnings": ["本次仅刷新当前申请存量；面试历史、面评、锁和逐条Offer发送证据未采集。",
                         "岗位来源为申请中观察到的岗位，不是全公司岗位；部门待实时岗位清单核对。",
                         "分页为顺序快照，不是事务快照；当前阶段不代表历史通过事件。"],
        },
    }


def collect_interviews():
    data = _collect("collect_interviews.js", "verified_interview_inventory", 180)
    if not data.get("interviews") or len({i["interview_id"] for i in data["interviews"]}) != data["declared"]:
        raise ValueError("Interview coverage mismatch")
    return data


def add_interview_counts(position_inventory, interview_inventory):
    """Attach distinct candidate counts by job and interview round."""
    counts = defaultdict(lambda: defaultdict(set))
    round_fields = {"一面": "first_interview_count", "二面": "second_interview_count",
                    "三面": "third_interview_count"}
    for interview in interview_inventory["interviews"]:
        field = round_fields.get(interview.get("round"))
        identity = interview.get("candidate_id") or interview.get("application_id")
        if field and interview.get("job_id") and identity:
            counts[interview["job_id"]][field].add(str(identity))
    positions = []
    for position in position_inventory["positions"]:
        row = dict(position)
        for field in round_fields.values():
            row[field] = len(counts[row["job_id"]][field])
        positions.append(row)
    return {**position_inventory, "positions": positions,
            "interviews_declared": interview_inventory["declared"],
            "interviews_captured": interview_inventory["captured"],
            "interviews_collected_at": interview_inventory["collected_at"],
            "interview_count_scope": "全部时间面试 / 当前账号可见列表；按岗位、轮次、候选人去重"}


def match_candidate_history(identities):
    data = _collect("match_candidate_history.js", "verified_candidate_history", 360, identities)
    data["source"] = "Authorized iTalent candidate information query"
    return data


def combine_interviews(capture, inventory):
    import copy
    capture = copy.deepcopy(capture)
    applications = {a["application_id"]: a for a in capture["applications"]}
    jobs = {p["job_id"] for p in capture["positions"]}
    for interview in inventory["interviews"]:
        application = applications.get(interview["application_id"])
        interview = copy.deepcopy(interview)
        if application and any(interview[k] != application[k] for k in ("candidate_id", "job_id")):
            raise ValueError("Live interview/application identity conflict; no combined dataset committed")
        interview["in_application_scope"] = application is not None
        interview["in_current_job_scope"] = interview["job_id"] in jobs
        from datetime import datetime
        from zoneinfo import ZoneInfo
        try:
            local = datetime.strptime(interview["scheduled_at"], "%Y/%m/%d %H:%M:%S")
            interview["scheduled_at"] = local.replace(tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()
        except (ValueError, TypeError, KeyError):
            raise ValueError("Interview time format changed; timezone normalization requires review") from None
        capture["interviews"].append(interview)
    capture["run"]["applications_collected_at"] = capture["run"]["completed_at"]
    capture["run"]["completed_at"] = inventory["collected_at"]
    capture["run"]["scope"] += "; " + inventory["scope"] + "; 面评正文未刷新"
    capture["quality"]["coverage"]["interviews"] = {
        "declared": inventory["declared"], "captured": inventory["captured"], "scope": inventory["scope"]}
    capture["quality"]["pages"]["interviews"] = inventory["pages"]
    capture["quality"]["warnings"] = [
        "本次采集当前申请存量和面试列表；个人面评正文、锁、逐条Offer发送证据未采集。",
        "只按申请ID+候选人ID+岗位编号关联；申请范围外的面试保留，不并入当前岗位通过率。",
        "岗位仅含申请中观察到的岗位；部门与本次岗位清单按岗位编号核对。",
        "分页为顺序快照，不是事务快照；当前阶段不代表历史通过事件。",
    ]
    return capture
