"""Plain-language résumé layout preferences for the Discord adapter."""

from __future__ import annotations

import re

from erga_mcp.config import ResumeSettings

_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_EXPERIENCE = r"experiences?"
_PROJECT = r"projects?"


def _normalize(content: str) -> str:
    normalized = " ".join(content.casefold().replace("résumé", "resume").split())
    normalized = normalized.replace("–", "-").replace("—", "-")
    for word, value in _NUMBER_WORDS.items():
        normalized = re.sub(rf"\b{word}\b", str(value), normalized)
    return normalized


def _has_resume_preference_subject(content: str) -> bool:
    return "resume" in content or bool(
        re.search(r"\b(?:experience|project)s?\s+bullets?\b", content)
        or re.search(r"\bbullets?\s+(?:per|for)\s+(?:experience|project)s?\b", content)
    )


def _has_persistent_update_intent(content: str) -> bool:
    if re.search(r"\b(?:set|change|update|configure)\b", content):
        return True
    persistent_markers = (
        "default",
        "preference",
        "setting",
        "from now on",
        "going forward",
        "always",
        "keep resumes",
        "keep my resumes",
        "use for every resume",
        "use for all resumes",
    )
    return any(marker in content for marker in persistent_markers)


def _limits_for(content: str, label: str) -> tuple[int, int] | None:
    range_token = r"(?P<minimum>\d+)\s*(?:-|to|through)\s*(?P<maximum>\d+)"
    patterns = (
        rf"{range_token}\s+(?:(?:bullets?\s+)?(?:per|for)\s+{label}|{label}\s+bullets?)",
        rf"(?:per\s+)?{label}(?:\s+bullets?)?\s*(?::|=|at|with|to)?\s*{range_token}",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return int(match.group("minimum")), int(match.group("maximum"))

    exact_token = r"(?P<count>\d+)"
    exact_patterns = (
        rf"{exact_token}\s+(?:(?:bullets?\s+)?(?:per|for)\s+{label}|{label}\s+bullets?)",
        rf"(?:per\s+)?{label}(?:\s+bullets?)?\s*(?::|=|at|with|to)?\s*{exact_token}\s*bullets?",
    )
    for pattern in exact_patterns:
        match = re.search(pattern, content)
        if match:
            count = int(match.group("count"))
            return count, count
    return None


def parse_resume_preference_update(content: str) -> dict[str, int] | None:
    """Parse only explicit persistent-default requests, never ordinary tailoring prompts."""
    normalized = _normalize(content)
    if not _has_resume_preference_subject(normalized) or not _has_persistent_update_intent(
        normalized
    ):
        return None

    updates: dict[str, int] = {}
    if "unlimited pages" in normalized or "no page limit" in normalized:
        updates["max_pages"] = 0
    else:
        page_match = re.search(r"\b(?P<count>\d+)[ -]?pages?\b", normalized)
        if page_match:
            updates["max_pages"] = int(page_match.group("count"))

    experience_limits = _limits_for(normalized, _EXPERIENCE)
    project_limits = _limits_for(normalized, _PROJECT)
    shared_limits = bool(
        re.search(r"\bexperience\s*/\s*projects?\b", normalized)
        or re.search(r"\bexperience\s+and\s+projects?\b", normalized)
    )
    if shared_limits and experience_limits is not None and project_limits is None:
        project_limits = experience_limits
    if shared_limits and project_limits is not None and experience_limits is None:
        experience_limits = project_limits
    if experience_limits is not None:
        updates["experience_min_bullets"], updates["experience_max_bullets"] = experience_limits
    if project_limits is not None:
        updates["project_min_bullets"], updates["project_max_bullets"] = project_limits
    return updates or None


def is_resume_preference_query(content: str) -> bool:
    """Recognize a request to see the effective persistent résumé defaults."""
    normalized = _normalize(content)
    if "resume" not in normalized:
        return False
    has_preferences = any(
        marker in normalized for marker in ("default", "preference", "setting", "configuration")
    )
    asks_to_see = bool(re.search(r"\b(?:show|see|list|what|current|check)\b", normalized))
    return has_preferences and asks_to_see


def render_resume_preferences(settings: ResumeSettings, *, updated: bool = False) -> str:
    """Render the effective shape settings without exposing config or tool vocabulary."""
    if settings.max_pages == 0:
        page_limit = "no hard page cap"
    else:
        suffix = "page" if settings.max_pages == 1 else "pages"
        page_limit = f"up to {settings.max_pages} {suffix}"
    heading = "✓ Résumé defaults updated" if updated else "Your résumé defaults"
    return (
        f"**{heading}**\n"
        f"• Length: {page_limit}\n"
        f"• Each experience: {settings.experience_min_bullets}–"
        f"{settings.experience_max_bullets} bullets\n"
        f"• Each project: {settings.project_min_bullets}–"
        f"{settings.project_max_bullets} bullets\n\n"
        "These apply to future tailored résumés. You can change them anytime in normal language."
    )
