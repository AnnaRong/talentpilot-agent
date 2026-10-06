"""Local JD keyword coverage, copy-only review exports, and upload provenance."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

from .candidate_history import SUPPORTED
from .config import settings
from .platform_store import private_write
from .resume import extract_text, mask_pii

# Seed terms stay literal; additional meaningful JD terms are extracted deterministically.
TERMS = (
    "用户触达", "功能推广", "用户引导", "用户调研", "调研问卷", "数据分析", "活动策划",
    "活动运营", "用户活动", "留存率", "NPS", "跨部门协作", "用户分层", "文案", "视频剪辑",
    "Excel", "数据透视表", "VLOOKUP", "SQL", "Python", "Canva", "PS",
    "需求分析", "需求调研", "竞品分析", "产品设计", "原型", "PRD", "用户体验", "项目管理",
    "大模型", "机器学习", "深度学习", "自然语言处理", "数据标注", "模型评估", "提示词",
    "Agent", "RAG", "LangChain", "PyTorch", "TensorFlow",
    "JavaScript", "TypeScript", "React", "Vue", "HTML", "CSS", "Java", "C++", "Go",
    "Linux", "Docker", "Kubernetes", "MySQL", "Redis", "Git", "接口测试", "自动化测试",
    "性能测试", "测试用例", "软件测试", "后端开发", "前端开发", "全栈",
    "会议", "会务", "接待", "报销", "行政", "办公软件", "PPT", "Word", "PowerPoint",
    "Office", "工位管理", "团队活动", "员工关怀", "公众号", "新闻稿", "企业文化",
    "海报", "软文", "选题", "排版", "AI工具", "图片", "视频",
    "系统测试", "质量保证", "缺陷", "测试报告", "问题定位", "用户反馈", "效率", "质量",
)

STOP_WORDS = {"负责", "相关", "工作", "能力", "要求", "具备", "熟悉", "掌握", "了解", "优先",
              "参与", "支持", "进行", "以及", "以上", "以下", "良好", "一定", "能够", "使用",
              "岗位", "专业", "经验", "项目", "开发", "技术", "基础", "具有", "本科", "硕士",
              "学历", "在校生", "毕业", "全职", "其他", "一个", "一种", "团队", "方向"}

ROLE_ALIASES = {
    "测试开发工程师": {"AI测试开发工程师"},
    "开发测试工程师": {"AI测试开发工程师"},
    "开发测试工程师实习生": {"测试开发实习生", "AI测试开发工程师"},
    "AI产品经理实习生": {"产品经理实习生", "AI产品经理"},
    "大模型研发工程师": {"AI研发工程师"},
}

FORMAL_LOCAL_ROLES = {"AI研发工程师", "agent开发工程师", "深圳部门助理"}

def _root():
    return settings.data_dir / "resume-matching"


def _replace_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"J\d+", value):
        raise ValueError("岗位编号无效")
    return value


def _contains(text, term):
    if re.fullmatch(r"[A-Za-z0-9+#.]+", term):
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", text, re.I))
    return term.casefold() in text.casefold()


def keywords(jd):
    text = jd["duties"] + chr(10) + jd["qualifications"]
    found = [term for term in TERMS if _contains(text, term)]
    # Prefer the specific JD phrase, e.g. JavaScript never counts as Java.
    found = [term for term in found if term.isascii() or not any(
        term != other and term.casefold() in other.casefold() for other in found)]
    candidates = re.findall(r"[A-Za-z][A-Za-z0-9+.#-]{1,30}", text)
    for token in candidates:
        token = token.strip(".-")
        if len(token) < 2 or token in STOP_WORDS or token.isdigit() or token in found:
            continue
        found.append(token)
        if len(found) >= 24:
            break
    return found


def save_jd(data):
    job_id = _job_id(data.get("job_id"))
    if data.get("state") != "verified_job_jd" or any(
            len(data.get(field, "").strip()) < 20 for field in ("duties", "qualifications")):
        raise ValueError("必须读取完整的北森工作职责和任职资格")
    fields = ("job_id", "title", "department", "duties", "qualifications", "recruitment_type",
              "job_uuid", "numeric_job_id", "collected_at", "state")
    profile = {key: data.get(key) for key in fields}
    profile["keywords"] = keywords(profile)
    profile["jd_hash"] = hashlib.sha256((profile["duties"] + "\n" + profile["qualifications"]).encode()).hexdigest()
    profile["match_hash"] = hashlib.sha256(json.dumps(
        [profile["jd_hash"], "typesafe-jev-v1", settings.typesafe_model,
         settings.typesafe_confidence_threshold], ensure_ascii=False).encode()).hexdigest()
    path = _root() / "jd" / f"{job_id}.json"
    _replace_private(path, json.dumps(profile, ensure_ascii=False, indent=2).encode())
    return profile


def load_jd(job_id):
    path = _root() / "jd" / f"{_job_id(job_id)}.json"
    if not path.is_file():
        raise ValueError("尚未读取该岗位 JD")
    return json.loads(path.read_text())


def folders():
    root = settings.resume_archive_dir.resolve()
    rows = []
    if not root.is_dir():
        return rows
    for category in sorted(root.iterdir()):
        if not category.is_dir() or category.is_symlink() or category.name.startswith(".") or category.name == "高匹配":
            continue
        for folder in sorted(category.iterdir()):
            if folder.is_dir() and not folder.is_symlink() and not folder.name.startswith("."):
                count = sum(1 for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
                if count:
                    rows.append({"path": str(folder.relative_to(root)), "files": count})
    return rows


def _role_name(value):
    value = re.sub(r"^20\d{2}校园招聘[-_—]?", "", str(value), flags=re.I)
    value = re.sub(r"^(北京|上海|深圳|广州|成都|杭州)[-_—]", "", value)
    value = re.sub(r"[-_—](?:Comate[^-_—]*组|[^-_—]*组)-[^-_—]+$", "", value, flags=re.I)
    return re.sub(r"[\s（）()/_\-—]+", "", value).casefold()


def source_folders(position, available=None):
    """Map a live job to local role folders; weak similarities stay unmatched."""
    available = available or folders()
    title = _role_name(position.get("title"))
    category = "校招" if "校园招聘" in str(position.get("title")) else None
    internship = bool(re.search(r"实习生|实习", str(position.get("title"))))
    formal_local = str(position.get("title")) in FORMAL_LOCAL_ROLES
    base_title = re.sub(r"^20\d{2}校园招聘-(?:北京|上海|深圳|广州|成都|杭州)-", "", str(position.get("title")))
    base_title = re.sub(r"-(?:Comate[^-]*组|[^-]*组)-[^-]+$", "", base_title, flags=re.I)
    aliases = {_role_name(value) for value in ROLE_ALIASES.get(position.get("title"), set())
               | ROLE_ALIASES.get(base_title, set())}
    aliases.add(_role_name(base_title))
    exact = []
    for item in available:
        parts = Path(item["path"]).parts
        if len(parts) != 2 or parts[1] == "待确认岗位" or (category and parts[0] != category):
            continue
        if not category and not internship and not formal_local and parts[0] not in {"校招", "实习生"}:
            continue
        if formal_local and parts[0] != "实习生":
            continue
        if not category and internship != bool(re.search(r"实习生|实习", parts[1])):
            continue
        role = _role_name(parts[1])
        if role == title or role in aliases:
            exact.append(item)
    if exact:
        return exact
    candidates = [item for item in available if Path(item["path"]).name != "待确认岗位"
                  and (not category or Path(item["path"]).parts[0] == category)
                  and (not formal_local or Path(item["path"]).parts[0] == "实习生")
                  and (category or internship == bool(re.search(r"实习生|实习", Path(item["path"]).name)))]
    ranked = [(SequenceMatcher(None, title, _role_name(Path(item["path"]).name)).ratio(), item)
              for item in candidates]
    best = max((score for score, _ in ranked), default=0)
    return [item for score, item in ranked if score == best and score >= 0.82]


def overview():
    profiles = [json.loads(p.read_text()) for p in sorted((_root() / "jd").glob("J*.json"))]
    batches = sorted((_root() / "batches").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    from .jev_matching import status as jev_status
    latest = json.loads(batches[0].read_text()) if batches else None
    if latest and latest.get("matching_engine") != "TypeSafe AI Jev":
        latest = None
    return {"folders": folders(), "profiles": profiles, "latest": latest,
            "threshold": 70, "jev": jev_status(), "external_actions": 0}


def score_text(text, terms):
    hits = [term for term in terms if _contains(text, term)]
    # The unrounded fraction is used for the strict >70 gate.
    high = bool(terms) and len(hits) * 100 > len(terms) * 70
    evidence = {}
    lines = [line.strip() for line in mask_pii(text).splitlines() if line.strip()]
    for term in hits:
        evidence[term] = next((line[:260] for line in lines if _contains(line, term)), "")
    return {"score": round(100 * len(hits) / len(terms), 2) if terms else None,
            "high": high, "matched_keywords": hits,
            "missing_keywords": [term for term in terms if term not in hits], "evidence": evidence}


def run(job_id, folder, batch_id=None, evaluator=None):
    from .jev_matching import evaluate
    evaluator = evaluator or evaluate
    profile = load_jd(job_id)
    root = settings.resume_archive_dir.resolve()
    allowed = {item["path"] for item in folders()}
    if folder not in allowed:
        raise ValueError("请选择当前本地归档中的岗位文件夹")
    source = (root / folder).resolve()
    if not source.is_relative_to(root):
        raise ValueError("简历目录超出本地归档范围")
    run_id = uuid.uuid4().hex
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    title = re.sub(r'[\\/:*?"<>|]', "-", profile["title"]).strip(". ")
    target = root / "高匹配" / f"{title}-{job_id}" / f"{stamp}-{run_id[:6]}"
    if not target.resolve().is_relative_to(root) or any(
            parent.is_symlink() for parent in (target, *target.parents) if parent != root):
        raise ValueError("高匹配输出目录不能使用符号链接")
    target.mkdir(parents=True, mode=0o700)
    rows, seen = [], set()
    paths = sorted(source.rglob("*"))
    for path in paths:
        if path.suffix.lower() not in SUPPORTED or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source) or any(
                part.startswith(".") for part in path.relative_to(source).parts):
            continue
        row = {"source": str(path), "filename": path.name, "status": "自动匹配", "copy": None}
        try:
            if path.stat().st_size > 10_000_000:
                raise ValueError("超过10MB")
            content = path.read_bytes()
            file_id = hashlib.sha256(content).hexdigest()
            row["file_id"] = file_id
            text = extract_text(path)
            if len(re.sub(r"\s+", "", text)) < 80:
                raise ValueError("正文不足80字或为扫描件，未纳入匹配")
            from .candidate_history import _name
            row.update(evaluator(profile, text, _name(text, path)))
            if hashlib.sha256(path.read_bytes()).hexdigest() != file_id:
                raise ValueError("匹配过程中原文件发生变化，请重试")
            if file_id in seen:
                row["status"] = "本岗位相同文件，未重复复制"
            elif row["high"]:
                destination = target / "简历" / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                private_write(destination, content)
                row["copy"] = str(destination)
                seen.add(file_id)
            else:
                row["status"] = "未超过70%，保留原件"
        except Exception as error:
            from .jev_matching import JevConfigurationError, JevServiceError
            if isinstance(error, (JevConfigurationError, JevServiceError)):
                raise
            row.update(status="解析失败，未纳入", score=None, high=False,
                       reason=type(error).__name__ + ": " + str(error)[:180])
        rows.append(row)
    result = {"run_id": run_id, "job_id": job_id, "job_title": profile["title"],
              "jd_hash": profile["jd_hash"], "jd_collected_at": profile["collected_at"],
              "match_hash": profile["match_hash"],
              "source_folder": folder, "threshold": 70, "status": "completed_automatically",
              "batch_id": batch_id,
              "matching_engine": "TypeSafe AI Jev", "requested_model": settings.typesafe_model,
              "total": len(rows), "high_count": sum(bool(r["copy"]) for r in rows),
              "parse_failed": sum(r["score"] is None for r in rows), "rows": rows,
              "directory": str(target), "report": str(target / "自动匹配结果.html"), "external_actions": 0}
    _write_report(result, profile)
    manifest = _root() / "runs" / f"{run_id}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(manifest, json.dumps(result, ensure_ascii=False, indent=2).encode())
    return result


def run_all(positions, jd_reader, evaluator=None):
    from .jev_matching import configured, evaluate
    if evaluator is None and not configured():
        raise ValueError("Jev 尚未启用：请先配置 TypeSafe API Key；未运行旧关键词匹配")
    evaluator = evaluator or evaluate
    if not positions:
        raise ValueError("当前北森岗位清单为空，未执行匹配")
    batch_id = uuid.uuid4().hex
    started_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    available = folders()
    results, skipped, errors, used_folders = [], [], [], set()
    for position in positions:
        job_id = position.get("job_id")
        try:
            last_error = None
            for _ in range(2):
                try:
                    profile = save_jd(jd_reader(job_id))
                    break
                except Exception as error:
                    last_error = error
            else:
                raise last_error
            sources = source_folders({**position, **profile}, available)
            if not sources:
                skipped.append({"job_id": job_id, "title": profile["title"],
                                "reason": "没有对应的本地岗位简历文件夹"})
                continue
            for source in sources:
                item = run(job_id, source["path"], batch_id, evaluator)
                results.append({key: item[key] for key in (
                    "run_id", "job_id", "job_title", "source_folder", "total", "high_count",
                    "parse_failed", "directory", "report", "status")})
                used_folders.add(source["path"])
        except Exception as error:
            errors.append({"job_id": job_id, "title": position.get("title"),
                           "reason": type(error).__name__ + ": " + str(error)[:240]})
    result = {
        "batch_id": batch_id,
        "status": "completed" if not errors and not skipped else "completed_with_gaps",
        "started_at": started_at,
        "completed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "position_count": len(positions), "matched_jobs": len({item["job_id"] for item in results}),
        "matched_folders": len(results), "resume_count": sum(item["total"] for item in results),
        "high_count": sum(item["high_count"] for item in results),
        "parse_failed": sum(item["parse_failed"] for item in results),
        "results": results, "skipped_jobs": skipped, "errors": errors,
        "unmatched_folders": [item for item in available if item["path"] not in used_folders],
        "matching_engine": "TypeSafe AI Jev", "requested_model": settings.typesafe_model,
        "external_actions": 0,
    }
    if not results:
        result["status"] = "failed_no_matching_folders"
    path = _root() / "batches" / f"{batch_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_write(path, json.dumps(result, ensure_ascii=False, indent=2).encode())
    return result


def _write_report(result, profile):
    esc = html.escape
    cards = []
    selected = sorted((r for r in result["rows"] if r["copy"]), key=lambda r: -r["score"])
    for index, row in enumerate(selected, 1):
        relative = Path(row["copy"]).relative_to(result["directory"]).as_posix()
        from urllib.parse import quote
        cards.append(f'<article><h2>{index}. <a href="{quote(relative)}">{esc(row["filename"])}</a>'
                     f' · {row["score"]}%</h2><p>Jev 自动高匹配 · 模型 {esc(row.get("model"))}'
                     f' · 置信度 {row.get("confidence")}</p><p>核心技能概率 {row.get("required_skills_probability")}'
                     f' · 硬条件概率 {row.get("hard_requirements_probability")} · 级别 {esc(row.get("seniority"))}</p></article>')
    all_rows = "".join(f'<tr><td>{esc(r["filename"])}</td><td>{r["score"] if r["score"] is not None else "未评分"}</td>'
                       f'<td>{esc(r["status"])}</td></tr>' for r in result["rows"])
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>高匹配简历自动结果</title>
<style>body{{font:15px/1.7 system-ui;max-width:1080px;margin:32px auto;padding:0 20px;color:#222}}
h1{{font-size:24px}}h2{{font-size:18px}}article{{border-top:1px solid #ccc;padding:20px 0}}
pre{{white-space:pre-wrap}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}
a{{color:#175db1}}p,li,td{{overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse}}</style>
<h1>{esc(profile["title"])} · {esc(result["job_id"])} · 自动匹配结果</h1>
<p>已检查 {result["total"]} 份；严格大于70%并复制 {result["high_count"]} 份；解析失败 {result["parse_failed"]} 份。</p>
<p>匹配由 TypeSafe AI Jev 对脱敏后的简历正文和当前 JD 作结构化概率判断。超过70分且技能、硬条件、
置信度与级别门槛同时通过才复制。模型分数不是录用结论；未上传北森。原件未移动、未删除。</p>
<p>来源目录：{esc(result["source_folder"])}；JD 读取时间：{esc(str(profile["collected_at"]))}</p>
<details><summary>完整 JD 和复核条件</summary><h2>工作职责</h2><pre>{esc(profile["duties"])}</pre>
<h2>任职资格</h2><pre>{esc(profile["qualifications"])}</pre></details>
{''.join(cards) or '<p>本批没有超过70%的可复制简历。</p>'}
<details><summary>全部匹配结果</summary><table><tr><th>简历</th><th>覆盖率 %</th><th>状态</th></tr>{all_rows}</table></details></html>"""
    private_write(Path(result["report"]), page.encode())
    private_write(Path(result["directory"]) / "岗位JD.json", json.dumps(profile, ensure_ascii=False, indent=2).encode())


def high_match_provenance(job_id, file_ids):
    """Only saved copies matching the current JD can enter upload history previews."""
    profile = load_jd(job_id)
    if not profile.get("match_hash"):
        raise ValueError("请重新读取 JD 并生成当前版本的高匹配审核夹")
    eligible = set()
    for path in (_root() / "runs").glob("*.json"):
        result = json.loads(path.read_text())
        if (result["job_id"] != job_id or result.get("match_hash") != profile.get("match_hash")
                or result.get("matching_engine") != "TypeSafe AI Jev"):
            continue
        for row in result["rows"]:
            if row.get("copy") and row.get("high"):
                copy = Path(row["copy"])
                if copy.is_file() and not copy.is_symlink() and hashlib.sha256(copy.read_bytes()).hexdigest() == row["file_id"]:
                    eligible.add(row["file_id"])
    if not set(file_ids).issubset(eligible):
        raise ValueError("本批含未完成当前岗位 JD 自动匹配或未超过70%的简历，请先运行全部岗位自动匹配")


def review_asset(run_id, asset):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("审核批次无效")
    manifest = _root() / "runs" / f"{run_id}.json"
    if not manifest.is_file():
        raise ValueError("审核批次不存在")
    result = json.loads(manifest.read_text())
    target = Path(result["directory"]).resolve()
    path = Path(result["report"]) if asset == "report" else target / asset
    allowed = {Path(result["report"]), *(Path(r["copy"]) for r in result["rows"] if r.get("copy"))}
    if path not in allowed or path.is_symlink() or not path.resolve().is_relative_to(target) or not path.is_file():
        raise ValueError("审核文件不可用")
    return path
