"""YAML-backed, deterministic candidate filtering."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


@dataclass(frozen=True)
class CandidateFilterConfig:
    keywords: tuple[str, ...] = ()
    keyword_match: str = "any"
    min_experience_years: float | None = None
    max_experience_years: float | None = None
    max_inactive_days: int | None = None
    min_age: int | None = None
    max_age: int | None = None
    max_salary_k: float | None = None
    exclude_status: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        keywords = tuple(str(value).strip() for value in self.keywords if str(value).strip())
        object.__setattr__(self, "keywords", keywords)
        object.__setattr__(self, "exclude_status", tuple(
            str(value).strip() for value in self.exclude_status if str(value).strip()
        ))
        if self.keyword_match not in {"any", "all"}:
            raise ValueError("keyword match must be 'any' or 'all'")
        for name, value in (
            ("min_experience_years", self.min_experience_years),
            ("max_experience_years", self.max_experience_years),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{name} must be a non-negative number")
        if (self.min_experience_years is not None and self.max_experience_years is not None
                and self.min_experience_years > self.max_experience_years):
            raise ValueError("minimum experience cannot exceed maximum experience")
        if (self.max_inactive_days is not None
                and (isinstance(self.max_inactive_days, bool)
                     or not isinstance(self.max_inactive_days, int)
                     or self.max_inactive_days < 0)):
            raise ValueError("max_inactive_days must be a non-negative integer")
        for name, value in (("min_age", self.min_age), ("max_age", self.max_age)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 16 or value > 100):
                raise ValueError(f"{name} must be an integer between 16 and 100")
        if self.min_age is not None and self.max_age is not None and self.min_age > self.max_age:
            raise ValueError("minimum age cannot exceed maximum age")
        if (self.max_salary_k is not None and
                (isinstance(self.max_salary_k, bool) or not isinstance(self.max_salary_k, (int, float))
                 or self.max_salary_k < 0)):
            raise ValueError("max_salary_k must be a non-negative number")


@dataclass(frozen=True)
class ExcludedCandidate:
    candidate: Mapping[str, Any]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CandidateFilterResult:
    included: tuple[Mapping[str, Any], ...]
    excluded: tuple[ExcludedCandidate, ...]


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def parse_candidate_filter_config(content: str) -> CandidateFilterConfig:
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError(f"unable to parse candidate filter YAML: {error}") from error
    root = _mapping(document, "YAML document")
    data = _mapping(root.get("candidate_filter"), "candidate_filter")
    keywords = _mapping(data.get("keywords"), "candidate_filter.keywords")
    experience = _mapping(data.get("experience"), "candidate_filter.experience")
    activity = _mapping(data.get("activity"), "candidate_filter.activity")
    age = _mapping(data.get("age"), "candidate_filter.age")
    salary = _mapping(data.get("salary"), "candidate_filter.salary")
    values = keywords.get("values", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError("candidate_filter.keywords.values must be a list of strings")
    excluded = data.get("exclude_status", [])
    if not isinstance(excluded, list) or any(not isinstance(value, str) for value in excluded):
        raise ValueError("candidate_filter.exclude_status must be a list of strings")
    return CandidateFilterConfig(
        keywords=tuple(values),
        keyword_match=keywords.get("match", "any"),
        min_experience_years=experience.get("min_years"),
        max_experience_years=experience.get("max_years"),
        max_inactive_days=activity.get("max_inactive_days"),
        min_age=age.get("min"),
        max_age=age.get("max"),
        max_salary_k=salary.get("max_k"),
        exclude_status=tuple(excluded),
    )


def load_candidate_filter_config(path: str | Path) -> CandidateFilterConfig:
    try:
        return parse_candidate_filter_config(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"unable to read candidate filter YAML: {error}") from error


def _activity_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _inactive_days(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if any(marker in text for marker in ("今日", "刚刚", "当前在线", "在线")):
        return 0
    if "昨日" in text:
        return 1
    match = __import__("re").search(r"(\d+)\s*(?:天|日)内?活跃", text)
    return int(match.group(1)) if match else None


def filter_candidates(
    candidates: Iterable[Mapping[str, Any]],
    config: CandidateFilterConfig,
    *,
    now: datetime | None = None,
) -> CandidateFilterResult:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(timezone.utc)
    included: list[Mapping[str, Any]] = []
    excluded: list[ExcludedCandidate] = []
    normalized_keywords = tuple(value.casefold() for value in config.keywords)

    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("each candidate must be a mapping")
        reasons: list[str] = []
        text = str(candidate.get("text") or "").casefold()
        if normalized_keywords:
            matches = [keyword in text for keyword in normalized_keywords]
            if not (all(matches) if config.keyword_match == "all" else any(matches)):
                reasons.append("keywords_not_matched")

        if config.min_experience_years is not None or config.max_experience_years is not None:
            years = candidate.get("experience_years")
            if isinstance(years, bool) or not isinstance(years, (int, float)):
                reasons.append("experience_missing")
            elif config.min_experience_years is not None and years < config.min_experience_years:
                reasons.append("experience_below_minimum")
            elif config.max_experience_years is not None and years > config.max_experience_years:
                reasons.append("experience_above_maximum")

        if config.max_inactive_days is not None:
            last_active = _activity_time(candidate.get("last_active_at"))
            inactive_days = _inactive_days(candidate.get("active_text"))
            if last_active is None and inactive_days is None:
                reasons.append("activity_missing")
            elif ((last_active is not None and
                   (last_active > current or current - last_active > timedelta(days=config.max_inactive_days)))
                  or (inactive_days is not None and inactive_days > config.max_inactive_days)):
                reasons.append("candidate_inactive")

        age = candidate.get("age")
        if config.min_age is not None or config.max_age is not None:
            if isinstance(age, bool) or not isinstance(age, int):
                reasons.append("age_missing")
            elif config.min_age is not None and age < config.min_age:
                reasons.append("age_below_minimum")
            elif config.max_age is not None and age > config.max_age:
                reasons.append("age_above_maximum")

        if config.max_salary_k is not None:
            salary = candidate.get("salary_max_k")
            if isinstance(salary, bool) or not isinstance(salary, (int, float)):
                reasons.append("salary_missing")
            elif salary > config.max_salary_k:
                reasons.append("salary_above_maximum")

        status = str(candidate.get("status") or "")
        if config.exclude_status and any(value in status for value in config.exclude_status):
            reasons.append("status_excluded")

        if reasons:
            excluded.append(ExcludedCandidate(candidate, tuple(reasons)))
        else:
            included.append(candidate)

    return CandidateFilterResult(tuple(included), tuple(excluded))
