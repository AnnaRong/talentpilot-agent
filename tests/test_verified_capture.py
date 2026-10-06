import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from talentpilot.schemas import ITalentCapture
from talentpilot.verified_capture import capture_status, validate_capture
from talentpilot.reports import build_audit_report
from talentpilot.exports import build_workbook


class VerifiedCaptureTests(unittest.TestCase):
    def setUp(self):
        self.capture = {
            "schema_version": "talentpilot-ledger/2",
            "positions": [{"job_id": "J1", "job_uuid": "job1"}],
            "applications": [{"application_id": "app1", "candidate_id": "person1", "job_id": "J1", "job_uuid": "job1"}],
            "interviews": [{"interview_id": "i1", "application_id": "app1", "candidate_id": "person1", "job_id": "J1"}],
            "quality": {"coverage": {"positions": {"declared": 1}, "applications": {"declared": 1}, "interviews": {"declared": 1}}},
        }

    def test_valid(self):
        validate_capture(self.capture)

    def test_missing_data_is_not_connected(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(capture_status(Path(directory))["state"], "not_verified")

    def test_duplicate_application_rejected(self):
        self.capture["applications"] *= 2
        with self.assertRaises(ValueError):
            validate_capture(self.capture)

    def test_candidate_mismatch_rejected(self):
        self.capture["interviews"][0]["candidate_id"] = "another"
        with self.assertRaises(ValueError):
            validate_capture(self.capture)

    def test_code_uuid_mismatch_rejected(self):
        self.capture["applications"][0]["job_uuid"] = "wrong"
        with self.assertRaises(ValueError):
            validate_capture(self.capture)

    def test_partial_pagination_rejected(self):
        self.capture["quality"]["coverage"]["applications"]["declared"] = 10
        with self.assertRaises(ValueError):
            validate_capture(self.capture)

    def test_new_schema_cannot_silently_enter_old_exporter(self):
        with self.assertRaises(ValidationError):
            ITalentCapture.model_validate(copy.deepcopy(self.capture))

    def test_empty_export_rejected(self):
        with self.assertRaises(ValidationError):
            ITalentCapture.model_validate({})

    def test_legacy_workbook_refuses_new_schema(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                build_workbook(self.capture, Path(directory) / "empty.xlsx")

    def test_uncaptured_offers_remain_unknown(self):
        report = build_audit_report({"offers": [], "coverage": {"offers": "not_collected"}})
        self.assertIsNone(report["summary"]["offer_count"])


if __name__ == "__main__":
    unittest.main()
