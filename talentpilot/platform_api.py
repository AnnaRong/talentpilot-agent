from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from . import actions, privacy
from . import operations, knowledge
from .config import settings
from .security import authorize_dataset, require
from .verified_capture import read_latest

router = APIRouter(prefix="/api/platform")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobRequest(Strict):
    kind: Literal["audit", "analytics", "insights", "locks", "loads", "talent_pool", "analyst", "scorecards"]
    dataset_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    request_key: str = Field(min_length=8, max_length=200)
    budget: int = Field(default=8, ge=1, le=30)
    timeout: int = Field(default=60, ge=1, le=120)


class ActionRequest(Strict):
    kind: str
    dataset_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Decision(Strict):
    decision: Literal["approved", "rejected"]
    payload_hash: str
    note: str = Field(default="", max_length=1000)


class PanelRequest(Strict):
    dataset_id: str
    job_id: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=200)
    rounds: dict[str, list[str]]
    expected_revision: int = Field(ge=0)


class ScheduleRequest(Strict):
    dataset_id: str
    kind: Literal["audit", "analytics", "locks", "loads", "insights"]
    options: dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = Field(default=1800, ge=900, le=604800)
    enabled: bool = False


class ScheduleState(Strict):
    enabled: bool
    dataset_id: str


class KnowledgeRequest(Strict):
    department: str = Field(min_length=1, max_length=200)
    source_id: str
    title: str
    text: str


class PaperRequest(Strict):
    tool: Literal["tools/list", "search_papers", "get_paper_metadata"]
    arguments: dict[str, Any] = Field(default_factory=dict)


class PaperRadarRunRequest(Strict):
    jd: str = Field(min_length=10, max_length=20_000)
    sources: list[Literal["arXiv", "OpenAlex", "Semantic Scholar", "DBLP", "Crossref", "会议官网"]] = Field(default_factory=list, max_length=6)
    limit: int = Field(default=8, ge=1, le=20)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)


class PaperRadarApproveRequest(Strict):
    run_id: str = Field(min_length=16, max_length=64)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=1000)


class LiveRequest(Strict):
    request_key: str = Field(min_length=8, max_length=200)


class ResumeRequest(Strict):
    dataset_id: str
    candidate_id: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=1000)


class UploadExecuteRequest(Strict):
    fingerprint: str = Field(min_length=64, max_length=64)


class ResumeMatchRequest(Strict):
    job_id: str = Field(pattern=r"^J\d+$", max_length=20)
    source_folder: str = Field(min_length=1, max_length=1000)


class LockSettingsRequest(Strict):
    enabled: bool = False
    interval_minutes: int = Field(default=30, ge=15, le=1440)
    warning_hours: int = Field(default=24, ge=1, le=168)


class LockRecallRequest(Strict):
    watch_id: str = Field(min_length=64, max_length=64)
    job_id: str = Field(min_length=1, max_length=100)
    target_stage: str = Field(min_length=1, max_length=100)


@router.get("/lock-monitor")
def lock_monitor_overview(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import overview
    return overview(runtime.store, actor["id"])


@router.post("/lock-monitor/settings")
def lock_monitor_settings(request: Request, body: LockSettingsRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import configure
    return configure(runtime.store, actor["id"], **body.model_dump())


@router.post("/lock-monitor/refresh")
def lock_monitor_refresh(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import refresh
    return refresh(runtime.store, actor["id"])


@router.post("/lock-monitor/acknowledge")
def lock_monitor_acknowledge(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import acknowledge
    return acknowledge(runtime.store, actor["id"])


@router.post("/lock-monitor/recall-preview")
def lock_monitor_recall(request: Request, body: LockRecallRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import recall_preview
    return recall_preview(runtime.store, actor["id"], **body.model_dump())


@router.post("/lock-monitor/recall/{preview_id}/confirm")
def lock_monitor_confirm(request: Request, preview_id: str, body: UploadExecuteRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .lock_monitor import confirm_recall
    return confirm_recall(runtime.store, actor["id"], preview_id, body.fingerprint)


class BossPlanRequest(Strict):
    job_id: str = Field(pattern=r"^J[0-9A-Za-z_-]+$", max_length=100)
    recruitment_type: Literal["校招", "实习生", "社招"]
    education: Literal["不限", "大专", "本科", "硕士", "博士"]
    internship_keywords: list[str] = Field(default_factory=list, max_length=10)
    min_internships: int = Field(default=0, ge=0, le=5)
    greeting_limit: int = Field(default=10, ge=1, le=200)
    sparse_first_max: int = Field(default=3, ge=0, le=20)


class BossSearchRequest(Strict):
    job_id: str = Field(pattern=r"^J[0-9A-Za-z_-]+$", max_length=100)
    yaml_content: str = Field(min_length=20, max_length=20_000)


class TalentPoolSearchRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    school_types: list[str] = Field(default_factory=list, max_length=10)
    majors: list[str] = Field(default_factory=list, max_length=20)
    recent_days: int | None = Field(default=None, ge=1, le=3650)
    expected_city: str = Field(default="", max_length=100)
    include_sub_pools: bool = False
    talent_pool_id: str = Field(default="", max_length=100)
    limit: int = Field(default=20, ge=1, le=100)
    pages: int = Field(default=1, ge=1, le=10)
    page_size: Literal[15, 30, 60, 100, 300] = 100


class TalentPoolRecommendRequest(Strict):
    batch_id: str = Field(min_length=16, max_length=64)
    candidate_ids: list[str] = Field(min_length=1, max_length=20)
    fingerprint: str = Field(min_length=64, max_length=64)
    explicit_confirmation: bool = False


class PlatformAdvisorRequest(Strict):
    platform: Literal["BOSS直聘", "公司系统与人才库", "海外", "邮箱"]
    job_id: str = Field(min_length=1, max_length=100)
    answers: list[str] = Field(default_factory=list, max_length=6)


class ResumeDirectionRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    source_folder: str = Field(min_length=1, max_length=1000)


class OverseasProfile(Strict):
    name: str = Field(min_length=1, max_length=500)
    headline: str = Field(default="", max_length=1000)
    location: str = Field(default="", max_length=500)
    about: str = Field(default="", max_length=10_000)
    linkedin_url: str = Field(min_length=1, max_length=2048)
    experience: list[str] = Field(default_factory=list, max_length=50)
    education: list[str] = Field(default_factory=list, max_length=50)
    skills: list[str] = Field(default_factory=list, max_length=100)
    languages: list[str] = Field(default_factory=list, max_length=50)
    certifications: list[str] = Field(default_factory=list, max_length=50)
    projects: list[str] = Field(default_factory=list, max_length=50)


class OverseasCollectRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    job_title: str = Field(min_length=1, max_length=500)
    region: str = Field(min_length=1, max_length=100)
    consent_evidence: str = Field(min_length=1, max_length=1000)
    retention_days: int = Field(default=90, ge=1, le=366)
    extracted_at: str = Field(min_length=10, max_length=100)
    profile: OverseasProfile


class OverseasExportRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    record_ids: list[str] = Field(min_length=1, max_length=100)


class EmailAttachment(Strict):
    name: str = Field(min_length=1, max_length=500)
    content_base64: str = Field(min_length=1)


class EmailPreviewRequest(Strict):
    files: list[EmailAttachment] = Field(min_length=1, max_length=20)


class EmailSaveItem(EmailAttachment):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    graduation_year: str = Field(min_length=5, max_length=5)
    school: str = Field(min_length=1, max_length=100)
    candidate_name: str = Field(min_length=1, max_length=100)


class EmailSaveRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="其他邮箱", max_length=100)
    intent_confirmed: bool = False
    files: list[EmailSaveItem] = Field(min_length=1, max_length=20)


class EmailCollectionStartRequest(Strict):
    job_id: str = Field(min_length=1, max_length=100)
    provider: Literal["百度邮箱", "QQ邮箱", "网易邮箱", "Outlook", "Gmail"]


@router.post("/resumes/register")
def retired_register_resume(request: Request, body: ResumeRequest):
    _, actor = components(request)
    require(actor, {"admin"})
    raise HTTPException(410, "该功能已由候选人历史批量核验替代")


@router.get("/resume-organizer")
def resume_organizer_status(request: Request):
    _, actor = components(request)
    require(actor, {"admin"})
    from .resume_organizer import status
    return status()


@router.get("/resume-match/overview")
def resume_match_overview(request: Request):
    _, actor = components(request)
    require(actor, {"admin"})
    from .resume_matching import overview
    return overview()


@router.post("/resume-match/preview")
def resume_match_preview(request: Request, body: ResumeMatchRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .italent_browser import collect_job_jd
    from .resume_matching import run, save_jd
    jd = save_jd(collect_job_jd(body.job_id))
    result = run(body.job_id, body.source_folder)
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "resume_match.local_preview", result["run_id"])
    return {**result, "jd": {
        "title": jd["title"], "department": jd.get("department"),
        "keywords": jd["keywords"], "jd_hash": jd["jd_hash"],
    }}


@router.post("/resume-match/all")
def resume_match_all(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .italent_browser import collect_job_jd, collect_positions
    from .resume_matching import run_all
    inventory = collect_positions()
    result = run_all(inventory["positions"], collect_job_jd)
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "resume_match.all_jobs", result["batch_id"])
    return result


@router.get("/resume-match/jev-status")
def resume_match_jev_status(request: Request):
    _, actor = components(request)
    require(actor, {"admin"})
    from .jev_matching import status
    return status()


@router.post("/resume-match/direction")
def resume_match_direction(request: Request, body: ResumeDirectionRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .jev_matching import direction
    from .resume_matching import load_jd, run
    result = run(body.job_id, body.source_folder)
    rows = []
    for row in result["rows"]:
        if row.get("score") is None:
            rows.append({"filename": row["filename"], "status": row["status"],
                         "direction": None, "adjustments": [],
                         "reason": row.get("reason")})
        else:
            rows.append({"filename": row["filename"], "status": row["status"],
                         "score": row["score"], **direction(row)})
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "jev.resume_direction", result["run_id"])
    return {"run_id": result["run_id"], "job_id": body.job_id,
            "job_title": load_jd(body.job_id)["title"], "model": result["requested_model"],
            "rows": rows, "external_actions": 0}


@router.get("/resume-match/runs/{run_id}/{asset:path}")
def resume_match_asset(request: Request, run_id: str, asset: str):
    _, actor = components(request)
    require(actor, {"admin"})
    from .resume_matching import review_asset
    return FileResponse(review_asset(run_id, asset))


@router.post("/live-runs")
def submit_live(request: Request, body: LiveRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from . import live_runs
    return {"run_id": live_runs.submit(runtime, actor["id"], body.request_key)}


@router.get("/live-runs")
def read_live_runs(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from . import live_runs
    return live_runs.list_runs(runtime.store)


@router.post("/live-runs/{run_id}/{operation}")
def control_live(request: Request, run_id: str, operation: Literal["resume", "cancel"]):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from . import live_runs
    live_runs.control(runtime, run_id, actor["id"], operation)
    return {"operation": operation, "run_id": run_id}


@router.post("/recordings")
async def save_recording(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    if request.headers.get("content-type", "").split(";")[0] != "video/webm":
        raise HTTPException(415, "Only browser WebM recording is supported")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 19_000_000:
            raise HTTPException(413, "Recording segment exceeds 19MB")
    if len(data) < 100 or data[:4] != b"\x1aE\xdf\xa3":
        raise HTTPException(422, "Invalid WebM container")
    import uuid
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .platform_store import private_write
    name = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-招聘操作录频-" + uuid.uuid4().hex[:8] + ".webm"
    directory = settings.recordings_dir / "browser-selected"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(directory / name, bytes(data))
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "recording.save_user_selected_surface", name)
    return {"filename": name, "bytes": len(data), "path": str(directory / name),
            "surface_verified_by_server": False}


@router.post("/candidate-history/match")
async def match_candidate_history(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    content_type = request.headers.get("content-type", "").split(";")[0]
    if content_type != "application/json":
        raise HTTPException(415, "请选择简历文件夹")
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from .candidate_history import match_directory
    from .italent_browser import match_candidate_history as live_match
    with TemporaryDirectory(prefix="talentpilot-history-") as temporary:
        payload = await request.json()
        target_job_id = payload.get("job_id") if isinstance(payload, dict) else None
        if not isinstance(target_job_id, str) or not target_job_id.startswith("J"):
            raise HTTPException(422, "请选择本次上传的准确岗位")
        uploads = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(uploads, list):
            raise HTTPException(422, "简历批次格式不正确")
        if not 1 <= len(uploads) <= 100:
            raise HTTPException(422, "每批请选择1至100份简历")
        root = Path(temporary)
        original_names = {}
        import base64
        for index, upload in enumerate(uploads):
            if not isinstance(upload, dict) or not isinstance(upload.get("name"), str) or not isinstance(upload.get("content_base64"), str):
                raise HTTPException(422, "简历批次格式不正确")
            name = Path(upload["name"]).name
            suffix = Path(name).suffix.lower()
            if suffix not in {".pdf", ".docx", ".txt", ".md"}:
                continue
            try:
                data = base64.b64decode(upload["content_base64"], validate=True)
            except ValueError:
                raise HTTPException(422, f"{name} 内容编码不正确") from None
            if len(data) > 10_000_000:
                raise HTTPException(413, f"{name} 超过10MB")
            stored = root / f"{index:03d}-{name}"
            stored.write_bytes(data)
            original_names[stored.name] = name
        from .resume_matching import high_match_provenance
        from .resume import hash_file
        file_ids = [hash_file(root / stored_name) for stored_name in original_names]
        if not file_ids:
            raise HTTPException(422, "没有可处理的简历")
        try:
            high_match_provenance(target_job_id, file_ids)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        positions = collect_positions_for_upload()
        job = next((p for p in positions["positions"] if p["job_id"] == target_job_id), None)
        if not job:
            raise HTTPException(409, "所选岗位已不在当前权限岗位中，请刷新岗位")
        result = match_directory(root, live_match, target_job_id)
        result.update(job_id=target_job_id, target_stage="简历复筛/进行中",
                      channel="BOSS直聘", acquisition="主动投递")
        for row in result["results"]:
            row["filename"] = original_names.get(row.get("filename"), row.get("filename"))
        indexed_files = []
        for stored_name, original_name in original_names.items():
            content = (root / stored_name).read_bytes()
            indexed_files.append({"file_id": hash_file(root / stored_name), "name": original_name,
                                  "content_base64": base64.b64encode(content).decode()})
        from .upload_batches import create
        result.update(create(runtime.store, actor["id"], job, result, indexed_files))
        from .lock_monitor import track_upload
        result["lock_watches_added"] = track_upload(runtime.store, actor["id"], result["batch_id"], result)
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "candidate_history.batch_match", str(result["files"]))
    return result


def collect_positions_for_upload():
    from .italent_browser import collect_positions
    return collect_positions()


def collect_position_overview():
    from .italent_browser import add_interview_counts, collect_interviews, collect_positions
    return add_interview_counts(collect_positions(), collect_interviews())


@router.post("/candidate-history/{batch_id}/execute")
def execute_candidate_upload(request: Request, batch_id: str, body: UploadExecuteRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .upload_batches import execute
    return execute(runtime.store, actor["id"], batch_id, body.fingerprint)


@router.post("/connectors/italent/positions")
def read_positions(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    result = collect_position_overview()
    with runtime.store.transaction() as cx:
        runtime.store.event(cx, None, "italent.inventory.collected", "sourcing", result)
        runtime.store.audit(cx, actor["id"], "italent.read_positions", "live_job_grid")
    return result


@router.post("/connectors/boss/plans")
def create_boss_plan(request: Request, body: BossPlanRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .boss import create_plan, latest_position
    position = latest_position(runtime.store, body.job_id)
    return create_plan(runtime.store, actor["id"], position,
                       body.model_dump(exclude={"job_id"}))


@router.post("/connectors/boss/search")
def search_boss_candidates(request: Request, body: BossSearchRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .boss import latest_position
    from .boss_sourcing import search_and_rank
    position = latest_position(runtime.store, body.job_id)
    return search_and_rank(runtime.store, actor["id"], position, body.yaml_content)


@router.get("/connectors/platform-advisor/status")
def platform_advisor_status(request: Request):
    _, actor = components(request)
    require(actor, {"admin"})
    from .platform_advisor import status
    return status()


@router.post("/connectors/platform-advisor/interview")
def platform_advisor_interview(request: Request, body: PlatformAdvisorRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .boss import latest_position
    from .platform_advisor import interview
    job = latest_position(runtime.store, body.job_id)
    result = interview(body.platform, job, body.answers)
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "llm.platform_grill",
                            body.platform + "/" + body.job_id)
    return result


@router.post("/connectors/italent/talent-pool/search")
def search_talent_pool(request: Request, body: TalentPoolSearchRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .talent_pool import TalentPoolUnavailable, search
    positions = collect_positions_for_upload()
    job = next((item for item in positions["positions"] if item["job_id"] == body.job_id), None)
    if not job:
        raise HTTPException(409, "所选岗位已不在当前权限岗位中，请刷新岗位")
    try:
        return search(runtime.store, actor["id"], job, body.model_dump())
    except TalentPoolUnavailable as error:
        raise HTTPException(409, str(error)) from error


@router.post("/connectors/italent/talent-pool/recommend")
def recommend_talent_pool(request: Request, body: TalentPoolRecommendRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .talent_pool import recommend
    return recommend(runtime.store, actor["id"], body.batch_id, body.candidate_ids,
                     body.fingerprint, body.explicit_confirmation)


@router.get("/connectors/overseas/jobs")
def overseas_jobs(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .overseas import latest_positions
    jobs = latest_positions(runtime.store)
    if not jobs:
        raise HTTPException(409, "请先在 TalentPilot 刷新我的岗位")
    return jobs


@router.post("/connectors/overseas/profiles")
def collect_overseas_profile(request: Request, body: OverseasCollectRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .overseas import collect_profile, latest_positions
    jobs = latest_positions(runtime.store)
    job = next((item for item in jobs if item["job_id"] == body.job_id), None)
    if not job or job["title"] != body.job_title:
        raise HTTPException(409, "目标岗位已变化，请先刷新岗位")
    return collect_profile(runtime.store, actor["id"], body.model_dump())


@router.get("/connectors/overseas/profiles")
def overseas_profiles(request: Request, job_id: str):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .overseas import list_profiles
    return list_profiles(runtime.store, job_id)


@router.post("/connectors/overseas/exports")
def create_overseas_export(request: Request, body: OverseasExportRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .overseas import export_resumes
    result = export_resumes(runtime.store, actor["id"], body.job_id, body.record_ids)
    return {"count": result["count"],
            "download_url": f"/api/platform/connectors/overseas/exports/{result['export_id']}"}


@router.get("/connectors/overseas/exports/{export_id}")
def download_overseas_export(request: Request, export_id: str):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .overseas import export_path
    try:
        path = export_path(export_id)
    except FileNotFoundError as error:
        raise HTTPException(404, "简历包不存在或已清理") from error
    with runtime.store.transaction() as cx:
        runtime.store.audit(cx, actor["id"], "overseas.resume.download", export_id)
    return FileResponse(path, filename=f"海外招聘简历-{export_id[:8]}.zip")


@router.get("/connectors/overseas/extension")
def download_overseas_extension(request: Request):
    _, actor = components(request)
    require(actor, {"admin"})
    from .overseas import extension_archive
    return FileResponse(extension_archive(), filename="TalentPilot-LinkedIn-Collector.zip")


@router.post("/connectors/email/preview")
def preview_email_resumes(request: Request, body: EmailPreviewRequest):
    _, actor = components(request)
    require(actor, {"admin"})
    from .email_recruiting import preview
    return preview([item.model_dump() for item in body.files])


@router.post("/connectors/email/collections")
def start_email_collection(request: Request, body: EmailCollectionStartRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .boss import latest_position
    from .email_recruiting import start_collection
    job = latest_position(runtime.store, body.job_id)
    return start_collection(runtime.store, actor["id"], job, body.provider)


@router.post("/connectors/email/collections/{run_id}/resume")
def resume_email_collection(request: Request, run_id: str):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .email_recruiting import resume_collection
    return resume_collection(runtime.store, actor["id"], run_id)


@router.post("/connectors/email/collections/{run_id}/cancel")
def cancel_email_collection(request: Request, run_id: str):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .email_recruiting import cancel_collection
    return cancel_collection(runtime.store, actor["id"], run_id)


@router.post("/connectors/email/save")
def save_email_resumes(request: Request, body: EmailSaveRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .boss import latest_position
    from .email_recruiting import save
    job = latest_position(runtime.store, body.job_id)
    return save(runtime.store, actor["id"], job, body.provider, body.intent_confirmed,
                [item.model_dump() for item in body.files])


@router.get("/panels")
def panels(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT * FROM panels ORDER BY job_id").fetchall()
    return [{**dict(r), "payload": runtime.store.unseal(r["payload"])} for r in rows
            if "*" in actor["departments"] or r["department"] in actor["departments"]]


@router.post("/panels")
def confirm_panel(request: Request, body: PanelRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    authorize_dataset(runtime.store, actor, body.dataset_id)
    return operations.save_panel(runtime.store, actor["id"], **body.model_dump())


@router.get("/panels/assignments")
def panel_assignments(request: Request, dataset_id: str):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    authorize_dataset(runtime.store, actor, dataset_id)
    return operations.assignments(runtime.store, dataset_id)


@router.post("/schedules")
def add_schedule(request: Request, body: ScheduleRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    authorize_dataset(runtime.store, actor, body.dataset_id)
    return {"schedule_id": operations.create_schedule(runtime, actor["id"], **body.model_dump())}


@router.get("/schedules")
def schedules(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT * FROM schedules WHERE actor=? OR ?='admin'",
                          (actor["id"], actor["role"])).fetchall()
    result = []
    for row in rows:
        try:
            authorize_dataset(runtime.store, actor, row["dataset_id"])
            result.append({**dict(row), "options": runtime.store.unseal(row["options"])})
        except HTTPException:
            continue
    return result


@router.post("/schedules/{schedule_id}/state")
def schedule_state(request: Request, schedule_id: str, body: ScheduleState):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    authorize_dataset(runtime.store, actor, body.dataset_id)
    import time
    with runtime.store.transaction() as cx:
        row = cx.execute("SELECT actor FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not row or (row["actor"] != actor["id"] and actor["role"] != "admin"):
            raise HTTPException(403, "Only the schedule owner or administrator may change this schedule")
        cx.execute("UPDATE schedules SET enabled=?,dataset_id=?,next_due=?,last_error=NULL WHERE id=?",
                   (int(body.enabled), body.dataset_id, time.time(), schedule_id))
        runtime.store.audit(cx, actor["id"], "schedule.configure", schedule_id)
    return {"enabled": body.enabled}


@router.post("/knowledge")
def add_knowledge(request: Request, body: KnowledgeRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    if "*" not in actor["departments"] and body.department not in actor["departments"]:
        raise HTTPException(403, "Department scope denied")
    return knowledge.add(runtime.store, actor["id"], **body.model_dump())


@router.get("/knowledge/search")
def search_knowledge(request: Request, query: str, limit: int = 5):
    runtime, actor = components(request)
    return knowledge.search(runtime.store, actor["departments"], query, limit)


@router.post("/connectors/instsci")
async def call_papers(request: Request, body: PaperRequest):
    runtime, actor = components(request)
    require(actor, {"admin"})
    from .paper_mcp import call
    result = await call(body.tool, body.arguments)
    with runtime.store.transaction() as cx:
        runtime.store.event(cx, None, "mcp.metadata.completed", "paper_radar",
                            {"tool": body.tool, "status": result["status"], "downloads": 0})
        runtime.store.audit(cx, actor["id"], "mcp.metadata", body.tool)
    return result


@router.post("/connectors/paper-radar/run")
def run_paper_radar(request: Request, body: PaperRadarRunRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    from .paper_radar import run
    result = run(body.jd, body.sources or None, body.limit, body.year_from, body.year_to)
    now = datetime.now(timezone.utc).isoformat()
    with runtime.store.transaction() as cx:
        cx.execute("INSERT INTO paper_radar_runs VALUES (?,?,?,?,?,?)",
                   (result["run_id"], actor["id"], runtime.store.seal(result), result["status"], now, now))
        runtime.store.event(cx, None, "paper_radar.completed", "paper_radar",
                            {"run_id": result["run_id"], "papers": len(result["papers"]),
                             "candidates": result["candidate_count"], "sources": result["sources"]})
        runtime.store.audit(cx, actor["id"], "paper_radar.run", result["run_id"])
    return result


@router.get("/connectors/paper-radar/runs/{run_id}")
def get_paper_radar_run(request: Request, run_id: str):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    with runtime.store.transaction() as cx:
        row = cx.execute("SELECT * FROM paper_radar_runs WHERE id=?", (run_id,)).fetchone()
    if not row or (row["actor"] != actor["id"] and actor["role"] not in {"admin", "lead"}):
        raise HTTPException(404, "论文雷达任务不存在")
    return runtime.store.unseal(row["payload"])


@router.post("/connectors/paper-radar/approve")
def approve_paper_radar(request: Request, body: PaperRadarApproveRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    from .paper_radar import contact_drafts
    with runtime.store.transaction() as cx:
        row = cx.execute("SELECT * FROM paper_radar_runs WHERE id=?", (body.run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "论文雷达任务不存在")
        result = runtime.store.unseal(row["payload"])
        candidates = {item["candidate_id"]: item for item in result.get("candidates", [])}
        if any(candidate_id not in candidates for candidate_id in body.candidate_ids):
            raise HTTPException(409, "审批名单必须来自本次论文雷达结果")
        selected = [candidates[candidate_id] for candidate_id in body.candidate_ids]
        for candidate in selected:
            candidate["status"] = "已审批，待发送草稿"
        result["approved_candidate_ids"] = body.candidate_ids
        result["approval_note"] = body.note
        result["drafts"] = contact_drafts(selected, result["profile"])
        result["status"] = "approved_locally"
        result["approval_evidence"] = "HR 明确勾选并确认；仅生成草稿，未发送外部消息"
        now = datetime.now(timezone.utc).isoformat()
        cx.execute("UPDATE paper_radar_runs SET payload=?,status=?,updated_at=? WHERE id=?",
                   (runtime.store.seal(result), result["status"], now, body.run_id))
        runtime.store.audit(cx, actor["id"], "paper_radar.approve_and_draft", body.run_id)
    return {"run_id": body.run_id, "status": result["status"], "approved": len(selected),
            "drafts": result["drafts"], "evidence": result["approval_evidence"]}


def components(request):
    return request.app.state.platform, request.state.actor


@router.get("/me")
def me(request: Request):
    return request.state.actor


@router.get("/capabilities")
def capabilities(request: Request):
    return request.app.state.platform.capabilities()


@router.get("/datasets")
def datasets(request: Request):
    runtime, actor = components(request)
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT id,created_at,scope,kind FROM datasets ORDER BY created_at DESC").fetchall()
    items = []
    for row in rows:
        try:
            authorize_dataset(runtime.store, actor, row["id"])
            items.append(dict(row))
        except HTTPException:
            continue
    return items


@router.post("/datasets/latest")
def import_latest(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    capture = read_latest(settings.data_dir / "live-captures")
    if not capture:
        raise HTTPException(409, "No verified local snapshot; no live connector is configured")
    return {"dataset_id": runtime.store.import_dataset(capture, actor["id"]), "live_connection": False}


@router.get("/recruitment-report")
def recruitment_report(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr", "auditor"})
    from .recruitment_report import build_report, has_interviews
    from .workflows import parse_time
    available = []
    for item in datasets(request):
        capture = runtime.store.dataset(item["id"])
        at = parse_time(capture.get("run", {}).get("completed_at"))
        available.append((has_interviews(capture), at.timestamp() if at else 0, item["id"], capture))
    if not available:
        return {"dataset_id": None, "groups": [], "columns": [], "stages": []}
    _, _, dataset_id, capture = max(available, key=lambda row: row[:3])
    return {**build_report(capture), "dataset_id": dataset_id}


@router.post("/datasets")
def import_data(request: Request, body: dict[str, Any]):
    runtime, actor = components(request)
    require(actor, {"admin"})
    return {"dataset_id": runtime.store.import_dataset(body, actor["id"], "authorized_import")}


@router.post("/jobs")
def submit(request: Request, body: JobRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    if body.kind in {"audit", "insights", "scorecards"}:
        require(actor, {"admin", "lead"})
    if body.kind == "analyst":
        require(actor, {"admin"})
        if body.timeout < 40:
            raise HTTPException(422, "Model workflow needs at least 40 seconds")
    authorize_dataset(runtime.store, actor, body.dataset_id)
    job_id = runtime.submit(body.kind, actor["id"], body.dataset_id, body.options,
                            body.request_key, body.budget, body.timeout)
    return {"job_id": job_id}


@router.get("/jobs")
def jobs(request: Request):
    runtime, actor = components(request)
    items = []
    for job in runtime.jobs():
        try:
            authorize_dataset(runtime.store, actor, job["dataset_id"])
            if job["kind"] in {"audit", "insights", "scorecards"}:
                require(actor, {"admin", "lead"})
            items.append(job)
        except HTTPException:
            continue
    return items


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, job_id: str):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    with runtime.store.transaction() as cx:
        row = cx.execute("SELECT actor,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Job not found")
        if row["actor"] != actor["id"] and actor["role"] != "admin":
            raise HTTPException(403, "Only the task owner or administrator may cancel")
        if row["status"] not in {"queued", "running"}:
            raise HTTPException(409, "Job already terminal")
        cx.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
        runtime.store.audit(cx, actor["id"], "job.cancel", job_id)
    return {"status": "cancelled"}


@router.get("/events")
def events(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin", "auditor"})
    if "*" not in actor["departments"]:
        raise HTTPException(403, "Cross-department audit access required")
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200").fetchall()
    return [{**dict(r), "payload": runtime.store.unseal(r["payload"])} for r in rows]


@router.get("/alerts")
def alerts(request: Request):
    runtime, actor = components(request)
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 200").fetchall()
    items = []
    for row in rows:
        try:
            authorize_dataset(runtime.store, actor, row["dataset_id"])
            items.append({**dict(row), "payload": runtime.store.unseal(row["payload"])})
        except HTTPException:
            continue
    return items


@router.post("/actions")
def propose(request: Request, body: ActionRequest):
    runtime, actor = components(request)
    require(actor, {"admin", "lead", "hr"})
    authorize_dataset(runtime.store, actor, body.dataset_id)
    return {"action_id": actions.propose(runtime.store, actor["id"], body.kind, body.dataset_id, body.payload)}


def allowed_action(runtime, actor, action_id):
    with runtime.store.transaction() as cx:
        row = cx.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Action not found")
    authorize_dataset(runtime.store, actor, row["dataset_id"])
    return row


@router.get("/actions")
def action_list(request: Request):
    runtime, actor = components(request)
    with runtime.store.transaction() as cx:
        rows = cx.execute("SELECT * FROM actions ORDER BY created_at DESC LIMIT 100").fetchall()
    result = []
    for row in rows:
        try:
            authorize_dataset(runtime.store, actor, row["dataset_id"])
            result.append({**dict(row), "payload": runtime.store.unseal(row["payload"]),
                           "result": runtime.store.unseal(row["result"])})
        except HTTPException:
            continue
    return result


@router.post("/actions/{action_id}/decision")
def decision(request: Request, action_id: str, body: Decision):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    allowed_action(runtime, actor, action_id)
    actions.decide(runtime.store, action_id, actor["id"], body.decision, body.payload_hash, body.note)
    return {"status": body.decision}


@router.post("/actions/{action_id}/execute")
def execute(request: Request, action_id: str):
    runtime, actor = components(request)
    require(actor, {"admin", "lead"})
    allowed_action(runtime, actor, action_id)
    return actions.execute(runtime.store, action_id, actor["id"])


@router.post("/privacy/collect")
def collect(request: Request, body: dict[str, Any]):
    runtime, actor = components(request)
    require(actor, {"admin"})
    return privacy.collect(runtime.store, actor["id"], body)


@router.post("/privacy/delete")
def delete(request: Request, body: dict[str, Any]):
    runtime, actor = components(request)
    require(actor, {"admin"})
    candidate_id = body.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise HTTPException(422, "Explicit candidate_id required")
    return privacy.delete(runtime.store, actor["id"], candidate_id)


@router.post("/privacy/purge-expired")
def purge(request: Request):
    runtime, actor = components(request)
    require(actor, {"admin"})
    return privacy.delete(runtime.store, actor["id"])
