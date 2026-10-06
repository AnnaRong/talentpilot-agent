"""Durable read-only acquisition DAG. External writes never run from this queue."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import italent_browser
from .platform_store import digest, utcnow

STEPS = ("positions", "applications", "interviews", "validate_import", "audit", "report")
ACTIVE = ("queued", "running", "waiting_for_audit")
TTL = 1800


def initialize(store):
    with store.transaction() as cx:
        cx.execute("""CREATE TABLE IF NOT EXISTS live_runs (
          id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, actor TEXT NOT NULL,
          status TEXT NOT NULL, step INTEGER NOT NULL DEFAULT 0,
          checkpoint BLOB NOT NULL, result BLOB, error TEXT,
          attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL, claim TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")


def authorized(cx, actor):
    row = cx.execute("SELECT * FROM users WHERE id=?", (actor,)).fetchone()
    return bool(row and row["enabled"] and row["role"] == "admin"
                and "*" in json.loads(row["departments"]))


def submit(runtime, actor, request_key):
    store = runtime.store
    key = digest([actor, request_key])
    with store.transaction() as cx:
        if not authorized(cx, actor):
            raise ValueError("Live collection requires an enabled cross-department administrator")
        old = cx.execute("SELECT id FROM live_runs WHERE request_key=?", (key,)).fetchone()
        if old:
            return old["id"]
        if cx.execute("SELECT 1 FROM live_runs WHERE status IN ('queued','running','waiting_for_audit')").fetchone():
            raise ValueError("A live workflow is already active; inspect or cancel it first")
        run_id = uuid.uuid4().hex
        cx.execute("""INSERT INTO live_runs
          (id,request_key,actor,status,checkpoint,created_at,updated_at)
          VALUES (?,?,?,'queued',?,?,?)""",
                   (run_id, key, actor, store.seal({}), utcnow(), utcnow()))
        store.event(cx, run_id, "live.queued", "orchestrator", {"steps": STEPS, "external_writes": 0})
        store.audit(cx, actor, "live.submit", run_id)
    return run_id


def list_runs(store):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with store.transaction() as cx:
        rows = cx.execute("SELECT * FROM live_runs ORDER BY created_at DESC LIMIT 50").fetchall()
    rows = [row for row in rows if local_date(row["created_at"]) == today][:1]
    result = []
    for row in rows:
        checkpoints = store.unseal(row["checkpoint"])
        result.append({
            "id": row["id"], "actor": row["actor"], "status": row["status"],
            "step": STEPS[row["step"]] if row["step"] < len(STEPS) else "done",
            "completed_steps": [s for s in STEPS if s in checkpoints], "attempts": row["attempts"],
            "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "result": store.unseal(row["result"]),
            "graph": [{"id": s, "depends_on": list(STEPS[i-1:i])} for i, s in enumerate(STEPS)],
        })
    return result


def local_date(value):
    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(ZoneInfo("Asia/Shanghai")).date()


def control(runtime, run_id, actor, operation):
    store = runtime.store
    with store.transaction() as cx:
        if not authorized(cx, actor):
            raise ValueError("Administrator required")
        row = cx.execute("SELECT * FROM live_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Live workflow not found")
        if operation == "cancel":
            if row["status"] not in ACTIVE + ("waiting_for_user", "failed"):
                raise ValueError("Workflow is already terminal")
            cx.execute("UPDATE live_runs SET status='cancelled',claim=NULL,updated_at=? WHERE id=?",
                       (utcnow(), run_id))
        elif operation == "resume":
            if row["status"] not in {"waiting_for_user", "failed"} or row["attempts"] >= 12:
                raise ValueError("Workflow cannot resume; retry budget exhausted or not paused")
            if cx.execute("""SELECT 1 FROM live_runs WHERE id<>? AND
                          status IN ('queued','running','waiting_for_audit')""", (run_id,)).fetchone():
                raise ValueError("Another live workflow is active")
            checkpoints = store.unseal(row["checkpoint"])
            stale = checkpoints and time.time() - min(c["at"] for c in checkpoints.values()) > TTL
            cx.execute("""UPDATE live_runs SET status='queued',error=NULL,claim=NULL,
                          step=?,checkpoint=?,updated_at=? WHERE id=?""",
                       (0 if stale else row["step"], store.seal({}) if stale else row["checkpoint"],
                        utcnow(), run_id))
        else:
            raise ValueError("Unsupported workflow control")
        store.audit(cx, actor, "live." + operation, run_id)
        store.event(cx, run_id, "live." + operation, "orchestrator", {})


def claim(store):
    with store.transaction() as cx:
        # Never automatically reopen a browser after losing ownership or crashing.
        cx.execute("""UPDATE live_runs SET status='waiting_for_user',claim=NULL,
          error='Worker interrupted. Inspect browser control and explicitly resume.'
          WHERE status='running' AND lease_until<?""", (time.time(),))
        if cx.execute("SELECT 1 FROM live_runs WHERE status='running'").fetchone():
            return None
        row = cx.execute("""SELECT * FROM live_runs WHERE status IN ('queued','waiting_for_audit')
                            ORDER BY created_at LIMIT 1""").fetchone()
        if not row:
            return None
        if not authorized(cx, row["actor"]):
            cx.execute("UPDATE live_runs SET status='failed',error='Owner permission revoked' WHERE id=?", (row["id"],))
            return None
        if row["attempts"] >= 12:
            cx.execute("UPDATE live_runs SET status='failed',error='Execution budget exhausted' WHERE id=?", (row["id"],))
            return None
        token = uuid.uuid4().hex
        cx.execute("""UPDATE live_runs SET status='running',claim=?,lease_until=?,
                      updated_at=? WHERE id=?""", (token, time.time() + 400, utcnow(), row["id"]))
        return {**dict(row), "claim": token}


def tick(runtime):
    store = runtime.store
    with store.transaction() as cx:
        for pending in cx.execute("SELECT id,result FROM live_runs WHERE status='awaiting_export_approval'").fetchall():
            result = store.unseal(pending["result"])
            approval = cx.execute("SELECT status,expires FROM actions WHERE id=?",
                                  (result["export_action_id"],)).fetchone()
            status = None
            if approval and approval["status"] == "executed":
                status = result["status"]
            elif approval and approval["status"] == "rejected":
                status = "export_rejected"
            elif approval and approval["expires"] < time.time():
                status = "export_expired"
            if status:
                cx.execute("UPDATE live_runs SET status=?,updated_at=? WHERE id=?",
                           (status, utcnow(), pending["id"]))
    row = claim(store)
    if not row:
        return False
    run_id, token = row["id"], row["claim"]
    cp = store.unseal(row["checkpoint"])
    step = STEPS[row["step"]]
    try:
        if cp and step not in {"report"} and time.time() - min(v["at"] for v in cp.values()) > TTL:
            raise ValueError("Source checkpoints older than 30 minutes. Resume to restart collection.")
        with store.transaction() as cx:
            store.event(cx, run_id, "live.step.started", step, {"depends_on": list(cp)})
        if step in {"positions", "applications", "interviews"}:
            value = getattr(italent_browser, "collect_" + step)()
        elif step == "validate_import":
            capture = italent_browser.combine_interviews(cp["applications"]["value"], cp["interviews"]["value"])
            inventory = cp["positions"]["value"]
            by_job = {p["job_id"]: p for p in inventory["positions"]}
            for position in capture["positions"]:
                position.update({k: by_job.get(position["job_id"], {}).get(k)
                                 for k in ("department", "recruiter", "hc")})
            capture["run"].update(positions_collected_at=inventory["collected_at"], live_run_id=run_id)
            value = {"dataset_id": store.import_dataset(capture, row["actor"], "live_background_workflow"),
                     "application_count": len(capture["applications"]), "interview_count": len(capture["interviews"]),
                     "job_inventory_count": len(inventory["positions"]),
                     "captured_at": capture["run"]["completed_at"]}
        elif step == "audit":
            dataset_id = cp["validate_import"]["value"]["dataset_id"]
            job_id = runtime.submit("audit", row["actor"], dataset_id,
                                    {"source_timezone": "Asia/Shanghai"}, "live-run:" + run_id + ":" + dataset_id)
            with store.transaction() as cx:
                job = cx.execute("SELECT status,result FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job["status"] in {"queued", "running"}:
                with store.transaction() as cx:
                    cx.execute("""UPDATE live_runs SET status='waiting_for_audit',claim=NULL
                                  WHERE id=? AND claim=? AND status='running'""", (run_id, token))
                return True
            if job["status"] not in {"completed", "completed_with_gaps"}:
                raise ValueError("Downstream audit did not complete; no report approval created")
            value = {"job_id": job_id, "status": job["status"]}
        else:
            value = {**cp["validate_import"]["value"], **cp["audit"]["value"],
                     "feedback_refreshed": False, "sent_offers_verified": False,
                     "external_actions": 0, "llm_calls": 0}
        with store.transaction() as cx:
            current = cx.execute("SELECT * FROM live_runs WHERE id=?", (run_id,)).fetchone()
            if current["status"] != "running" or current["claim"] != token:
                return True
            if not authorized(cx, row["actor"]):
                raise ValueError("Owner permission revoked before checkpoint commit")
            cp[step] = {"at": time.time(), "value": value}
            status = "queued"
            if step == "report":
                # Approval creation and final checkpoint are atomic and replay-safe.
                action_id = digest(["live-report", run_id, value["dataset_id"]])
                payload = {"format": "csv_bundle", "live_run_id": run_id}
                envelope = {"kind": "report.export", "dataset_id": value["dataset_id"], "payload": payload}
                cx.execute("INSERT OR IGNORE INTO actions VALUES (?,?,?,?,?,?,?, ?,NULL,NULL,?,NULL)",
                           (action_id, "report.export", row["actor"], value["dataset_id"], store.seal(payload),
                            digest(envelope), "pending", time.time() + 3600, utcnow()))
                value["export_action_id"] = action_id
                status = "awaiting_export_approval"
                store.audit(cx, row["actor"], "approval.proposed", action_id)
            cx.execute("""UPDATE live_runs SET status=?,step=step+1,checkpoint=?,result=?,
              attempts=attempts+1,claim=NULL,lease_until=NULL,error=NULL,updated_at=? WHERE id=?""",
                       (status, store.seal(cp), store.seal(value) if step == "report" else None, utcnow(), run_id))
            store.event(cx, run_id, "live.step.completed", step, {"result_hash": digest(value)})
    except Exception as error:
        # Browser errors need inspection; data/schema errors fail closed.
        status = "waiting_for_user" if step in {"positions", "applications", "interviews"} else "failed"
        message = str(error)[:300] if isinstance(error, ValueError) else type(error).__name__
        with store.transaction() as cx:
            cx.execute("""UPDATE live_runs SET status=?,error=?,attempts=attempts+1,
              claim=NULL,lease_until=NULL,updated_at=? WHERE id=? AND claim=? AND status='running'""",
                       (status, message, utcnow(), run_id, token))
            store.event(cx, run_id, "live.step.stopped", step, {"error_type": type(error).__name__})
    return True
