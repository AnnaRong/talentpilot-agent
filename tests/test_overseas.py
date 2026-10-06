import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from talentpilot import overseas, privacy
from talentpilot.runtime import Runtime


class OverseasRecruitmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root / "platform")
        self.store = self.runtime.store
        with self.store.transaction() as cx:
            self.store.event(cx, None, "italent.inventory.collected", "sourcing", {
                "positions": [{"job_id": "J900001", "title": "海外业务经理"}]
            })
        self.payload = {
            "job_id": "J900001",
            "job_title": "海外业务经理",
            "region": "Singapore",
            "consent_evidence": "Synthetic authorized test profile",
            "retention_days": 90,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "profile": {
                "name": "Synthetic Candidate",
                "headline": "Business Development",
                "location": "Singapore",
                "about": "Synthetic profile for tests.",
                "linkedin_url": "https://www.linkedin.com/in/synthetic-candidate/?trk=test",
                "experience": ["Example Co - Manager"],
                "education": ["Example University"],
                "skills": ["Partnerships", "English"],
                "languages": ["English"],
                "certifications": [],
                "projects": [],
            },
        }

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_collect_deduplicate_encrypt_and_export(self):
        first = overseas.collect_profile(self.store, "test-admin", self.payload)
        duplicate = overseas.collect_profile(self.store, "test-admin", self.payload)
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["record_id"], duplicate["record_id"])
        self.assertEqual(overseas.latest_positions(self.store)[0]["job_id"], "J900001")

        with self.store.transaction() as cx:
            encrypted = cx.execute(
                "SELECT payload FROM overseas_resumes WHERE id=?", (first["record_id"],)
            ).fetchone()["payload"]
        self.assertNotIn(b"Synthetic Candidate", encrypted)

        profiles = overseas.list_profiles(self.store, "J900001")
        self.assertEqual([item["name"] for item in profiles], ["Synthetic Candidate"])
        with patch.object(overseas.settings, "exports_dir", self.root / "exports"):
            exported = overseas.export_resumes(
                self.store, "test-admin", "J900001", [first["record_id"]]
            )
        with zipfile.ZipFile(exported["path"]) as archive:
            names = archive.namelist()
            self.assertIn("resumes/Synthetic Candidate.docx", names)
            self.assertIn("resumes/Synthetic Candidate.md", names)
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["schema"], "talentpilot-overseas-resume-export/v1")
        self.assertEqual(manifest["resumes"][0]["name"], "Synthetic Candidate")
        deleted = privacy.delete(self.store, "test-admin", first["candidate_id"])
        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(overseas.list_profiles(self.store, "J900001"), [])

    def test_rejects_non_linkedin_profile_url(self):
        payload = {**self.payload, "profile": {**self.payload["profile"], "linkedin_url": "https://example.com/in/person"}}
        with self.assertRaisesRegex(ValueError, "linkedin.com"):
            overseas.collect_profile(self.store, "test-admin", payload)


if __name__ == "__main__":
    unittest.main()
