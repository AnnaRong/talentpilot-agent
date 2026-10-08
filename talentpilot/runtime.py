"""Bounded specialist orchestration with persistent queue, events and checkpoints."""
from __future__ import annotations

import threading
import time
import uuid
import hashlib
from pathlib import Path
from dataclasses import asdict, dataclass

from . import workflows
from .platform_store import Store, digest, utcnow
from .model_agent import explain_metrics


@dataclass(frozen=True)
class Agent:
    id: str
    goal: str
    tools: tuple[str, ...]
    memory: str = "agent + immutable dataset + options"
    permission: str = "read_snapshot_write_local_result"
    mode: str = "deterministic_specialist"


AGENTS = {
    "analytics": Agent("analytics", "Calculate scoped funnel from explicit outcomes", ("analytics",)),
    "insights": Agent("insights", "Compare sourced interviewer conclusions without bias labels", ("insights",)),
    "locks": Agent("locks", "Detect fresh lock expirations and state changes", ("locks",)),
    "talent_pool": Agent("talent_pool", "Prepare evidence and safety gates for human review", ("recommend",)),
    "loads": Agent("loads", "Count non-cancelled interviews by stable interviewer ID", ("loads",)),
    "analyst": Agent("analyst", "Explain only tool-verified anonymous numeric metrics", ("explain_metrics",),
                     permission="numeric_lookup_only_no_hiring_actions", mode="bounded_llm_tool_loop"),
    "scorecards": Agent("scorecards", "Compare sourced dimensions within one interview without imputing missing scores",
                       ("scorecards",)),
}
TOOLS = {"analytics": workflows.analytics, "insights": workflows.insights,
         "locks": workflows.locks, "recommend": workflows.recommend, "loads": workflows.loads,
         "explain_metrics": explain_metrics, "scorecards": workflows.scorecards}
PIPELINES = {"audit": ["analytics", "insights", "locks", "loads"],
             **{key: [key] for key in AGENTS}}
WORKFLOW_REVISION = hashlib.sha256(
    Path(workflows.__file__).read_bytes() + Path(__file__).with_name("model_agent.py").read_bytes()
).hexdigest()


class Context:
    def __init__(self, runtime, job, agent, deadline):
        self.runtime, self.job, self.agent, self.deadline = runtime, job, agent, deadline

    def call(self, tool, capture, options):
        if tool not in self.agent.tools:
            raise PermissionError("Agent tool permission denied")
        self.runtime.check_budget(self.job["id"], self.deadline)
        result = TOOLS[tool](capture, options) if tool in {"loads", "recommend", "explain_metrics"} else TOOLS[tool](capture)
        if time.monotonic() > self.deadline:
            raise TimeoutError("Specialist exceeded task deadline; result not committed")
        return result


class Runtime:
    def __init__(self, root):
        self.store = Store(root)
        from .operations import initialize
        initialize(self.store)
        from .knowledge import initialize as initialize_knowledge
        initialize_knowledge(self.store)
        from .live_runs import initialize as initialize_live_runs
        initialize_live_runs(self.store)
        from .resume_registry import initialize as initialize_resumes
        initialize_resumes(self.store)
        from .upload_batches import initialize as initialize_upload_batches
        initialize_upload_batches(self.store)
        from .talent_pool import initialize as initialize_talent_pool
        initialize_talent_pool(self.store)
        from .boss_sourcing import initialize as initialize_boss_sourcing
        initialize_boss_sourcing(self.store)
        from .lock_monitor import initialize as initialize_lock_monitor
        initialize_lock_monitor(self.store)
        from .autonomous import initialize as initialize_autonomous
        initialize_autonomous(self.store)
        self.stop_event = threading.Event()
        self.thread = None

    def submit(self, kind, actor, dataset_id, options, request_key, budget=8, timeout=60):
        if kind not in PIPELINES:
            raise ValueError("Unsupported workflow")
        self.store.dataset(dataset_id)
        payload = {"options": options, "budget": budget, "timeout": timeout}
        request_key = digest([actor, request_key])
        with self.store.transaction() as cx:
            old = cx.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if old:
                if old["kind"] != kind or old["dataset_id"] != dataset_id or self.store.unseal(old["payload"]) != payload:
                    raise ValueError("Idempotency key reused with different request")
                return old["id"]
            job_id = uuid.uuid4().hex
            cx.execute("""INSERT INTO jobs
              (id,request_key,kind,actor,dataset_id,payload,status,max_attempts,budget,
               timeout_seconds,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued',2,?,?,?,?)""",
                       (job_id, request_key, kind, actor, dataset_id, self.store.seal(payload),
                        budget, timeout, utcnow(), utcnow()))
            self.store.event(cx, job_id, "job.queued", "orchestrator", {"kind": kind})
            self.store.audit(cx, actor, "job.submit", job_id)
        return job_id

    def claim(self):
        with self.store.transaction() as cx:
            # Read-only computations can be retried after a lease expires; never external effects.
            cx.execute("""UPDATE jobs SET status='queued',lease_until=NULL
              WHERE status='running' AND lease_until<? AND attempts<max_attempts""", (time.time(),))
            cx.execute("""UPDATE jobs SET status='failed',error='Worker lease expired; retries exhausted'
              WHERE status='running' AND lease_until<? AND attempts>=max_attempts""", (time.time(),))
            row = cx.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            cx.execute("""UPDATE jobs SET status='running',attempts=attempts+1,
              lease_until=?,updated_at=? WHERE id=?""",
                       (time.time() + row["timeout_seconds"] + 30, utcnow(), row["id"]))
            self.store.event(cx, row["id"], "job.claimed", "orchestrator", {"attempt": row["attempts"] + 1})
            return dict(row)

    def check_budget(self, job_id, deadline):
        if time.monotonic() > deadline:
            raise TimeoutError("Task deadline reached")
        with self.store.transaction() as cx:
            row = cx.execute("SELECT status,used,budget FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["status"] == "cancelled":
                raise InterruptedError("Task cancelled")
            if row["used"] >= row["budget"]:
                raise ValueError("Tool-call budget exhausted")
            cx.execute("UPDATE jobs SET used=used+1 WHERE id=?", (job_id,))

    def tick(self):
        job = self.claim()
        if not job:
            return False
        deadline = time.monotonic() + job["timeout_seconds"]
        result = {}
        try:
            capture = self.store.dataset(job["dataset_id"])
            options = self.store.unseal(job["payload"])["options"]
            for agent_id in PIPELINES[job["kind"]]:
                agent = AGENTS[agent_id]
                subject = digest([WORKFLOW_REVISION, job["dataset_id"], options])
                with self.store.transaction() as cx:
                    old = cx.execute("SELECT payload FROM memories WHERE agent=? AND subject=?",
                                     (agent_id, subject)).fetchone()
                    self.store.event(cx, job["id"], "agent.started", agent_id, {"tools": agent.tools})
                # Time-sensitive monitors must re-evaluate; other immutable evidence can reuse checkpoints.
                cached = old is not None and agent_id not in {"locks", "talent_pool", "loads", "analyst"}
                if cached:
                    self.check_budget(job["id"], deadline)
                    value = self.store.unseal(old["payload"])
                else:
                    value = Context(self, job, agent, deadline).call(agent.tools[0], capture, options)
                result[agent_id] = value
                with self.store.transaction() as cx:
                    cx.execute("INSERT OR REPLACE INTO memories VALUES (?,?,?,?)",
                               (agent_id, subject, self.store.seal(value), utcnow()))
                    if agent_id == "locks":
                        for alert in value["alerts"]:
                            alert_id = digest(alert)
                            cx.execute("INSERT OR IGNORE INTO alerts VALUES (?,?,?,?)",
                                       (alert_id, job["dataset_id"], self.store.seal(alert), utcnow()))
                    self.store.event(cx, job["id"], "agent.completed", agent_id,
                                     {"cached": cached, "result_hash": digest(value)})
            with self.store.transaction() as cx:
                status = "completed_with_gaps" if any(v.get("status") in {"awaiting_source", "awaiting_configuration", "partial"}
                                                    for v in result.values()) else "completed"
                cx.execute("""UPDATE jobs SET status=?,result=?,lease_until=NULL,updated_at=?
                  WHERE id=? AND status='running'""",
                           (status, self.store.seal(result), utcnow(), job["id"]))
                self.store.event(cx, job["id"], "job.finished", "evaluator",
                                 {"status": status, "external_actions": 0,
                                  "llm_calls": sum(v.get("llm_calls", 0) for v in result.values())})
        except InterruptedError:
            pass
        except Exception as error:
            with self.store.transaction() as cx:
                # Data/permission/budget errors are terminal. Only worker crashes are retried.
                cx.execute("""UPDATE jobs SET status='failed',error=?,lease_until=NULL,updated_at=?
                  WHERE id=? AND status='running'""", (str(error)[:500], utcnow(), job["id"]))
                self.store.event(cx, job["id"], "job.failed", "evaluator", {"error_type": type(error).__name__})
        return True

    def start(self):
        self.stop_event.clear()
        def work():
            while not self.stop_event.is_set():
                try:
                    from .operations import dispatch_due
                    dispatch_due(self)
                    from .lock_monitor import dispatch_due as dispatch_lock_monitor
                    dispatch_lock_monitor(self)
                    from .boss_sourcing import dispatch_due as dispatch_boss_sourcing
                    dispatch_boss_sourcing(self.store)
                    from .autonomous import dispatch_due as dispatch_autonomous, tick as autonomous_tick
                    dispatch_autonomous(self.store)
                    autonomous_work = autonomous_tick(self)
                    from .live_runs import tick as live_tick
                    live_work = live_tick(self)
                    if not self.tick() and not live_work and not autonomous_work:
                        self.stop_event.wait(0.5)
                except Exception:
                    self.stop_event.wait(2)
        self.thread = threading.Thread(target=work, daemon=True, name="talentpilot-durable-worker")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=420)

    def jobs(self):
        with self.store.transaction() as cx:
            rows = cx.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
        return [self.public_job(row) for row in rows]

    def public_job(self, row):
        result = dict(row)
        result.pop("request_key", None)
        result["payload"] = self.store.unseal(result["payload"])
        result["result"] = self.store.unseal(result["result"])
        return result

    def capabilities(self):
        return {
            "architecture": "local_hybrid_runtime", "autonomous_multi_llm": False,
            "agents": [asdict(a) for a in AGENTS.values()],
            "queue": "SQLite persistent queue; single local worker; crash leases",
            "external": {
                "italent": {"state": "live_inventory_and_gated_recommendation",
                            "recommendation_preflight": "authenticated_browser_exact_target_and_duplicate_check",
                            "write_enabled": __import__("os").getenv("ITALENT_RECOMMEND_WRITE_ENABLED") == "1",
                            "write_requires": "current confirmation, five-minute fingerprint, immediate GetJoblistOfApplicant read-back",
                            "feedback_body_refresh": False},
                "autonomous_reads": {"state": "daily_durable_recruiting_workflow", "external_write": False,
                                     "schedule": "11:00 Asia/Shanghai",
                                     "steps": ["positions", "applications", "interviews", "validate_import", "audit", "report"],
                                     "export": "requires_explicit_approval"},
                "boss": {"state": "cli_search_and_local_review",
                         "adapter": "@viyzhu/boss-cli-fork@0.8.1",
                         "search": "YAML filters, cross-keyword dedup and Top N local ranking",
                         "external_write": False,
                         "requires": "Visible browser login; recruiter review; stable candidate ID before outreach"},
                "ruliu": {"state": "not_connected", "requires": "Document binding and authorized write API"},
                "mcp": {"state": "metadata_allowlist_client", "requires": "Approved InstSci executable; per-call handshake",
                        "downloads": False},
                "overseas": {"state": "human_opened_profile_collection_and_local_resume_export",
                             "public_collection": True, "live_smoke_verified": False,
                             "automatic_outreach": False},
                "email": {"state": "human_login_then_agent_collects_and_saves",
                          "credentials_stored": False, "automatic_mailbox_access_after_handoff": True,
                          "attachment_type": "pdf",
                          "filename": "job+name"},
                "notification": {"state": "local_inbox_only", "external_send": False},
                "lock_monitor": {"state": "live_read_only_monitor_local_reminders",
                                 "recall": "skill_integrated_write_blocked_pending_verification",
                                 "automatic_recall": False},
                "recording": {"state": "user_selected_browser_recording", "verified_task_video": False,
                              "requires": "User selects the recruitment tab/window; no automatic desktop capture"},
                "ppm": {"state": "versioned_local_panels", "ats_write": False},
                "scheduler": {"state": "durable_read_only_schedules", "requires_fresh_dataset": True},
                "knowledge": {"state": "encrypted_local_source_retrieval", "method": "lexical_not_vector"},
                "candidate_history": {"state": "batch_resume_to_italent_read_only_match",
                         "identity": "resume_contact_or_name_with_manual_review", "ats_write": False},
                "talent_pool": {"state": "skill_search_and_authenticated_recommendation_preflight",
                                "search": "screen-italent-resumes; no PDF download",
                                "recommendation": "explicit confirmation and immediate per-candidate read-back; write switch defaults off"},
                "resume_upload": {"state": "preview_ready_write_blocked_pending_live_verification",
                         "target": "简历复筛/进行中", "defaults": "BOSS直聘/主动投递"},
            },
            "model": {"state": "platform_selection_grill_and_optional_analyst_require_approved_gateway",
                      "platform_selection": "one-question-at-a-time Grill Me interview",
                      "resume_direction": "TypeSafe Jev resume-to-JD evidence guidance",
                      "note": "Calls fail closed when approved LLM or Jev egress is not configured."},
        }
