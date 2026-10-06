import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from talentpilot import lock_monitor as locks
from talentpilot.runtime import Runtime
import test_platform


def stamp(hours=0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def inventory(records=None):
    return {"complete": True, "collected_at": stamp(), "records": records if records is not None else [
        {"source": "followed", "candidate_id": "synthetic-person", "name": "测试候选人",
         "job_id": "J_TEST", "locked": True, "unlock_at": stamp(2),
         "has_permission": True, "deleted": False, "followed": True}]}


class LockMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.temp.name))
        self.store = self.runtime.store

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_deadline_does_not_prove_unlock_and_missing_is_unknown(self):
        item = {"locked": True, "observed_at": stamp(), "unlock_at": stamp(-1)}
        self.assertEqual(locks.classify(item, 24)["state"], "verify_due")
        item["unlock_at"] = None
        self.assertEqual(locks.classify(item, 24)["state"], "unknown")
        item["locked"] = False
        self.assertEqual(locks.classify(item, 24)["state"], "unlocked")
        item["observed_at"] = stamp(-2)
        self.assertEqual(locks.classify(item, 24)["state"], "stale")
        item["observed_at"] = stamp(1)
        self.assertEqual(locks.classify(item, 24)["state"], "stale")

    def test_unknown_timezone_and_boundary(self):
        item = {"locked": True, "observed_at": stamp(), "unlock_at": "2026-09-23 12:00:00"}
        self.assertEqual(locks.classify(item, 24)["state"], "unknown")
        now = time.time()
        item["unlock_at"] = datetime.fromtimestamp(now + 24 * 3600, timezone.utc).isoformat()
        self.assertEqual(locks.classify(item, 24, now)["state"], "expiring")

    def test_persistence_dedup_ack_and_actor_isolation(self):
        source = inventory()
        first = locks.refresh(self.store, "local-admin", lambda: source)
        self.assertEqual(first["counts"]["expiring"], 1)
        locks.refresh(self.store, "local-admin", lambda: source)
        self.assertEqual(len(locks.overview(self.store, "local-admin")["notifications"]), 1)
        self.assertEqual(locks.overview(self.store, "other")["items"], [])
        locks.acknowledge(self.store, "local-admin")
        reopened = Runtime(Path(self.temp.name))
        self.assertTrue(locks.overview(reopened.store, "local-admin")["notifications"][0]["read_at"])
        with self.store.transaction() as cx:
            self.assertNotIn("测试候选人".encode(), cx.execute("SELECT payload FROM lock_watches").fetchone()[0])
        reopened.close()

    def test_incomplete_failure_preserves_previous_observation(self):
        first = locks.refresh(self.store, "local-admin", inventory)
        with self.assertRaises(ValueError):
            locks.refresh(self.store, "local-admin", lambda: {"complete": False})
        after = locks.overview(self.store, "local-admin")
        self.assertEqual(first["items"], after["items"])
        self.assertEqual(first["last_success"], after["last_success"])
        self.assertTrue(after["last_error"])
        self.assertFalse(after["running"])

    def test_empty_complete_list_retires_stale_rows(self):
        locks.refresh(self.store, "local-admin", inventory)
        result = locks.refresh(self.store, "local-admin", lambda: inventory([]))
        self.assertEqual(result["items"][0]["state"], "out_of_scope")

    def test_recruiting_lock_loss_is_not_silently_removed(self):
        source = inventory()
        record = source["records"][0]
        record.update(source="recruiting", current_job_locked=True)
        locks.refresh(self.store, "local-admin", lambda: source)
        record.update(locked=False, current_job_locked=False)
        result = locks.refresh(self.store, "local-admin", lambda: source)
        self.assertEqual(result["items"][0]["state"], "lost")
        self.assertEqual(result["counts"]["lost"], 1)
        self.assertTrue(any(n["state"] == "lost" for n in result["notifications"]))
        source["records"].append({**record, "candidate_id": "not-previously-monitored"})
        result = locks.refresh(self.store, "local-admin", lambda: source)
        self.assertEqual(len(result["items"]), 1)

    def test_upload_preview_is_not_upload_success(self):
        result = {"job_id": "J_TARGET", "collected_at": stamp(), "results": [
            {"status": "history_found", "candidate_name": "测试候选人", "applications": [
                {"candidate_id": "synthetic-person", "locked": True}]}]}
        self.assertEqual(locks.track_upload(self.store, "local-admin", "batch-test", result), 1)
        watch = locks.overview(self.store, "local-admin")["items"][0]
        self.assertEqual(watch["provenance"], "upload_blocked")
        self.assertEqual(watch["state"], "unknown")
        locks.refresh(self.store, "local-admin", inventory)
        watch = next(i for i in locks.overview(self.store, "local-admin")["items"] if i["source"] == "upload")
        self.assertEqual(watch["state"], "expiring")
        self.assertEqual(watch["job_id"], "J_TARGET")

    def test_ambiguous_upload_identity_not_tracked(self):
        result = {"job_id": "J_TARGET", "results": [
            {"status": "manual_review", "applications": [{"candidate_id": "p", "locked": True}]}]}
        self.assertEqual(locks.track_upload(self.store, "local-admin", "batch-test", result), 0)

    def test_schedule_disabled_default_and_revoked_owner(self):
        locks.configure(self.store, "local-admin", False, 30, 24)
        with patch("talentpilot.italent_browser.collect_locks", return_value=inventory()) as collect:
            locks.dispatch_due(self.runtime)
            collect.assert_not_called()
            locks.configure(self.store, "local-admin", True, 30, 24)
            locks.dispatch_due(self.runtime)
            collect.assert_called_once()
            locks.dispatch_due(self.runtime)
            collect.assert_called_once()
            locks.configure(self.store, "local-admin", True, 30, 24)
            with self.store.transaction() as cx:
                cx.execute("UPDATE users SET enabled=0 WHERE id='local-admin'")
            locks.dispatch_due(self.runtime)
            collect.assert_called_once()

    def test_recall_confirmation_is_bound_and_never_writes(self):
        source = inventory()
        source["records"][0]["locked"] = False
        result = locks.refresh(self.store, "local-admin", lambda: source)
        with patch("talentpilot.boss.latest_position", return_value={"title": "测试岗位"}):
            preview = locks.recall_preview(self.store, "local-admin", result["items"][0]["id"], "J_TARGET", "简历复筛/进行中")
        with self.assertRaises(ValueError):
            locks.confirm_recall(self.store, "other", preview["preview_id"], preview["fingerprint"])
        with self.assertRaises(ValueError):
            locks.confirm_recall(self.store, "local-admin", preview["preview_id"], "wrong")
        result = locks.confirm_recall(self.store, "local-admin", preview["preview_id"], preview["fingerprint"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["external_actions"], 0)
        with self.assertRaises(ValueError):
            locks.confirm_recall(self.store, "local-admin", preview["preview_id"], preview["fingerprint"])

    def test_bad_duplicates_and_permissions(self):
        source = inventory()
        source["records"] *= 2
        with self.assertRaises(ValueError):
            locks.refresh(self.store, "local-admin", lambda: source)
        with self.assertRaises(ValueError):
            locks.refresh(self.store, "unknown", inventory)
        with self.assertRaises(ValueError):
            locks.configure(self.store, "local-admin", True, 1, 24)

    def test_permission_revoked_during_read_discards_result(self):
        def collect():
            with self.store.transaction() as cx:
                cx.execute("UPDATE users SET role='hr' WHERE id='local-admin'")
            return inventory()
        with self.assertRaises(ValueError):
            locks.refresh(self.store, "local-admin", collect)
        self.assertEqual(locks.overview(self.store, "local-admin")["items"], [])

    def test_unknown_current_job_lock_is_not_reported_as_lost(self):
        item = {"source": "recruiting", "locked": None, "current_job_locked": None,
                "observed_at": stamp(), "unlock_at": None}
        self.assertEqual(locks.classify(item, 24)["state"], "unknown")


class LockMonitorApiTests(unittest.TestCase):
    setUp = test_platform.ApiTests.setUp
    tearDown = test_platform.ApiTests.tearDown

    def test_monitor_api_without_dataset(self):
        response = self.client.get("/api/platform/lock-monitor")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["settings"]["enabled"])
        self.assertFalse(response.json()["recall_write_enabled"])
        self.assertEqual(self.client.post("/api/platform/lock-monitor/settings", json={
            "enabled": False, "interval_minutes": 30, "warning_hours": 12}).status_code, 200)
        self.assertEqual(self.client.post("/api/platform/lock-monitor/settings", json={
            "enabled": True, "interval_minutes": 2, "automatic_recall": True}).status_code, 422)
        with patch("talentpilot.italent_browser.collect_locks", return_value=inventory()):
            response = self.client.post("/api/platform/lock-monitor/refresh", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counts"]["expiring"], 1)
        self.assertEqual(self.client.post("/api/platform/lock-monitor/acknowledge", json={}).status_code, 200)
        self.assertEqual(self.client.get("/assets/lock-monitor.js").status_code, 200)
        self.assertEqual(self.client.get("/assets/platform.key").status_code, 404)
