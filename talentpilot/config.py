from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    db_path = data_dir / "talentpilot.sqlite3"
    reports_dir = data_dir / "reports"
    plans_dir = data_dir / "recommendation-plan"
    recordings_dir = data_dir / "recordings"
    exports_dir = data_dir / "exports"
    email_resumes_dir = Path(os.getenv(
        "EMAIL_RESUMES_DIR", str(Path.home() / "Desktop" / "简历" / "邮箱")
    )).expanduser()
    resume_archive_dir = Path(os.getenv(
        "RESUME_ARCHIVE_DIR", str(Path.home() / "Desktop" / "简历")
    )).expanduser()
    resume_organizer_memory = Path(os.getenv(
        "RESUME_ORGANIZER_MEMORY",
        str(Path.home() / ".codex" / "automations" / "automation-2" / "memory.md"),
    )).expanduser()
    llm_api_key = os.getenv("LLM_API_KEY", "").strip()
    llm_base_url = os.getenv("LLM_BASE_URL", "https://oneapi-comate.baidu-int.com/v1").strip()
    llm_model = os.getenv("LLM_MODEL", "gpt-5.6-sol").strip()
    typesafe_api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    typesafe_base_url = os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1").strip()
    typesafe_model = os.getenv("TYPESAFE_MODEL", "jev-1.13.0").strip()
    typesafe_confidence_threshold = float(os.getenv("TYPESAFE_CONFIDENCE_THRESHOLD", "0.5"))

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.data_dir.mkdir(parents=True, exist_ok=True)
        cls.reports_dir.mkdir(parents=True, exist_ok=True)
        cls.plans_dir.mkdir(parents=True, exist_ok=True)
        cls.recordings_dir.mkdir(parents=True, exist_ok=True)
        cls.exports_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
