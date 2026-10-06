from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        from docx import Document

        return "\n".join(paragraph.text for paragraph in Document(str(path)).paragraphs)
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"unsupported resume format: {path.suffix}")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mask_pii(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", text)
    text = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[PHONE]", text)
    return text


def screen_directory(directory: str, keywords: list[str]) -> dict[str, Any]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"resume directory does not exist: {root}")
    seen_hashes: dict[str, str] = {}
    results = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"}:
            continue
        file_hash = hash_file(path)
        duplicate_of = seen_hashes.get(file_hash)
        seen_hashes[file_hash] = path.name
        text = extract_text(path)
        masked = mask_pii(text)
        lowered = masked.casefold()
        hits = [keyword for keyword in keywords if keyword.casefold() in lowered]
        score = round((len(hits) / max(len(keywords), 1)) * 100, 1)
        results.append({
            "source": str(path),
            "filename": path.name,
            "sha256": file_hash,
            "duplicate_of": duplicate_of,
            "score": score,
            "matched_keywords": hits,
            "evidence": masked[:1200],
            "status": "duplicate" if duplicate_of else "screened",
        })
    return {"directory": str(root), "count": len(results), "results": results}
