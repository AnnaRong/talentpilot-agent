"""Immutable approval envelopes. No unconfigured external action may execute."""
from __future__ import annotations

import time
import uuid

from .platform_store import digest, utcnow


EXTERNAL_ACTIONS = {"boss.outreach", "resume.download", "resume.upload", "talent.advance",
                    "ruliu.publish", "schedule.override", "overseas.collect"}
LOCAL_ACTIONS = {"report.export"}


def propose(store, actor, kind, dataset_id, payload):
    if kind not in EXTERNAL_ACTIONS | LOCAL_ACTIONS:
        raise ValueError("Unknown action")
    if kind == "report.export" and payload.get("format", "json") not in {"json", "csv_bundle"}:
        raise ValueError("Export format must be json or csv_bundle")
    store.dataset(dataset_id)
    if kind == "ruliu.publish" and not payload.get("document_id"):
        raise ValueError("Explicit target document_id required")
    if kind == "talent.advance" and not all(payload.get(k) for k in
                                           ("candidate_ids", "job_id", "stage", "state_fingerprint")):
        raise ValueError("Exact candidates/job/stage/current-state fingerprint required")
    envelope = {"kind": kind, "dataset_id": dataset_id, "payload": payload}
    action_id = uuid.uuid4().hex
    with store.transaction() as cx:
        cx.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?, ?,NULL,NULL,?,NULL)",
                   (action_id, kind, actor, dataset_id, store.seal(payload), digest(envelope),
                    "pending", time.time() + 3600, utcnow()))
        store.audit(cx, actor, "approval.proposed", action_id)
    return action_id


def decide(store, action_id, actor, decision, expected_hash, note=""):
    if decision not in {"approved", "rejected"}:
        raise ValueError("Invalid decision")
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise ValueError("Action not found")
        actual = digest({"kind": row["kind"], "dataset_id": row["dataset_id"],
                         "payload": store.unseal(row["payload"])})
        if row["status"] != "pending" or row["expires"] < time.time():
            raise ValueError("Action already decided or expired")
        if expected_hash != row["payload_hash"] or actual != expected_hash:
            raise ValueError("Approval payload hash mismatch")
        cx.execute("UPDATE actions SET status=?,decided_by=?,decision_note=? WHERE id=?",
                   (decision, actor, note, action_id))
        store.audit(cx, actor, f"approval.{decision}", action_id)


def execute(store, action_id, actor):
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not row or row["status"] != "approved" or row["expires"] < time.time():
            raise ValueError("A fresh, unconsumed approval is required")
        payload = store.unseal(row["payload"])
        if digest({"kind": row["kind"], "dataset_id": row["dataset_id"], "payload": payload}) != row["payload_hash"]:
            raise ValueError("Action changed since approval")
        if row["kind"] in EXTERNAL_ACTIONS:
            result = {"status": "blocked", "reason": "Authorized connector and live read-back not configured",
                      "external_actions": 0}
            cx.execute("UPDATE actions SET status='blocked',result=? WHERE id=?",
                       (store.seal(result), action_id))
            store.audit(cx, actor, "action.blocked", action_id)
            return result
        dataset = cx.execute("SELECT payload FROM datasets WHERE id=?", (row["dataset_id"],)).fetchone()
        if not dataset:
            raise ValueError("Approved dataset no longer exists")
        # Delivery is the authenticated HTTP response itself; this approval is consumed once.
        capture = store.unseal(dataset["payload"])
        if payload.get("format") == "csv_bundle":
            import base64
            from .ledger_export import export_bundle
            content = export_bundle(capture)
            result = {"status": "executed", "format": "csv_bundle",
                      "filename": "talentpilot-ledger.zip",
                      "content_base64": base64.b64encode(content).decode(), "external_actions": 0}
            cx.execute("UPDATE actions SET status='executed',result=? WHERE id=?",
                       (store.seal({"status": "executed", "format": "csv_bundle",
                                    "dataset_id": row["dataset_id"]}), action_id))
            store.audit(cx, actor, "report.export.csv", action_id)
            return result
        from .workflows import analytics
        report = analytics(capture)
        result = {"status": "executed", "report": report, "format": "json", "external_actions": 0}
        cx.execute("UPDATE actions SET status='executed',result=? WHERE id=?",
                   (store.seal({"status": "executed", "report_hash": digest(report)}), action_id))
        store.audit(cx, actor, "report.export", action_id)
        return result
