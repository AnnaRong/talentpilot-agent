"""Build a source-only distribution from an explicit allowlist."""
import hashlib
import json
from datetime import date
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DATE = date.today().strftime("%Y%m%d")


def build():
    output = ROOT.parent / f"TalentPilot-Agent-{VERSION}-{DATE}.zip"
    selected = []
    for folder, patterns in (
        (ROOT / "talentpilot", ("*.py", "*.js")),
        (ROOT / "web", ("*.html", "*.js", "*.css")),
        (ROOT / "overseas-extension", ("*",)),
        (ROOT / "tests", ("test_*.py",)),
        (ROOT / "scripts", ("*.py", "*.mjs", "*.swift")),
        (ROOT / "docs", ("*.md",)),
        (ROOT / "integrations", ("*.md", "*.yaml", "*.py")),
        (ROOT / "examples", ("*.json",)),
    ):
        for pattern in patterns:
            selected += sorted(folder.rglob(pattern))
    selected += [ROOT / p for p in (
        "README.md", "AGENT.md", "VERSION", ".env.example", "requirements.txt",
        "requirements-mcp.txt", "install.sh", "start.command",
    )]
    selected = [p for p in selected if p.is_file() and not p.is_symlink()]
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in selected:
            archive.write(path, "talentpilot-agent/" + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(output) as archive:
        for entry in archive.namelist():
            parts = Path(entry).parts
            if any(part in {"data", ".env", ".venv", "node_modules", "__pycache__"} for part in parts):
                raise RuntimeError(f"Private or nonportable file in distribution: {entry}")
        assert archive.testzip() is None
    print(json.dumps({"archive": str(output), "files": len(selected), "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                      "contains_live_data": False, "contains_credentials": False}, ensure_ascii=False))


if __name__ == "__main__":
    build()
