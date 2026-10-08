"""Daily runner for the numbered console workflow; the console only monitors it."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .platform_store import utcnow

ZONE = ZoneInfo("Asia/Shanghai")
STEPS = (
    "北森只读", "人才库筛选与推荐", "论文雷达 · 高端被动人才", "文件整理与清洗",
    "批量核验与上传", "候选人锁提醒", "招聘漏斗", "招聘台账",
)
SOURCING_STEPS = ("根据 JD 配置条件", "BOSS直聘", "公司系统与人才库", "海外", "QQ邮箱")
ACTIVE = {"queued", "running", "waiting_for_inventory"}


def initialize(store) -> None:
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS autonomous_settings (
          actor TEXT PRIMARY KEY, enabled INTEGER NOT NULL, hour INTEGER NOT NULL,
          minute INTEGER NOT NULL, updated_at TEXT NOT NULL)""")
        cx.execute("""CREATE TABLE IF NOT EXISTS autonomous_runs (
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, run_date TEXT NOT NULL, status TEXT NOT NULL,
          step INTEGER NOT NULL DEFAULT 0, checkpoint BLOB NOT NULL, error TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(actor,run_date))""")
        cx.execute("INSERT OR IGNORE INTO autonomous_settings VALUES ('local-admin',1,11,0,?)", (utcnow(),))
        # The previous four-card monitor stopped instead of walking through the page.
        # Archive those paused rows and free their date for the numbered workflow.
        cx.execute("""UPDATE autonomous_runs SET status='superseded',
          run_date='legacy-' || run_date || '-' || substr(id,1,6),updated_at=?
          WHERE status='awaiting_user'""", (utcnow(),))


def configure(store, actor: str, enabled: bool, hour: int = 11, minute: int = 0) -> dict:
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("运行时间不正确")
    with store.transaction() as cx:
        cx.execute("""INSERT INTO autonomous_settings VALUES (?,?,?,?,?)
          ON CONFLICT(actor) DO UPDATE SET enabled=excluded.enabled,hour=excluded.hour,
          minute=excluded.minute,updated_at=excluded.updated_at""",
                   (actor, int(enabled), hour, minute, utcnow()))
        store.audit(cx, actor, "autonomous.configure", f"{hour:02d}:{minute:02d}")
    return overview(store, actor)


def _create(store, actor: str, run_date: str, *, manual: bool) -> str:
    run_id, now = uuid.uuid4().hex, utcnow()
    with store.transaction() as cx:
        active = cx.execute("SELECT id FROM autonomous_runs WHERE actor=? AND status IN ('queued','running','waiting_for_inventory')",
                            (actor,)).fetchone()
        if active:
            return active["id"]
        try:
            cx.execute("INSERT INTO autonomous_runs VALUES (?,?,?,'queued',0,?,NULL,?,?)",
                       (run_id, actor, run_date, store.seal({"manual": manual}), now, now))
        except Exception as error:
            if "UNIQUE constraint" not in str(error):
                raise
            return cx.execute("SELECT id FROM autonomous_runs WHERE actor=? AND run_date=?",
                              (actor, run_date)).fetchone()["id"]
        store.event(cx, run_id, "autonomous.queued", "daily-agent",
                    {"run_date": run_date, "steps": STEPS, "manual": manual})
        store.audit(cx, actor, "autonomous.run_created", run_id)
    return run_id


def run_now(store, actor: str) -> str:
    return _create(store, actor, datetime.now(ZONE).strftime("%Y-%m-%d-manual-%H%M%S"), manual=True)


def rerun_step(store, actor: str, step: int) -> str:
    if step not in range(len(STEPS)):
        raise ValueError("只能重跑现有流程步骤")
    with store.transaction() as cx:
        sources = cx.execute("SELECT checkpoint FROM autonomous_runs WHERE actor=? AND status<>'superseded' ORDER BY created_at DESC LIMIT 20",
                             (actor,)).fetchall()
    checkpoint = {}
    for source in sources:
        candidate = store.unseal(source["checkpoint"]) or {}
        if step == 0 or candidate.get("positions"):
            checkpoint = candidate
            break
    checkpoint = checkpoint or {}
    if step > 0 and not checkpoint.get("positions"):
        with store.transaction() as cx:
            dataset = cx.execute("SELECT id,payload FROM datasets ORDER BY created_at DESC LIMIT 1").fetchone()
        if dataset:
            capture = store.unseal(dataset["payload"])
            checkpoint["dataset_id"] = dataset["id"]
            checkpoint["positions"] = capture.get("positions", [])
    checkpoint.pop("live_run_id", None)
    checkpoint.setdefault("step_statuses", {}).pop(str(step), None)
    checkpoint.setdefault("details", {}).pop(str(step), None)
    checkpoint["gaps"] = [item for item in checkpoint.get("gaps", []) if f"第{step + 1}步" not in item]
    run_id, now = uuid.uuid4().hex, utcnow()
    stamp = datetime.now(ZONE).strftime(f"%Y-%m-%d-step-{step + 1}-%H%M%S")
    with store.transaction() as cx:
        cx.execute("INSERT INTO autonomous_runs VALUES (?,?,?,'queued',?,?,NULL,?,?)",
                   (run_id, actor, stamp, step, store.seal(checkpoint), now, now))
        store.audit(cx, actor, "autonomous.step_rerun", f"{run_id}:{step + 1}")
    return run_id


def dispatch_due(store, now: datetime | None = None) -> int:
    current = (now or datetime.now(ZONE)).astimezone(ZONE)
    today = current.date().isoformat()
    with store.transaction() as cx:
        rows = cx.execute("SELECT * FROM autonomous_settings WHERE enabled=1").fetchall()
    created = 0
    for row in rows:
        if (current.hour, current.minute) < (row["hour"], row["minute"]):
            continue
        with store.transaction() as cx:
            exists = cx.execute("SELECT 1 FROM autonomous_runs WHERE actor=? AND run_date=?",
                                (row["actor"], today)).fetchone()
        if not exists:
            _create(store, row["actor"], today, manual=False)
            created += 1
    return created


def _public(store, row) -> dict:
    checkpoint = store.unseal(row["checkpoint"]) or {}
    statuses = checkpoint.get("step_statuses", {})
    return {
        "id": row["id"], "run_date": row["run_date"], "status": row["status"],
        "step": row["step"], "step_name": STEPS[min(row["step"], len(STEPS) - 1)],
        "steps": [{"index": index + 1, "name": name,
                   "status": statuses.get(str(index), "completed" if index < row["step"] else
                                          "running" if index == row["step"] and row["status"] in ACTIVE else "pending"),
                   "substeps": _sourcing_substeps(checkpoint, row, index)}
                  for index, name in enumerate(STEPS)],
        "checkpoint": checkpoint, "error": row["error"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _sourcing_substeps(checkpoint: dict, row, index: int) -> list[dict]:
    if index != 1:
        return []
    detail = checkpoint.get("details", {}).get("1", {})
    values = detail.get("substeps", {})
    default = "running" if row["step"] == 1 and row["status"] in ACTIVE else "pending"
    if row["step"] > 1 and not values:
        default = "completed_with_gaps"
    return [{"index": f"2.{offset + 1}", "name": name,
             "status": values.get(str(offset), {}).get("status", default),
             "detail": values.get(str(offset), {}).get("detail")}
            for offset, name in enumerate(SOURCING_STEPS)]


def overview(store, actor: str) -> dict:
    with store.transaction() as cx:
        setting = cx.execute("SELECT * FROM autonomous_settings WHERE actor=?", (actor,)).fetchone()
        runs = cx.execute("SELECT * FROM autonomous_runs WHERE actor=? AND status<>'superseded' ORDER BY created_at DESC LIMIT 10",
                          (actor,)).fetchall()
    return {
        "enabled": bool(setting["enabled"]) if setting else False,
        "schedule": f"{setting['hour']:02d}:{setting['minute']:02d}" if setting else "11:00",
        "timezone": "Asia/Shanghai", "service_pid": os.getpid(),
        "runs": [_public(store, row) for row in runs],
        "boundaries": ["登录、扫码和验证码需要本人处理", "上传、推荐和触达保留实时核验与当前确认"],
    }


def _finish(store, row, checkpoint: dict, status: str, detail: dict | None = None) -> None:
    checkpoint.setdefault("step_statuses", {})[str(row["step"])] = status
    checkpoint.setdefault("details", {})[str(row["step"])] = detail or {}
    next_step = row["step"] + 1
    final_status = "queued"
    if next_step >= len(STEPS):
        final_status = "completed_with_gaps" if checkpoint.get("gaps") else "completed"
    with store.transaction() as cx:
        cx.execute("UPDATE autonomous_runs SET status=?,step=?,checkpoint=?,error=NULL,updated_at=? WHERE id=?",
                   (final_status, next_step, store.seal(checkpoint), utcnow(), row["id"]))
        store.event(cx, row["id"], "autonomous.step_completed", "daily-agent",
                    {"step": STEPS[row["step"]], "step_status": status})


def _gap(checkpoint: dict, message: str) -> None:
    if message not in checkpoint.setdefault("gaps", []):
        checkpoint["gaps"].append(message)


def _run_step(runtime, row: dict, checkpoint: dict) -> bool:
    store, actor, step = runtime.store, row["actor"], row["step"]
    if step == 0:
        from . import live_runs
        live_id = checkpoint.get("live_run_id")
        if not live_id:
            checkpoint["live_run_id"] = live_runs.submit(runtime, actor, "autonomous-full:" + row["run_date"])
            with store.transaction() as cx:
                cx.execute("UPDATE autonomous_runs SET status='waiting_for_inventory',checkpoint=?,updated_at=? WHERE id=?",
                           (store.seal(checkpoint), utcnow(), row["id"]))
            return True
        with store.transaction() as cx:
            live = cx.execute("SELECT * FROM live_runs WHERE id=?", (live_id,)).fetchone()
        if not live or live["status"] in {"queued", "running", "waiting_for_audit"}:
            return False
        live_cp = store.unseal(live["checkpoint"]) or {}
        positions = live_cp.get("positions", {}).get("value", {}).get("positions", [])
        if live["status"] in {"waiting_for_user", "failed", "cancelled"}:
            _gap(checkpoint, "第1步北森完整采集未完成，请检查登录、浏览器控制权或面试页")
            _finish(store, row, checkpoint, "needs_attention",
                    {"status": live["status"], "error": live["error"], "positions_read": len(positions)})
            return True
        detail = live_cp.get("validate_import", {}).get("value", {})
        checkpoint["dataset_id"], checkpoint["positions"] = detail.get("dataset_id"), positions
        _finish(store, row, checkpoint, "completed", {
            "positions": len(positions), "applications": detail.get("application_count", 0),
            "interviews": detail.get("interview_count", 0), "dataset_id": checkpoint["dataset_id"],
        })
        return True

    positions = checkpoint.get("positions", [])
    if step == 1:
        today = datetime.now(ZONE).date().isoformat()
        from .boss_sourcing import configure_from_jd, dispatch_due as dispatch_boss, existing_login_status
        from .italent_browser import collect_job_jd
        from .email_recruiting import confirmed_login
        boss_login = confirmed_login(store, "BOSS直聘")
        mail_login = confirmed_login(store, "QQ邮箱")
        with store.transaction() as cx:
            schedules = {item["job_id"]: dict(item) for item in cx.execute(
                "SELECT job_id,enabled,last_run_date,last_status,last_error FROM boss_sourcing_schedules WHERE actor=?",
                (actor,)).fetchall()}
        existing_jds = {}
        from .resume_matching import load_jd
        for position in positions:
            try:
                existing_jds[position["job_id"]] = load_jd(position["job_id"])
            except ValueError:
                pass
        jobs = []
        for position in positions:
            schedule = schedules.get(position.get("job_id"))
            generated = False
            if not schedule or not schedule["enabled"]:
                try:
                    jd = existing_jds.get(position["job_id"]) or collect_job_jd(position["job_id"])
                    configured = configure_from_jd(store, actor, position, jd)
                    schedule = {"enabled": 1, "last_run_date": None, "last_status": "configured_from_jd",
                                "last_error": None}
                    generated = True
                except Exception as error:
                    jobs.append({"job_id": position.get("job_id"), "title": position.get("title"),
                                 "status": "configuration_failed", "error": str(error)[:240]})
                    continue
            status = schedule.get("last_status") if schedule and schedule["enabled"] and schedule["last_run_date"] == today else "awaiting_configuration"
            if generated:
                status = "configured_from_jd"
            jobs.append({"job_id": position.get("job_id"), "title": position.get("title"), "status": status,
                         "error": schedule.get("last_error") if schedule else None})
        # The runtime scheduler owns searches so this step remains resumable and bounded.
        # Newly generated profiles are eligible on its next tick.
        if any(item["status"] != "configured_from_jd" for item in jobs):
            dispatch_boss(store)
            with store.transaction() as cx:
                latest = {item["job_id"]: dict(item) for item in cx.execute(
                    "SELECT job_id,last_run_date,last_status,last_error FROM boss_sourcing_schedules WHERE actor=?",
                    (actor,)).fetchall()}
            for job in jobs:
                schedule = latest.get(job["job_id"])
                if schedule and schedule["last_run_date"] == today:
                    job.update(status=schedule["last_status"] or "running", error=schedule["last_error"])
        configured = sum(item["status"] not in {"awaiting_configuration", "configuration_failed"} for item in jobs)
        profile_failed = len(jobs) - configured
        boss_completed = sum(item["status"] == "completed" for item in jobs)
        boss_failed = sum(item["status"] == "failed" for item in jobs)
        substeps = {
            "0": {"status": "completed" if not profile_failed else "completed_with_gaps",
                  "detail": f"已配置 {configured}/{len(jobs)} 个岗位"},
            "1": {"status": "completed" if boss_completed == len(jobs) else "completed_with_gaps",
                  "detail": f"BOSS 已完成 {boss_completed}/{len(jobs)} 个岗位，失败 {boss_failed} 个"},
            "2": {"status": "needs_attention", "detail": "自动只读筛选；推荐前保留实时预检与确认"},
            "3": {"status": "needs_attention", "detail": "需要逐候选人合法处理依据，当前不自动采集"},
            "4": {"status": "ready", "detail": "QQ 邮箱登录已核验；按岗位扫描求职邮件与 PDF 简历"},
        }
        controlled_boss = existing_login_status()
        if controlled_boss["logged_in"]:
            substeps["1"]["detail"] += "；" + controlled_boss["detail"]
        from .email_recruiting import confirmed_login, probe_logged_in_mailbox
        try:
            email_state = probe_logged_in_mailbox("QQ邮箱")
            substeps["4"] = {"status": "ready" if email_state["logged_in"] else "needs_attention",
                             "detail": email_state["detail"]}
        except Exception as error:
            substeps["4"] = {"status": "needs_attention", "detail": str(error)[:240]}
        confirmation = confirmed_login(store, "QQ邮箱")
        if confirmation and substeps["4"]["status"] != "ready":
            substeps["4"] = {"status": "ready",
                             "detail": "用户已确认 Chrome QQ 邮箱登录；后台任务空间仍需建立受控会话后扫描"}
        if boss_login and substeps["1"]["status"] != "completed":
            substeps["1"]["detail"] += "；Chrome 登录已确认，但 BOSS CLI 受页面风控阻断"
        if mail_login and substeps["4"]["status"] != "ready":
            substeps["4"] = {"status": "ready", "detail": "Chrome QQ 邮箱登录已确认"}
        if configured < len(jobs):
            _gap(checkpoint, f"第2步有 {len(jobs) - configured} 个岗位未能根据 JD 配置自动寻才条件")
        has_gaps = configured < len(jobs) or boss_completed < len(jobs)
        _finish(store, row, checkpoint, "completed_with_gaps" if has_gaps else "completed",
                {"positions": len(jobs), "configured": configured, "jobs": jobs, "substeps": substeps,
                 "sources": {"BOSS直聘": "根据已核验 JD 自动配置并按时运行", "公司系统与人才库": "复用 JD 核心条件，推荐仍需预检",
                             "海外": "等待合法处理依据", "QQ邮箱": "复用 Chrome 已登录会话"}})
        return True

    if step == 2:
        from .paper_radar import run
        results = []
        for position in positions:
            title = position.get("title") or position.get("job_id")
            try:
                result = run(f"{title}。招聘岗位编号 {position.get('job_id')}，检索岗位相关技术方向的公开论文作者。",
                             ["arXiv", "OpenAlex", "Semantic Scholar", "DBLP", "Crossref"], 8)
                results.append({"job_id": position.get("job_id"), "title": title, "run_id": result["run_id"],
                                "status": result["status"], "candidates": result["candidate_count"]})
            except Exception as error:
                results.append({"job_id": position.get("job_id"), "title": title,
                                "status": "failed", "error": str(error)[:240]})
        failed = sum(item["status"] == "failed" for item in results)
        if failed:
            _gap(checkpoint, f"第3步有 {failed} 个岗位论文雷达失败")
        _finish(store, row, checkpoint, "completed_with_gaps" if failed else "completed",
                {"positions": len(results), "results": results, "external_actions": 0})
        return True

    if step == 3:
        from .resume_matching import run_all
        from .italent_browser import collect_job_jd
        try:
            result = run_all(positions, collect_job_jd)
            status = "completed" if result["status"] == "completed" else "completed_with_gaps"
            if status != "completed":
                _gap(checkpoint, "第4步部分岗位没有对应本地简历目录或读取 JD 失败")
        except Exception as error:
            result, status = {"status": "blocked", "error": str(error)[:400]}, "needs_attention"
            _gap(checkpoint, "第4步文件匹配未完成：" + str(error)[:160])
        checkpoint["matching"] = result
        _finish(store, row, checkpoint, status, result)
        return True

    if step == 4:
        matching = checkpoint.get("matching", {})
        detail = {"high_match_count": matching.get("high_count", 0),
                  "reports": [{"job_id": item.get("job_id"), "job_title": item.get("job_title"), "run_id": item.get("run_id")}
                              for item in matching.get("results", [])],
                  "status": "awaiting_confirmation", "automatic_upload": False,
                  "next_action": "在本步骤核验高匹配简历，再确认上传到简历复筛"}
        _gap(checkpoint, "第5步上传属于外部写入，等待当前确认和写后回读")
        _finish(store, row, checkpoint, "needs_attention", detail)
        return True

    if step == 5:
        from .lock_monitor import refresh
        try:
            result = refresh(store, actor)
            detail, status = {"items": len(result["items"]), "counts": result["counts"],
                              "last_success": result["last_success"]}, "completed"
        except Exception as error:
            detail, status = {"error": str(error)[:400]}, "needs_attention"
            _gap(checkpoint, "第6步候选人锁状态刷新未完成")
        _finish(store, row, checkpoint, status, detail)
        return True

    capture = store.dataset(checkpoint["dataset_id"]) if checkpoint.get("dataset_id") else None
    if step == 6:
        if capture:
            from .recruitment_report import build_report
            report = build_report(capture)
            detail, status = {"dataset_id": checkpoint["dataset_id"], "positions": len(report["groups"]),
                              "candidate_count": sum(group["candidate_count"] for group in report["groups"]),
                              "captured_at": report["captured_at"]}, "completed"
        else:
            detail, status = {"status": "awaiting_verified_dataset"}, "needs_attention"
            _gap(checkpoint, "第7步没有本次已核验数据，保留现有招聘漏斗")
        _finish(store, row, checkpoint, status, detail)
        return True

    if capture:
        from .recruitment_report import build_report
        report = build_report(capture)
        detail, status = {"dataset_id": checkpoint["dataset_id"], "positions": len(report["groups"]),
                          "rows": sum(len(group["rows"]) for group in report["groups"]),
                          "captured_at": report["captured_at"]}, "completed"
    else:
        detail, status = {"status": "awaiting_verified_dataset"}, "needs_attention"
        _gap(checkpoint, "第8步没有本次已核验数据，保留现有招聘台账")
    _finish(store, row, checkpoint, status, detail)
    return True


def tick(runtime) -> bool:
    store = runtime.store
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM autonomous_runs WHERE status IN ('queued','running','waiting_for_inventory') ORDER BY created_at LIMIT 1").fetchone()
    if not row:
        return False
    row, checkpoint = dict(row), store.unseal(row["checkpoint"]) or {}
    try:
        return _run_step(runtime, row, checkpoint)
    except Exception as error:
        _gap(checkpoint, f"第{row['step'] + 1}步异常：{str(error)[:160]}")
        with store.transaction() as cx:
            cx.execute("UPDATE autonomous_runs SET status='failed',checkpoint=?,error=?,updated_at=? WHERE id=?",
                       (store.seal(checkpoint), str(error)[:500], utcnow(), row["id"]))
            store.event(cx, row["id"], "autonomous.failed", "daily-agent", {"error_type": type(error).__name__})
        return True
