"""Read-only paper radar for evidence-backed passive technical sourcing.

The default path queries public metadata APIs only. It never downloads papers,
contacts authors, or treats an unavailable provider as an empty result.
"""
from __future__ import annotations

import math
import re
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable

import httpx


SOURCES = ("arXiv", "OpenAlex", "Semantic Scholar", "DBLP", "Crossref", "会议官网")
SOURCE_URLS = {
    "OpenAlex": "https://api.openalex.org/works",
    "Semantic Scholar": "https://api.semanticscholar.org/graph/v1/paper/search",
    "DBLP": "https://dblp.org/search/publ/api",
    "Crossref": "https://api.crossref.org/works",
}
STOPWORDS = set("的 和 与 及 或 在 以 用 于 通过 负责 具有 相关 优先 熟悉 能够 参与 工作 岗位 要求 经验".split())
KNOWN_TERMS = {
    "人工智能", "机器学习", "深度学习", "大模型", "自然语言处理", "计算机视觉", "多模态",
    "强化学习", "推荐系统", "知识图谱", "语音", "芯片", "芯片设计", "机器人", "自动驾驶",
    "软件工程", "测试开发", "数据科学", "隐私计算", "密码学", "生物信息", "Agent",
    "LLM", "RAG", "Transformer", "computer vision", "machine learning", "deep learning",
    "natural language processing", "reinforcement learning", "robotics", "distributed systems",
}
VENUE_LEVELS = {
    "nature": 1.0, "science": 1.0, "neurips": 0.98, "icml": 0.98, "iclr": 0.96,
    "cvpr": 0.98, "iccv": 0.96, "eccv": 0.93, "acl": 0.97, "emnlp": 0.94,
    "aaai": 0.93, "ijcai": 0.92, "kdd": 0.95, "www": 0.90, "sigmod": 0.96,
    "isca": 0.94, "micros": 0.93, "usenix": 0.92, "tpami": 0.98,
}


def _tokens(value: str) -> set[str]:
    raw = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}|[\u4e00-\u9fff]{2,}", value or "")
    return {item.lower() for item in raw if item.lower() not in STOPWORDS}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_jd(jd: str) -> dict[str, Any]:
    if not isinstance(jd, str) or not 10 <= len(jd.strip()) <= 20_000:
        raise ValueError("请提供 10 至 20000 个字符的岗位 JD")
    text = jd.strip()
    lower = text.lower()
    level = "未识别"
    for marker, value in (("实习", "实习生"), ("intern", "实习生"), ("校招", "初级"),
                          ("初级", "初级"), ("高级", "高级"), ("senior", "高级"),
                          ("专家", "专家"), ("principal", "专家"), ("负责人", "负责人"),
                          ("lead", "负责人"), ("总监", "负责人")):
        if marker in lower:
            level = value
            break
    cities = "北京 上海 广州 深圳 杭州 苏州 成都 武汉 南京 西安 天津 重庆 香港 新加坡 纽约 伦敦".split()
    locations = [city for city in cities if city.lower() in lower]
    skills = sorted({term for term in KNOWN_TERMS if term.lower() in lower}, key=str.casefold)
    all_terms = _tokens(text)
    direction_terms = sorted((all_terms - _tokens(" ".join(skills))), key=str.casefold)
    institutions = sorted(set(re.findall(r"[\w一-龥]{2,30}(?:大学|学院|研究院|实验室|研究所|Institute|University|Lab)", text, re.I)))
    preferences = sorted(set(re.findall(r"(?:偏好|优先|prefer(?:red)?|来自)[:： ]*([^。\n;；]+)", text, re.I)))
    return {
        "skills": skills,
        "directions": direction_terms[:30],
        "level": level,
        "locations": locations,
        "institution_preferences": institutions + preferences[:10],
        "query": " ".join(skills[:8] or direction_terms[:8]),
        "evidence": "deterministic_jd_parser",
    }


def _fetch_json(url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=8, follow_redirects=True, headers=headers or {}) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _fetch_text(url: str, params: dict[str, Any]) -> str:
    with httpx.Client(timeout=8, follow_redirects=True) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.text


def _author(raw: dict[str, Any], *, role: str = "") -> dict[str, Any]:
    name = _clean(raw.get("name") or raw.get("author_name") or raw.get("display_name"))
    ids = raw.get("ids") or {}
    orcid = _clean(raw.get("orcid") or ids.get("orcid"))
    if orcid and "orcid.org/" in orcid:
        orcid = orcid.split("orcid.org/", 1)[1]
    institutions = raw.get("institutions") or raw.get("affiliations") or []
    if isinstance(institutions, str):
        institutions = [institutions]
    institutions = [_clean(item.get("display_name") if isinstance(item, dict) else item) for item in institutions]
    return {
        "name": name,
        "role": role,
        "orcid": orcid,
        "email": _clean(raw.get("email")),
        "homepage": _clean(raw.get("homepage") or raw.get("url")),
        "github": _clean(raw.get("github")),
        "institutions": [item for item in institutions if item],
        "corresponding": bool(raw.get("corresponding") or raw.get("is_corresponding")),
    }


def _paper(source: str, raw: dict[str, Any], *, authors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    title = _clean(raw.get("title") or raw.get("name"))
    abstract = _clean(raw.get("abstract") or raw.get("description") or raw.get("abstract_inverted_index"))
    if isinstance(raw.get("abstract_inverted_index"), dict):
        abstract = " ".join(raw["abstract_inverted_index"].keys())
    year = raw.get("year") or raw.get("publication_year") or raw.get("published") or raw.get("published-print")
    if isinstance(year, dict):
        year = (year.get("date-parts") or [[None]])[0][0]
    try:
        year = int(year) if year else None
    except (TypeError, ValueError):
        year = None
    venue = _clean(raw.get("venue") or raw.get("container-title") or raw.get("journal") or raw.get("publication_venue"))
    citations = raw.get("citationCount") or raw.get("cited_by_count") or raw.get("is-referenced-by-count") or raw.get("citations") or 0
    try:
        citations = int(citations or 0)
    except (TypeError, ValueError):
        citations = 0
    doi = _clean(raw.get("doi") or raw.get("DOI"))
    url = _clean(raw.get("url") or raw.get("id") or raw.get("landing_page_url"))
    return {
        "source": source, "title": title, "abstract": abstract, "year": year,
        "venue": venue, "citations": citations, "doi": doi, "url": url,
        "authors": authors if authors is not None else [_author(item) for item in (raw.get("authors") or [])],
        "evidence": {"source": source, "provider_record": True},
    }


def _search_arxiv(query: str, limit: int) -> list[dict[str, Any]]:
    xml = _fetch_text("https://export.arxiv.org/api/query", {"search_query": f"all:{query}", "max_results": limit})
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows = []
    for entry in root.findall("a:entry", ns):
        authors = [_author({"name": item.findtext("a:name", default="", namespaces=ns)}) for item in entry.findall("a:author", ns)]
        published = entry.findtext("a:published", default="", namespaces=ns)
        rows.append(_paper("arXiv", {"title": entry.findtext("a:title", default="", namespaces=ns),
                                    "abstract": entry.findtext("a:summary", default="", namespaces=ns),
                                    "year": published[:4], "url": entry.findtext("a:id", default="", namespaces=ns)}, authors=authors))
    return rows


def _search_openalex(query: str, limit: int) -> list[dict[str, Any]]:
    data = _fetch_json(SOURCE_URLS["OpenAlex"], {"search": query, "per-page": limit, "select": "id,title,abstract_inverted_index,publication_year,cited_by_count,doi,primary_location,authorships"})
    rows = []
    for item in data.get("results", []):
        authors = []
        for authorship in item.get("authorships", []):
            author = dict(authorship.get("author") or {})
            author["institutions"] = authorship.get("institutions", [])
            authors.append(_author(author))
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        rows.append(_paper("OpenAlex", {**item, "venue": source.get("display_name"), "url": location.get("landing_page_url")}, authors=authors))
    return rows


def _search_semantic(query: str, limit: int) -> list[dict[str, Any]]:
    data = _fetch_json(SOURCE_URLS["Semantic Scholar"], {"query": query, "limit": limit,
                                                            "fields": "title,abstract,year,venue,citationCount,authors,externalIds,url"})
    rows = []
    for item in data.get("data", []):
        ids = item.get("externalIds") or {}
        rows.append(_paper("Semantic Scholar", {**item, "doi": ids.get("DOI"), "authors": item.get("authors", [])}))
    return rows


def _search_dblp(query: str, limit: int) -> list[dict[str, Any]]:
    data = _fetch_json(SOURCE_URLS["DBLP"], {"q": query, "h": limit, "format": "json"})
    hits = ((data.get("result") or {}).get("hits") or {}).get("hit", [])
    rows = []
    for hit in hits:
        info = hit.get("info") or {}
        authors = [_author({"name": item.get("text", "") if isinstance(item, dict) else item}) for item in (info.get("authors") or {}).get("author", [])]
        rows.append(_paper("DBLP", {"title": info.get("title"), "year": info.get("year"), "venue": info.get("venue"), "url": info.get("ee") or info.get("url")}, authors=authors))
    return rows


def _search_crossref(query: str, limit: int) -> list[dict[str, Any]]:
    data = _fetch_json(SOURCE_URLS["Crossref"], {"query": query, "rows": limit, "select": "title,author,published,container-title,DOI,URL,is-referenced-by-count"})
    rows = []
    for item in (data.get("message") or {}).get("items", []):
        authors = [_author({"name": f"{item.get('given', '')} {item.get('family', '')}"}) for item in item.get("author", [])]
        rows.append(_paper("Crossref", {**item, "title": (item.get("title") or [""])[0], "venue": (item.get("container-title") or [""])[0]}, authors=authors))
    return rows


FETCHERS: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {
    "arXiv": _search_arxiv, "OpenAlex": _search_openalex, "Semantic Scholar": _search_semantic,
    "DBLP": _search_dblp, "Crossref": _search_crossref,
}


def _source_search(source: str, query: str, limit: int) -> tuple[str, list[dict[str, Any]], str | None]:
    if source == "会议官网":
        return source, [], "需要在设置中配置会议官网白名单；当前仅保留来源位，不抓取未知站点"
    try:
        return source, FETCHERS[source](query, limit), None
    except Exception as error:  # provider failures are evidence gaps, not empty results
        return source, [], f"{type(error).__name__}: {_clean(error)[:180]}"


def _venue_score(venue: str) -> float:
    lowered = (venue or "").lower()
    return max((score for name, score in VENUE_LEVELS.items() if name in lowered), default=0.45)


def _score_paper(paper: dict[str, Any], profile: dict[str, Any], year: int) -> dict[str, Any]:
    target = _tokens(" ".join(profile["skills"] + profile["directions"]))
    text = _tokens(f"{paper.get('title', '')} {paper.get('abstract', '')} {paper.get('venue', '')}")
    similarity = len(target & text) / max(1, len(target))
    recency = max(0.0, 1.0 - max(0, year - (paper.get("year") or year)) / 10)
    citation = min(1.0, math.log1p(max(0, paper.get("citations", 0))) / math.log1p(500))
    venue = _venue_score(paper.get("venue", ""))
    total = round(0.52 * similarity + 0.20 * venue + 0.16 * recency + 0.12 * citation, 4)
    return {**paper, "match": {"total": total, "direction_similarity": round(similarity, 4),
                               "venue_level": venue, "recency": round(recency, 4), "citation": round(citation, 4)}}


def _identity(author: dict[str, Any]) -> tuple[str, str]:
    if author.get("orcid"):
        return "orcid", author["orcid"].lower()
    if author.get("email"):
        return "email", author["email"].lower()
    if author.get("homepage") or author.get("github"):
        return "profile", (author.get("homepage") or author.get("github")).lower()
    return "name_institution", f"{author.get('name', '').lower()}|{'|'.join(sorted(author.get('institutions') or []))}"


def _mask_email(email: str) -> str:
    if "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def build_candidates(papers: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for paper in papers:
        authors = paper.get("authors") or []
        role_rows = [(index, author, ["一作", "二作", "三作"][index]) for index, author in enumerate(authors[:3])]
        role_rows.extend((index, author, "通讯作者") for index, author in enumerate(authors)
                         if author.get("corresponding") and index >= 3)
        for _, author, role in role_rows:
            enriched = {**author, "role": role}
            key = _identity(enriched)
            item = groups.setdefault(key, {"candidate_id": uuid.uuid4().hex, "name": enriched.get("name", "未知作者"),
                                           "roles": set(), "institutions": set(), "papers": [], "orcid": "",
                                           "email": "", "homepage": "", "github": "", "identity_confidence": "低"})
            item["roles"].add(enriched["role"])
            item["roles"].add("通讯作者" if enriched.get("corresponding") else "")
            item["institutions"].update(enriched.get("institutions") or [])
            item["orcid"] = item["orcid"] or enriched.get("orcid", "")
            item["email"] = item["email"] or enriched.get("email", "")
            item["homepage"] = item["homepage"] or enriched.get("homepage", "")
            item["github"] = item["github"] or enriched.get("github", "")
            item["papers"].append({"title": paper.get("title"), "year": paper.get("year"), "venue": paper.get("venue"),
                                    "source": paper.get("source"), "url": paper.get("url"), "doi": paper.get("doi"),
                                    "match": paper.get("match", {})})
            if enriched.get("orcid") or enriched.get("email") or enriched.get("homepage") or enriched.get("github"):
                item["identity_confidence"] = "高"
            elif len(item["institutions"]) == 1:
                item["identity_confidence"] = "中"
    result = []
    for item in groups.values():
        item["roles"] = sorted(role for role in item["roles"] if role)
        item["institutions"] = sorted(item["institutions"])
        item["paper_count"] = len(item["papers"])
        item["best_match"] = max((paper["match"].get("total", 0) for paper in item["papers"]), default=0)
        item["recommendation_score"] = round(item["best_match"] + min(0.2, 0.04 * max(0, item["paper_count"] - 1)), 4)
        item["email_masked"] = _mask_email(item["email"])
        item["status"] = "待 HR 审批"
        result.append(item)
    return sorted(result, key=lambda item: (-item["recommendation_score"], item["name"]))


def contact_drafts(candidates: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    direction = "、".join(profile.get("skills")[:3] or profile.get("directions")[:3] or ["相关技术方向"])
    drafts = []
    for candidate in candidates:
        evidence = candidate.get("papers", [])[:3]
        links = "\n".join(f"- {paper.get('title')}（{paper.get('year') or '年份未返回'}，{paper.get('source')}）: {paper.get('url') or paper.get('doi') or '链接未返回'}" for paper in evidence)
        drafts.append({"candidate_id": candidate["candidate_id"], "name": candidate["name"],
                       "email": candidate.get("email_masked", ""), "channel": "邮件/私信",
                       "subject": f"关于{direction}方向的交流机会",
                       "body": f"您好 {candidate['name']}，我们关注到您在{direction}方向的研究成果，想邀请您了解一个相关技术岗位。\n\n公开论文证据：\n{links}\n\n如您愿意交流，我们可以根据您的方便安排一次简短沟通。\n\n此稿件需 HR 审阅后发送。",
                       "evidence": evidence})
    return drafts


def run(jd: str, sources: list[str] | None = None, limit: int = 8,
        year_from: int | None = None, year_to: int | None = None,
        source_payloads: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    profile = parse_jd(jd)
    selected = [source for source in (sources or list(SOURCES)) if source in SOURCES]
    if not selected:
        raise ValueError("至少选择一个论文来源")
    limit = max(1, min(20, int(limit)))
    current_year = date.today().year
    statuses = []
    papers: list[dict[str, Any]] = []
    remote_sources = []
    for source in selected:
        if source_payloads and source in source_payloads:
            rows = [row if row.get("source") else _paper(source, row) for row in source_payloads[source]]
            statuses.append({"source": source, "status": "fixture_loaded", "count": len(rows)})
            papers.extend(rows)
        else:
            remote_sources.append(source)
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(remote_sources)))) as pool:
        for source, rows, error in pool.map(lambda name: _source_search(name, profile["query"], limit), remote_sources):
            statuses.append({"source": source, "status": "completed" if not error else "unavailable",
                             "count": len(rows), "error": error})
            papers.extend(rows)
    unique: dict[str, dict[str, Any]] = {}
    for paper in papers:
        if not paper.get("title"):
            continue
        # Prefer DOI identity, while still merging provider records that expose
        # a DOI on only one side of an otherwise identical title.
        title_key = "title:" + re.sub(r"\W+", "", paper.get("title", "").lower())
        doi_key = "doi:" + paper["doi"].lower() if paper.get("doi") else ""
        key = next((candidate for candidate in (doi_key, title_key) if candidate and candidate in unique), doi_key or title_key)
        if key in unique:
            existing = unique[key]
            by_identity = {_identity(author): author for author in existing.get("authors", [])}
            for author in paper.get("authors", []):
                by_identity.setdefault(_identity(author), author)
            existing["authors"] = list(by_identity.values())
            for field in ("abstract", "venue", "doi", "url", "citations", "year"):
                if not existing.get(field) and paper.get(field):
                    existing[field] = paper[field]
        else:
            unique[key] = paper
    scored = [_score_paper(paper, profile, current_year) for paper in unique.values()]
    filtered = [paper for paper in scored if (year_from is None or not paper.get("year") or paper["year"] >= year_from)
                and (year_to is None or not paper.get("year") or paper["year"] <= year_to)]
    filtered.sort(key=lambda paper: (-paper["match"]["total"], -(paper.get("citations") or 0)))
    candidates = build_candidates(filtered[:limit * 4], profile)
    return {"run_id": uuid.uuid4().hex, "profile": profile, "sources": statuses,
            "papers": filtered[:limit * 4], "candidates": candidates[:limit],
            "candidate_count": len(candidates[:limit]), "evidence_boundary": "公开元数据匹配；作者身份和通讯作者标记需 HR 复核",
            "status": "completed" if any(item["count"] for item in statuses) else "completed_with_gaps"}
