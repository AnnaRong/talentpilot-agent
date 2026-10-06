import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from talentpilot import resume_matching as matching


class ResumeMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = patch.object(matching.settings, "data_dir", self.root / "data")
        self.archive = patch.object(matching.settings, "resume_archive_dir", self.root / "resumes")
        self.data.start()
        self.archive.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.data.stop)
        self.addCleanup(self.archive.stop)
        self.folder = self.root / "resumes" / "实习生" / "测试岗位" / "20260924"
        self.folder.mkdir(parents=True)
        self.jd = {"state": "verified_job_jd", "job_id": "J123", "title": "测试岗位",
                   "duties": "负责 Python SQL Excel JavaScript React 数据分析 相关岗位的实际工作。",
                   "qualifications": "要求具备上述相关项目经历，专业技能证据由人工确认。",
                   "collected_at": "2026-09-24T00:00:00+00:00"}

    def test_threshold_and_word_boundaries(self):
        terms = [f"skill{i}" for i in range(10)]
        self.assertFalse(matching.score_text(" ".join(terms[:7]), terms)["high"])
        self.assertTrue(matching.score_text(" ".join(terms[:8]), terms)["high"])
        self.assertEqual(matching.score_text("JavaScript Photoshop", ["Java", "PS"])["score"], 0)
        self.assertIsNone(matching.score_text("text", [])["score"])

    def evaluator(self, jd, text, name=None):
        return {**matching.score_text(text, jd["keywords"]), "model": "synthetic-jev",
                "confidence": 1, "required_skills_probability": 1,
                "hard_requirements_probability": 1, "seniority": "matched"}

    def test_copy_only_dedup_report_and_provenance(self):
        content = "姓名：测试者\n" + "Python SQL Excel JavaScript React 数据分析 项目经历开发实践。\n" * 5
        original = self.folder / "resume.txt"
        original.write_text(content)
        (self.folder / "duplicate.txt").write_text(content)
        (self.folder / "scan.pdf").write_bytes(b"broken")
        profile = matching.save_jd(self.jd)
        matching.save_jd(self.jd)
        self.assertNotIn("Java", profile["keywords"])
        result = matching.run("J123", "实习生/测试岗位", evaluator=self.evaluator)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["high_count"], 1)
        self.assertEqual(result["parse_failed"], 1)
        self.assertEqual(original.read_text(), content)
        copied = next(r for r in result["rows"] if r["copy"])
        self.assertEqual(Path(copied["copy"]).read_bytes(), original.read_bytes())
        self.assertIn("自动匹配结果", Path(result["report"]).read_text())
        matching.high_match_provenance("J123", [copied["file_id"]])
        with self.assertRaises(ValueError):
            matching.high_match_provenance("J123", ["unknown"])
        matching.review_asset(result["run_id"], "report")
        with self.assertRaises(ValueError):
            matching.review_asset(result["run_id"], "../../private")
        Path(copied["copy"]).write_text("changed")
        with self.assertRaises(ValueError):
            matching.high_match_provenance("J123", [copied["file_id"]])
        self.assertEqual(len(matching.folders()), 1)

    def test_missing_jd_invalid_path_and_changed_jd(self):
        with self.assertRaises(ValueError):
            matching.run("J123", "实习生/测试岗位")
        matching.save_jd(self.jd)
        with self.assertRaises(ValueError):
            matching.run("J123", "../outside")
        (self.folder / "good.txt").write_text(self.jd["duties"] * 5)
        result = matching.run("J123", "实习生/测试岗位", evaluator=self.evaluator)
        matching.save_jd({**self.jd, "duties": self.jd["duties"] + "额外要求"})
        with self.assertRaises(ValueError):
            matching.high_match_provenance("J123", [r["file_id"] for r in result["rows"]])

    def test_all_jobs_maps_folders_and_isolates_failures(self):
        (self.folder / "good.txt").write_text(self.jd["duties"] * 5)
        other = self.root / "resumes" / "校招" / "后端开发工程师" / "20260924"
        other.mkdir(parents=True)
        (other / "resume.txt").write_text(self.jd["duties"] * 5)
        positions = [{"job_id": "J123", "title": "测试岗位"},
                     {"job_id": "J124", "title": "2027校园招聘-北京-后端开发工程师-Comate组-张三"},
                     {"job_id": "J125", "title": "不存在岗位"}]
        def read(job_id):
            if job_id == "J125":
                raise ValueError("synthetic failure")
            return {**self.jd, "job_id": job_id,
                    "title": next(p["title"] for p in positions if p["job_id"] == job_id)}
        result = matching.run_all(positions, read, self.evaluator)
        self.assertEqual(result["matched_jobs"], 2)
        self.assertEqual(result["matched_folders"], 2)
        self.assertEqual(result["status"], "completed_with_gaps")
        self.assertEqual(result["errors"][0]["job_id"], "J125")
        self.assertEqual({r["source_folder"] for r in result["results"]},
                         {"实习生/测试岗位", "校招/后端开发工程师"})
        self.assertEqual(matching.overview()["latest"]["batch_id"], result["batch_id"])

    def test_duplicate_live_jobs_each_get_job_specific_match(self):
        (self.folder / "good.txt").write_text(self.jd["duties"] * 5)
        positions = [{"job_id": "J123", "title": "测试岗位"},
                     {"job_id": "J124", "title": "测试岗位"}]
        result = matching.run_all(positions, lambda job_id: {**self.jd, "job_id": job_id}, self.evaluator)
        self.assertEqual(result["matched_jobs"], 2)
        self.assertEqual(result["matched_folders"], 2)
