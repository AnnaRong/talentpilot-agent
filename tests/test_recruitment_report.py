import copy
import unittest

from talentpilot.recruitment_report import COLUMNS, build_report, has_interviews
from test_platform import sample


class RecruitmentReportTests(unittest.TestCase):
    def capture(self):
        capture = sample()
        capture["applications"][0]["candidate_name"] = "张三"
        capture["interviews"][0].update(round="一面", scheduled_at="2020-01-01T10:00:00+08:00")
        capture["interviews"][0]["interviewers"][0]["name"] = "王老师"
        return capture

    def test_exact_columns_and_names(self):
        report = build_report(self.capture())
        self.assertEqual(COLUMNS, ["岗位", "岗位编号", "用人经理", "筛选人", "hc", "Full",
                                  "面试官安排", "待入职+已入职", "面试人需求", "候选人", "一面",
                                  "一面淘汰", "二面", "二面淘汰", "三面", "三面淘汰", "Offer沟通中", "备注"])
        group = report["groups"][0]
        self.assertEqual(len(group["shared"]) + len(group["rows"][0]["cells"]), 18)
        self.assertEqual(group["shared"][2:6], [None]*4)
        self.assertIn("一面：王老师", group["shared"][6])
        self.assertIn("二面：未采集", group["shared"][6])
        self.assertIn("张三", group["rows"][0]["cells"][1])
        self.assertEqual(group["rows"][0]["cells"][2], "")

    def test_repeat_interviews_deduplicate_and_cancelled_are_excluded(self):
        capture = self.capture()
        duplicate = copy.deepcopy(capture["interviews"][0])
        duplicate["interview_id"] = "repeat"
        cancelled = copy.deepcopy(duplicate)
        cancelled.update(interview_id="cancelled", round="二面", status="已取消", overall_result="淘汰")
        capture["interviews"].extend([duplicate, cancelled])
        capture["quality"]["coverage"]["interviews"]["declared"] = 3
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["rounds"]["一面"]["count"], 1)
        self.assertEqual(group["rounds"]["二面"], {"count": 0, "rejected": 0})
        self.assertEqual(len(group["rows"]), 1)

    def test_current_rejection_and_completed_rejection_have_names(self):
        capture = self.capture()
        capture["applications"][0].update(current_stage="二面", current_status="本轮淘汰")
        capture["interviews"][0]["overall_result"] = "淘汰"
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["rows"][0]["cells"][2], "张三")
        self.assertEqual(group["rows"][0]["cells"][4], "张三")
        self.assertEqual(group["stages"]["二面"], 0)

    def test_future_outcome_not_counted_and_stage_not_historical_pass(self):
        capture = self.capture()
        capture["applications"][0].update(current_stage="三面")
        capture["interviews"][0].update(overall_result="淘汰", scheduled_at="2099-01-01T10:00:00+08:00")
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["rounds"]["一面"]["rejected"], 0)
        self.assertEqual(group["rows"][0]["cells"][3], "")
        self.assertEqual(group["stages"]["三面"], 1)

    def test_hired_status_not_masked_by_placeholder_entry_status(self):
        capture = self.capture()
        capture["applications"][0].update(current_stage="入职", current_status="已入职", entry_status="空")
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["stages"]["已入职"], 1)
        self.assertEqual(group["shared"][7], "张三（已入职）")
        self.assertIsNone(group["shared"][5])

    def test_same_name_is_not_identity_and_distinct_jobs_stay_separate(self):
        capture = self.capture()
        second = {**capture["applications"][0], "application_id":"a2", "candidate_id":"c2"}
        third = {**second, "application_id":"a3", "job_id":"J_OTHER", "job_uuid":"other"}
        capture["positions"].append({**capture["positions"][0], "job_id":"J_OTHER", "job_uuid":"other"})
        capture["applications"].extend([second, third])
        capture["quality"]["coverage"]["applications"]["declared"] = 3
        capture["quality"]["coverage"]["positions"]["declared"] = 2
        groups = build_report(capture)["groups"]
        self.assertEqual([g["candidate_count"] for g in groups], [2, 1])

    def test_interview_only_candidate_and_empty_job(self):
        capture = self.capture()
        capture["applications"] = []
        capture["quality"]["coverage"]["applications"]["declared"] = 0
        capture["interviews"][0]["candidate_name"] = "历史候选人"
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["candidate_count"], 1)
        self.assertIn("仅面试历史", group["rows"][0]["cells"][-1])
        self.assertEqual(sum(group["stages"].values()), 0)

    def test_missing_interview_coverage_and_conflicting_applications(self):
        capture = self.capture()
        capture["interviews"] = []
        capture["quality"]["coverage"]["interviews"] = {"declared":0, "state":"not_collected"}
        self.assertFalse(has_interviews(capture))
        capture["applications"].append({**capture["applications"][0],
                                         "application_id":"other", "current_stage":"Offer"})
        capture["quality"]["coverage"]["applications"]["declared"] = 2
        group = build_report(capture)["groups"][0]
        self.assertEqual(group["candidate_count"], 1)
        self.assertEqual(sum(group["stages"].values()), 0)
        self.assertIn("多条申请状态不一致", group["rows"][0]["cells"][-1])
