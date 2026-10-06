"""Read-only status for the scheduled local resume organizer."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import settings

CATEGORIES = ("校招", "实习生", "社招", "高端人才")


def _category_status(root: Path, category: str) -> dict:
    directory = root / category
    job_directories = [path for path in directory.iterdir() if path.is_dir()] if directory.is_dir() else []
    date_directories = [
        path
        for job in job_directories
        for path in job.iterdir()
        if path.is_dir() and len(path.name) == 8 and path.name.isdigit()
    ]
    files = sum(
        1
        for path in directory.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(directory).parts)
    ) if directory.is_dir() else 0
    return {
        "category": category,
        "exists": directory.is_dir(),
        "jobs": len(job_directories),
        "dates": len(date_directories),
        "files": files,
    }


def status() -> dict:
    root = settings.resume_archive_dir.expanduser()
    categories = [_category_status(root, category) for category in CATEGORIES]
    memory = settings.resume_organizer_memory.expanduser()
    last_run = None
    last_report = "尚无可读取的自动整理记录"
    if memory.is_file():
        lines = [
            line[2:].strip()
            for line in memory.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.startswith("- ")
        ]
        report_lines = [line for line in lines if "Counts after run" in line or "本次运行" in line]
        if report_lines:
            last_report = report_lines[-1]
        last_run = datetime.fromtimestamp(memory.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return {
        "status": "active",
        "schedule": "每天 22:00（Asia/Shanghai）",
        "source": str(Path.home() / "Downloads"),
        "archive_root": str(root),
        "categories": categories,
        "total_files": sum(item["files"] for item in categories),
        "last_run": last_run,
        "last_report": last_report,
        "deduplication": "同一类别和岗位内跨日期核对；一个月内保留旧版，超过一个月保留最新版",
        "trash_policy": "仅将确认重复的简历移入系统垃圾箱，不永久删除",
    }
