from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RunKind = Literal[
    "audit",
    "recommendation_plan",
    "resume_screen",
    "paper_radar",
    "recruitment_workflow",
    "export_excel",
    "publish_ruliu",
]


class RunCreate(BaseModel):
    kind: RunKind
    goal: str = Field(min_length=1, max_length=4000)
    input_path: str | None = None
    candidate_path: str | None = None
    target_jobs: list[dict[str, str]] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = ""


class StatusUpdate(BaseModel):
    status: str
    message: str


class ITalentCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: dict[str, Any] = Field(default_factory=dict)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    interviews: list[dict[str, Any]] = Field(default_factory=list)
    offers: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_evidence(self):
        if not self.metadata.get("captured_at") or not self.positions:
            raise ValueError("Capture needs a timestamp and nonempty verified positions.")
        if not self.candidates and not self.interviews:
            raise ValueError("Empty candidate/interview exports are not allowed.")
        return self


class BatchRequest(BaseModel):
    candidates: list[dict[str, Any]]
    first_job_id: str = "J103039"
    first_job_name: str = "AI测试开发工程师"
    second_job_id: str = "J103043"
    second_job_name: str = "AI测试开发工程师"
    first_count: int = Field(default=8, ge=1, le=20)
    second_count: int = Field(default=12, ge=1, le=20)


class RecordingStart(BaseModel):
    task_id: str | None = None
    label: str = "招聘操作录频"


class RecordingStep(BaseModel):
    action: str = Field(min_length=1, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)
    screenshot_path: str | None = None


class RecordingStop(BaseModel):
    status: Literal["completed", "failed", "cancelled"] = "completed"
