import copy
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from talentpilot import actions, live_runs, workflows
from talentpilot.platform_store import utcnow
from talentpilot.runtime import Runtime
from test_platform import sample


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Path(self.tmp.name))
        self.store = self.runtime.store
        self.capture = sample()
        self.capture["interviews"] = []
        self.capture["quality"]["coverage"]["interviews"]["declared"] = 0
        interview = sample()["interviews"][0]
        interview["scheduled_at"] = "2026/09/20 10:00:00"
        self.positions = {"positions": self.capture["positions"], "collected_at": utcnow()}
        self.interviews = {"interviews": [interview], "collected_at": utcnow(),
                           "scope": "SYNTHETIC", "declared": 1, "captured": 1, "pages": 1}
        self.capture["quality"]["pages"] = {}
        self.patches = [
            patch.object(live_runs.italent_browser, "collect_positions", return_value=self.positions),
            patch.object(live_runs.italent_browser, "collect_applications", return_value=self.capture),
            patch.object(live_runs.italent_browser, "collect_interviews", return_value=self.interviews),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.runtime.close()
        self.tmp.cleanup()

    def submit(self, key="request-test"):
        return live_runs.submit(self.runtime, "local-admin", key)

    def finish(self):
        for _ in range(9):
            live_runs.tick(self.runtime)
            self.runtime.tick()

    def state(self):
        return live_runs.list_runs(self.store)[0]

    def test_complete_chain_with_real_approval_gate(self):
        self.submit()
        self.finish()
        run = self.state()
        self.assertEqual(run["completed_steps"], list(live_runs.STEPS))
        self.assertEqual(run["status"], "awaiting_export_approval")
        result = run["result"]
        self.assertEqual(result["application_count"], 1)
        self.assertFalse(result["feedback_refreshed"])
        with self.store.transaction() as cx:
            approval = dict(cx.execute("SELECT * FROM actions").fetchone())
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            actions.execute(self.store, approval["id"], "local-admin")
        actions.decide(self.store, approval["id"], "local-admin", "approved", approval["payload_hash"])
        self.assertTrue(actions.execute(self.store, approval["id"], "local-admin")["content_base64"])
        live_runs.tick(self.runtime)
        self.assertEqual(self.state()["status"], "completed_with_gaps")

    def test_idempotency_and_single_active_run(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(lambda _: self.submit(), range(8)))
        self.assertEqual(len(set(ids)), 1)
        with self.assertRaises(ValueError):
            self.submit("different")
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: live_runs.claim(self.store), range(4)))
        self.assertEqual(sum(r is not None for r in rows), 1)

    def test_list_shows_only_latest_run_from_today(self):
        first = self.submit("first")
        with self.store.transaction() as cx:
            cx.execute("UPDATE live_runs SET status='completed', created_at=? WHERE id=?",
                       ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                        first))
        latest = self.submit("latest")
        self.assertEqual([run["id"] for run in live_runs.list_runs(self.store)], [latest])

    def test_browser_failure_pauses_without_downstream(self):
        self.submit()
        self.mocks[1].side_effect = ValueError("Login required")
        self.finish()
        self.assertEqual(self.state()["status"], "waiting_for_user")
        self.assertEqual(self.state()["completed_steps"], ["positions"])
        self.mocks[2].assert_not_called()
        with self.store.transaction() as cx:
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0)
        self.mocks[1].side_effect = None
        live_runs.control(self.runtime, self.state()["id"], "local-admin", "resume")
        self.finish()
        self.assertEqual(self.state()["status"], "awaiting_export_approval")
        self.mocks[0].assert_called_once()

    def test_application_pager_uses_current_component_control(self):
        source = (Path(live_runs.italent_browser.__file__).with_name("collect_applications.js")).read_text()
        self.assertIn("handlePageChange", source)
        self.assertNotIn("textContent.trim()==='下一页'", source)

    def test_cancel_discards_inflight_checkpoint(self):
        run_id = self.submit()
        def cancel_during_read():
            live_runs.control(self.runtime, run_id, "local-admin", "cancel")
            return self.positions
        self.mocks[0].side_effect = cancel_during_read
        self.finish()
        self.assertEqual(self.state()["status"], "cancelled")
        self.assertEqual(self.state()["completed_steps"], [])
        self.mocks[1].assert_not_called()

    def test_restart_lease_requires_explicit_resume(self):
        run_id = self.submit()
        live_runs.claim(self.store)
        with self.store.transaction() as cx:
            cx.execute("UPDATE live_runs SET lease_until=?", (time.time() - 1,))
        restarted = Runtime(self.store.root)
        self.assertFalse(live_runs.tick(restarted))
        self.assertEqual(self.state()["status"], "waiting_for_user")
        self.mocks[0].assert_not_called()
        live_runs.control(restarted, run_id, "local-admin", "resume")
        live_runs.tick(restarted)
        self.assertEqual(self.state()["completed_steps"], ["positions"])

    def test_stale_checkpoint_restarts_all_reads(self):
        run_id = self.submit()
        live_runs.tick(self.runtime)
        with self.store.transaction() as cx:
            row = cx.execute("SELECT checkpoint FROM live_runs").fetchone()
            cp = self.store.unseal(row["checkpoint"])
            cp["positions"]["at"] = time.time() - 1900
            cx.execute("UPDATE live_runs SET checkpoint=?,status='waiting_for_user'", (self.store.seal(cp),))
        live_runs.control(self.runtime, run_id, "local-admin", "resume")
        self.assertEqual(self.state()["completed_steps"], [])
        self.finish()
        self.assertEqual(self.mocks[0].call_count, 2)

    def test_slow_application_read_is_not_rejected_only_because_positions_are_old(self):
        self.submit()
        live_runs.tick(self.runtime)
        with self.store.transaction() as cx:
            row = cx.execute("SELECT checkpoint FROM live_runs").fetchone()
            cp = self.store.unseal(row["checkpoint"])
            cp["positions"]["at"] = time.time() - 1900
            cx.execute("UPDATE live_runs SET checkpoint=?", (self.store.seal(cp),))
        live_runs.tick(self.runtime)
        self.assertEqual(self.state()["status"], "queued")
        self.assertEqual(self.state()["completed_steps"], ["positions", "applications"])
        self.mocks[1].assert_called_once()

    def test_permission_rechecked_before_each_step(self):
        self.submit()
        live_runs.tick(self.runtime)
        with self.store.transaction() as cx:
            cx.execute("UPDATE users SET enabled=0")
        self.finish()
        self.assertEqual(self.state()["status"], "failed")
        self.mocks[1].assert_not_called()

    def test_mismatched_identity_fails_closed(self):
        self.submit()
        self.interviews["interviews"][0]["candidate_id"] = "WRONG"
        self.finish()
        self.assertEqual(self.state()["status"], "failed")
        with self.store.transaction() as cx:
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 0)

    def test_no_claim_after_retry_budget(self):
        run_id = self.submit()
        with self.store.transaction() as cx:
            cx.execute("UPDATE live_runs SET attempts=12,status='failed'")
        with self.assertRaises(ValueError):
            live_runs.control(self.runtime, run_id, "local-admin", "resume")

    def test_scorecards_missing_values_and_rounds(self):
        capture = sample()
        persons = capture["interviews"][0]["interviewers"]
        persons[0].update(scores={"skill": 4, "experience": 3}, score_scale="1-5")
        persons[1].update(scores={"skill": 2}, score_scale="1-5")
        second = copy.deepcopy(capture["interviews"][0])
        second.update(interview_id="second", round="second")
        capture["interviews"].append(second)
        groups = workflows.scorecards(capture)["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["dimensions"]["skill"]["mean"], 3)
        self.assertEqual(groups[0]["dimensions"]["experience"]["n"], 1)
        self.assertEqual(groups[0]["dimensions"]["experience"]["mean"], 3)

    def test_scorecard_duplicate_conflict_and_unknown_scale(self):
        capture = sample()
        person = capture["interviews"][0]["interviewers"][0]
        person.update(scores={"skill": 4}, score_scale="1-5")
        duplicate = copy.deepcopy(person)
        duplicate["scores"]["skill"] = 2
        capture["interviews"][0]["interviewers"].append(duplicate)
        self.assertEqual(workflows.scorecards(capture)["groups"], [])

    def test_negative_word_never_matches_pass_substring(self):
        capture = sample()
        capture["interviews"][0]["overall_result"] = "不通过"
        result = workflows.analytics(capture)["jobs"][0]
        self.assertEqual(result["passed"], 0)
        self.assertEqual(result["completed_explicit"], 0)
        capture["interviews"][0]["overall_result"] = "不推荐"
        self.assertEqual(workflows.analytics(capture)["jobs"][0]["passed"], 0)


if __name__ == "__main__":
    unittest.main()
