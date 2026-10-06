"""Local consent/retention vault. Not a certification of overseas legal compliance."""
import time
import uuid

from .platform_store import digest


def collect(store, actor, payload):
    required = ("candidate_id", "purpose", "policy_version", "region", "consent_evidence",
                "consent_at", "expires_at", "record")
    if not all(payload.get(k) for k in required):
        raise ValueError("Consent, purpose, policy, region and retention evidence required")
    from .workflows import parse_time
    expires = parse_time(payload["expires_at"])
    consent_at = parse_time(payload["consent_at"])
    if not expires or not consent_at or consent_at.timestamp() > time.time():
        raise ValueError("Invalid consent/retention timestamp")
    if not time.time() < expires.timestamp() <= time.time() + 366 * 86400:
        raise ValueError("Retention must be future and <=366 days; policy review still required")
    consent_id, record_id = uuid.uuid4().hex, uuid.uuid4().hex
    with store.transaction() as cx:
        cx.execute("INSERT INTO consents VALUES (?,?,?,?,?)",
                   (consent_id, payload["candidate_id"], store.seal({k: v for k, v in payload.items() if k != "record"}),
                    expires.timestamp(), "active"))
        cx.execute("INSERT INTO private_records VALUES (?,?,?,?,?)",
                   (record_id, payload["candidate_id"], consent_id, store.seal(payload["record"]), expires.timestamp()))
        store.audit(cx, actor, "consent.record_local", digest(payload["candidate_id"]))
    return {"record_id": record_id, "consent_id": consent_id, "encrypted": True,
            "scope": "local_vault_only", "public_collection": False}


def delete(store, actor, candidate_id=None):
    with store.transaction() as cx:
        if candidate_id:
            cursor = cx.execute("DELETE FROM private_records WHERE candidate_id=?", (candidate_id,))
            cx.execute("DELETE FROM consents WHERE candidate_id=?", (candidate_id,))
            overseas = cx.execute("DELETE FROM overseas_resumes WHERE candidate_id=?", (candidate_id,))
        else:
            cursor = cx.execute("DELETE FROM private_records WHERE expires<=?", (time.time(),))
            cx.execute("DELETE FROM consents WHERE expires<=?", (time.time(),))
            overseas = cx.execute("DELETE FROM overseas_resumes WHERE expires<=?", (time.time(),))
        count = cursor.rowcount + overseas.rowcount
        store.audit(cx, actor, "vault.delete", digest(candidate_id) if candidate_id else "expired")
    return {"deleted": count, "scope": "local_consent_vault_and_overseas_resumes",
            "not_deleted": ["upstream ATS", "legacy captures", "exports", "recordings", "backups"],
            "full_erasure_complete": False}
