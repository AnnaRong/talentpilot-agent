"""Human-login email resume import with reviewed, deterministic filenames."""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .candidate_history import extract_identity
from .config import settings
from .platform_store import digest
from .resume import extract_text


SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
PROVIDERS = {
    "百度邮箱": "https://mail.baidu.com/",
    "QQ邮箱": "https://mail.qq.com/",
    "网易邮箱": "https://mail.163.com/",
    "Outlook": "https://outlook.office.com/mail/",
    "Gmail": "https://mail.google.com/",
}
AUTOMATION = Path(__file__).with_name("collect_email_resumes.js")
YEAR = re.compile(r"(?<!\d)(20\d{2})(?:\s*年)?\s*(?:届|毕业)")
GRADUATION_LABEL = re.compile(
    r"(?:预计毕业(?:时间|年份)?|毕业(?:时间|年份)?|应届生)[^\n\r]{0,24}?(20\d{2})", re.I
)
DATE_RANGE = re.compile(r"20\d{2}(?:[./年-]\d{1,2})?\s*[-至~—–]\s*(20\d{2})(?:[./年-]\d{1,2})?")
SCHOOL = re.compile(
    r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z .·&()（）'-]{1,48}?(?:大学|学院|University|College))",
    re.I,
)
INVALID_FILENAME = re.compile(r'[\\/:*?"<>|+\x00-\x1f]+')


def _decode_file(item: dict) -> tuple[str, bytes]:
    name = Path(str(item.get("name") or "")).name
    suffix = Path(name).suffix.lower()
    if not name or suffix not in SUPPORTED:
        raise ValueError("仅支持 PDF、DOCX、TXT 或 Markdown 简历")
    try:
        content = base64.b64decode(item.get("content_base64", ""), validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise ValueError(f"{name} 内容编码不正确") from None
    if not content or len(content) > 10_000_000:
        raise ValueError(f"{name} 必须为不超过10MB的非空简历")
    return name, content


def _clean(value: str, fallback: str = "") -> str:
    value = " ".join(str(value or "").split()).strip(" .-_+")
    value = INVALID_FILENAME.sub("-", value).strip(" .-_")
    return value[:60] or fallback


def _graduation_year(text: str) -> tuple[str, str]:
    for pattern, confidence in ((GRADUATION_LABEL, "high"), (YEAR, "high"), (DATE_RANGE, "review")):
        years = [int(value) for value in pattern.findall(text) if 2000 <= int(value) <= 2040]
        if years:
            return f"{max(years)}届", confidence
    return "", "missing"


def _school(text: str) -> tuple[str, str]:
    section = re.search(r"(?:教育经历|教育背景|Education)([\s\S]{0,2000})", text, re.I)
    matches = SCHOOL.findall(section.group(1) if section else text[:5000])
    for value in matches:
        cleaned = _clean(value)
        cleaned = re.sub(r"^(?:学校|院校|毕业院校)\s*[:：]?\s*", "", cleaned)
        if 3 <= len(cleaned) <= 60:
            return cleaned, "high" if section else "review"
    return "", "missing"


def preview(files: list[dict]) -> dict:
    if not 1 <= len(files) <= 20:
        raise ValueError("每批请选择1至20份邮箱简历")
    results = []
    total = 0
    with tempfile.TemporaryDirectory(prefix="talentpilot-email-") as temporary:
        root = Path(temporary)
        for index, item in enumerate(files):
            name, content = _decode_file(item)
            total += len(content)
            if total > 19_000_000:
                raise ValueError("单批邮箱简历总大小不能超过19MB")
            path = root / f"{index:02d}-{name}"
            path.write_bytes(content)
            try:
                text = extract_text(path)
                identity = extract_identity(path)
                year, year_confidence = _graduation_year(text)
                school, school_confidence = _school(text)
                person = _clean(identity.get("name") or "")
                results.append({
                    "index": index,
                    "original_name": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "graduation_year": year,
                    "school": school,
                    "candidate_name": person,
                    "confidence": {
                        "graduation_year": year_confidence,
                        "school": school_confidence,
                        "name": "high" if person else "missing",
                    },
                    "needs_review": not all((year, school, person)) or "review" in {
                        year_confidence, school_confidence
                    },
                })
            except Exception as error:
                results.append({
                    "index": index,
                    "original_name": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "graduation_year": "",
                    "school": "",
                    "candidate_name": "",
                    "confidence": {"graduation_year": "missing", "school": "missing", "name": "missing"},
                    "needs_review": True,
                    "parse_error": str(error),
                })
    return {"files": len(results), "results": results, "requires_intent_confirmation": True}


def _ego(payload: dict, timeout: int) -> dict:
    executable = shutil.which("ego-browser")
    if not executable:
        raise ValueError("Ego Browser 不可用，无法打开可由 Agent 接管的邮箱")
    import json
    source = "const TALENTPILOT_INPUT=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    source += AUTOMATION.read_text()
    try:
        result = subprocess.run([executable, "nodejs"], input=source, text=True,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError("邮箱 Agent 超时；未保存任何简历") from None
    records = [line.split("TALENTPILOT_EMAIL_RESULT:", 1)[1]
               for line in (result.stdout + "\n" + result.stderr).splitlines()
               if line.startswith("TALENTPILOT_EMAIL_RESULT:")]
    if not records:
        raise ValueError("邮箱 Agent 未返回可验证结果")
    data = json.loads(records[-1])
    if data.get("state") == "failed":
        raise ValueError(data.get("reason") or "邮箱 Agent 执行失败")
    return data


def start_collection(store, actor: str, job: dict, provider: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("请选择支持的邮箱")
    with store.transaction() as cx:
        active = cx.execute("SELECT id FROM email_collection_runs WHERE actor=? AND status='awaiting_login'",
                            (actor,)).fetchone()
    if active:
        raise ValueError("已有邮箱登录任务等待继续，请先完成当前任务")
    run_id = uuid.uuid4().hex
    result = _ego({"operation": "start", "run_id": run_id,
                   "provider_url": PROVIDERS[provider]}, 60)
    if result.get("state") != "awaiting_login" or not result.get("handoff_completed"):
        raise ValueError("邮箱登录页未成功交给收集人")
    payload = {"run_id": run_id, "task_id": result["task_id"], "job": job,
               "provider": provider, "status": "awaiting_login"}
    import time
    with store.transaction() as cx:
        cx.execute("INSERT INTO email_collection_runs VALUES (?,?,?,?,?,?)",
                   (run_id, actor, job["job_id"], store.seal(payload), "awaiting_login", time.time()))
        store.audit(cx, actor, "email.collection.login_handoff", run_id)
    return {"run_id": run_id, "status": "awaiting_login", "task_id": result["task_id"],
            "job_id": job["job_id"], "job_title": job["title"], "provider": provider,
            "instruction": "请在已打开的邮箱页面完成登录，然后返回 TalentPilot 点击“登录完成，Agent 开始收集”。"}


def resume_collection(store, actor: str, run_id: str) -> dict:
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM email_collection_runs WHERE id=?", (run_id,)).fetchone()
    if not row or row["actor"] != actor or row["status"] != "awaiting_login":
        raise ValueError("邮箱收集任务不存在、已结束或不属于当前用户")
    payload = store.unseal(row["payload"])
    with store.transaction() as cx:
        updated = cx.execute("UPDATE email_collection_runs SET status='running' "
                             "WHERE id=? AND status='awaiting_login'", (run_id,))
        if updated.rowcount != 1:
            raise ValueError("邮箱收集任务正在运行或已结束")
    download_dir = Path(tempfile.mkdtemp(prefix="talentpilot-email-download-"))
    download_dir.chmod(0o700)
    try:
        result = _ego({"operation": "resume", "task_id": payload["task_id"],
                       "job_title": payload["job"]["title"],
                       "download_dir": str(download_dir)}, 360)
        if result.get("state") == "awaiting_login":
            with store.transaction() as cx:
                cx.execute("UPDATE email_collection_runs SET status='awaiting_login' WHERE id=?", (run_id,))
            return {"run_id": run_id, "status": "awaiting_login", "count": 0,
                    "instruction": "邮箱仍显示登录或验证页面，请完成后再次点击开始收集。"}
        paths = [Path(value) for value in result.get("files", [])]
        if any(path.parent != download_dir or not path.is_file() for path in paths):
            raise ValueError("邮箱 Agent 返回了无效附件路径")
        files = [{"name": path.name, "content_base64": base64.b64encode(path.read_bytes()).decode()}
                 for path in paths]
        if not files:
            saved = {"status": "completed", "count": 0, "results": [],
                     "directory": str(settings.email_resumes_dir / _clean(payload["job"]["title"])),
                     "reviewed": result.get("reviewed", []),
                     "limitation": "没有找到同时具备明确岗位意向和 PDF/DOCX 简历附件的邮件。"}
        else:
            parsed = preview(files)["results"]
            ready, skipped = [], []
            for attachment, metadata in zip(files, parsed):
                if metadata.get("needs_review"):
                    skipped.append({"filename": metadata["original_name"],
                                    "reason": "毕业年份、学校或姓名无法可靠识别"})
                    continue
                ready.append({**attachment, "sha256": metadata["sha256"],
                              "graduation_year": metadata["graduation_year"],
                              "school": metadata["school"],
                              "candidate_name": metadata["candidate_name"]})
            saved = save(store, actor, payload["job"], payload["provider"], True, ready) if ready else {
                "status": "completed", "count": 0, "results": [],
                "directory": str(settings.email_resumes_dir / _clean(payload["job"]["title"])),
                "external_actions": 0,
            }
            saved.update(skipped=skipped, reviewed=result.get("reviewed", []), found=len(files))
        with store.transaction() as cx:
            cx.execute("UPDATE email_collection_runs SET status='completed' WHERE id=?", (run_id,))
            store.audit(cx, actor, "email.collection.completed", run_id)
        return {"run_id": run_id, **saved}
    except Exception:
        with store.transaction() as cx:
            cx.execute("UPDATE email_collection_runs SET status='awaiting_login' WHERE id=? AND status='running'",
                       (run_id,))
        raise
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


def cancel_collection(store, actor: str, run_id: str) -> dict:
    with store.transaction() as cx:
        row = cx.execute("SELECT * FROM email_collection_runs WHERE id=?", (run_id,)).fetchone()
    if not row or row["actor"] != actor or row["status"] not in {"awaiting_login", "running"}:
        raise ValueError("邮箱收集任务不存在、已结束或不属于当前用户")
    payload = store.unseal(row["payload"])
    _ego({"operation": "stop", "task_id": payload["task_id"]}, 30)
    with store.transaction() as cx:
        cx.execute("UPDATE email_collection_runs SET status='cancelled' WHERE id=?", (run_id,))
        store.audit(cx, actor, "email.collection.cancelled", run_id)
    return {"run_id": run_id, "status": "cancelled"}


def _validated_metadata(item: dict) -> tuple[str, str, str]:
    year = str(item.get("graduation_year") or "").strip()
    if not re.fullmatch(r"20\d{2}届", year) or not 2000 <= int(year[:4]) <= 2040:
        raise ValueError("请为每份简历确认20xx届毕业年份")
    school = _clean(item.get("school") or "")
    person = _clean(item.get("candidate_name") or "")
    if not school or not person:
        raise ValueError("请为每份简历确认学校和姓名")
    return year, school, person


def save(store, actor: str, job: dict, provider: str, intent_confirmed: bool, files: list[dict]) -> dict:
    if not intent_confirmed:
        raise ValueError("请先确认邮件中的求职意向与当前岗位一致")
    if not 1 <= len(files) <= 20:
        raise ValueError("每批请选择1至20份邮箱简历")
    title = _clean(job.get("title") or "", "未命名岗位")
    job_id = str(job.get("job_id") or "")
    if not job_id:
        raise ValueError("请选择准确岗位")
    prepared = []
    total = 0
    for item in files:
        original_name, content = _decode_file(item)
        total += len(content)
        if total > 19_000_000:
            raise ValueError("单批邮箱简历总大小不能超过19MB")
        actual_hash = hashlib.sha256(content).hexdigest()
        if item.get("sha256") != actual_hash:
            raise ValueError(f"{original_name} 在预览后发生变化，请重新解析")
        year, school, person = _validated_metadata(item)
        prepared.append((original_name, content, actual_hash, year, school, person))
    directory = settings.email_resumes_dir / title
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    results = []
    for original_name, content, actual_hash, year, school, person in prepared:
        base = f"{title}+{year}+{school}+{person}"
        target = directory / f"{base}{Path(original_name).suffix.lower()}"
        version = 2
        while target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != actual_hash:
            target = directory / f"{base}+版本{version}{Path(original_name).suffix.lower()}"
            version += 1
        duplicate = target.exists()
        if not duplicate:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
        results.append({"original_name": original_name, "filename": target.name,
                        "path": str(target), "duplicate": duplicate})
    with store.transaction() as cx:
        store.audit(cx, actor, "email.resume.saved", digest({
            "job_id": job_id, "provider": _clean(provider, "其他邮箱"),
            "hashes": [item["sha256"] for item in files],
        }))
        store.event(cx, None, "email.resumes.saved", "sourcing", {
            "job_id": job_id, "count": len(results),
            "duplicates": sum(item["duplicate"] for item in results),
        })
    return {"status": "saved", "job_id": job_id, "job_title": title,
            "directory": str(directory), "count": len(results), "results": results,
            "external_actions": 0,
            "limitation": "邮箱登录和附件下载由收集人完成；TalentPilot 不保存邮箱密码或登录会话。"}
