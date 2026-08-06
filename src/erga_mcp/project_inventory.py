from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Evidence
from .resume import latex_to_text

_TOKEN = re.compile(r"[a-z0-9+#.]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_DISALLOWED_LATEX = ("\\input", "\\include", "\\write18", "\\immediate\\write")
_CONTROL_SEQUENCE = re.compile(r"\\([A-Za-z@]+|.)")
_ALLOWED_CONTROL_SEQUENCES = frozenset(
    {
        "resumeProjectHeading",
        "resumeItemListStart",
        "resumeItemListEnd",
        "resumeItem",
        "href",
        "textbf",
        "textit",
        "&",
        "#",
        "$",
        "%",
        "_",
        ",",
        "\\",
    }
)
_REQUIREMENT_MARKERS = (
    "required",
    "requirements",
    "minimum qualification",
    "basic qualification",
    "must have",
    "must-have",
)


@dataclass(frozen=True)
class ProjectSelection:
    """One deterministic project choice with its job-description overlap."""

    id: str
    title: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True)
class ProjectCandidate:
    id: str
    title: str
    latex: str
    evidence_ids: tuple[str, ...]
    bullet_evidence_ids: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = ()
    git_repositories: tuple[str, ...] = ()


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        term
        for term in _TOKEN.findall(value.casefold())
        if len(term) > 1 and term not in _STOP_WORDS
    )


def _required_terms(job_description: str) -> frozenset[str]:
    return frozenset(
        term
        for line in job_description.splitlines()
        if any(marker in line.casefold() for marker in _REQUIREMENT_MARKERS)
        for term in _terms(line)
    )


def _validate_candidate(candidate: ProjectCandidate, approved_ids: frozenset[str]) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", candidate.id):
        raise ValueError("project inventory IDs must be lowercase safe identifiers")
    if not candidate.title.strip():
        raise ValueError("project inventory titles must be non-empty")
    if not candidate.evidence_ids:
        raise ValueError("project inventory entries require approved evidence IDs")
    if any(evidence_id not in approved_ids for evidence_id in candidate.evidence_ids):
        raise ValueError("project inventory entries require approved evidence IDs")
    if any(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
        for repository in candidate.git_repositories
    ):
        raise ValueError("project inventory git_repositories must contain GitHub owner/repo names")
    if any(marker in candidate.latex for marker in _DISALLOWED_LATEX):
        raise ValueError("project inventory LaTeX contains a disallowed command")
    unknown_commands = {
        match.group(1)
        for match in _CONTROL_SEQUENCE.finditer(candidate.latex)
        if match.group(1) not in _ALLOWED_CONTROL_SEQUENCES
    }
    if unknown_commands:
        raise ValueError("project inventory LaTeX contains a disallowed command")
    if candidate.latex.count(r"\resumeProjectHeading") != 1:
        raise ValueError("each project inventory entry must contain exactly one project heading")
    if (
        candidate.latex.count(r"\resumeItemListStart") != 1
        or candidate.latex.count(r"\resumeItemListEnd") != 1
    ):
        raise ValueError("each project inventory entry must contain one resume item list")
    bullet_count = candidate.latex.count(r"\resumeItem{")
    if bullet_count != len(candidate.bullet_evidence_ids) or any(
        not evidence_ids for evidence_ids in candidate.bullet_evidence_ids
    ):
        raise ValueError("project inventory bullet_evidence_ids must map every bullet")
    if any(
        evidence_id not in approved_ids
        for evidence_ids in candidate.bullet_evidence_ids
        for evidence_id in evidence_ids
    ):
        raise ValueError("project inventory bullet_evidence_ids require approved evidence IDs")
    if frozenset(candidate.evidence_ids) != frozenset(
        evidence_id
        for evidence_ids in candidate.bullet_evidence_ids
        for evidence_id in evidence_ids
    ):
        raise ValueError("project inventory evidence_ids must match bullet_evidence_ids")


def _candidate_from_json(value: object) -> ProjectCandidate:
    if not isinstance(value, dict):
        raise ValueError("project inventory entries must be objects")

    def string(name: str) -> str:
        item = value.get(name)
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"project inventory {name} must be a non-empty string")
        return item.strip()

    def string_list(name: str) -> tuple[str, ...]:
        item = value.get(name)
        if not isinstance(item, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in item
        ):
            raise ValueError(f"project inventory {name} must be a list of non-empty strings")
        return tuple(entry.strip() for entry in item)

    def nested_string_list(name: str) -> tuple[tuple[str, ...], ...]:
        item = value.get(name)
        if not isinstance(item, list) or any(
            not isinstance(entries, list)
            or not entries
            or any(not isinstance(entry, str) or not entry.strip() for entry in entries)
            for entries in item
        ):
            raise ValueError(f"project inventory {name} must be a list of non-empty string lists")
        return tuple(tuple(entry.strip() for entry in entries) for entries in item)

    def optional_string_list(name: str) -> tuple[str, ...]:
        item = value.get(name, [])
        if not isinstance(item, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in item
        ):
            raise ValueError(f"project inventory {name} must be a list of non-empty strings")
        return tuple(entry.strip() for entry in item)

    return ProjectCandidate(
        id=string("id"),
        title=string("title"),
        latex=string("latex"),
        evidence_ids=string_list("evidence_ids"),
        bullet_evidence_ids=nested_string_list("bullet_evidence_ids"),
        tags=string_list("tags"),
        git_repositories=optional_string_list("git_repositories"),
    )


def load_project_inventory(
    path: Path, evidence: Sequence[Evidence]
) -> tuple[ProjectCandidate, ...]:
    """Load a locally approved, LaTeX-ready project arsenal from one JSON file."""
    if not path.is_file():
        raise FileNotFoundError(f"project inventory does not exist: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"project inventory is not valid JSON: {path}") from error
    if not isinstance(payload, list):
        raise ValueError("project inventory must be a JSON array")

    approved_ids = frozenset(item.id for item in evidence if item.approved)
    candidates = tuple(_candidate_from_json(item) for item in payload)
    seen_ids: set[str] = set()
    for candidate in candidates:
        _validate_candidate(candidate, approved_ids)
        if candidate.id in seen_ids:
            raise ValueError(f"project inventory contains duplicate ID: {candidate.id}")
        seen_ids.add(candidate.id)
    return candidates


def _score(candidate: ProjectCandidate, job_description: str) -> int:
    candidate_terms = _terms(latex_to_text(candidate.latex)) | _terms(" ".join(candidate.tags))
    matched_terms = candidate_terms & _terms(job_description)
    required_matches = candidate_terms & _required_terms(job_description)
    return len(matched_terms) + 4 * len(required_matches)


def select_projects(
    candidates: Sequence[ProjectCandidate],
    job_description: str,
    *,
    max_projects: int,
    minimum_bullets: int = 1,
) -> tuple[ProjectCandidate, ...]:
    """Select role-relevant, sufficiently substantial candidates deterministically."""
    if max_projects < 1:
        raise ValueError("max_projects must be positive")
    if minimum_bullets < 1:
        raise ValueError("minimum_bullets must be positive")
    eligible = (
        candidates
        if minimum_bullets == 1
        else tuple(
            candidate
            for candidate in candidates
            if len(candidate.bullet_evidence_ids) >= minimum_bullets
        )
    )
    ranked = sorted(
        ((candidate, _score(candidate, job_description)) for candidate in eligible),
        key=lambda item: (-item[1], item[0].id),
    )
    return tuple(candidate for candidate, score in ranked if score > 0)[:max_projects]


def select_project_rationales(
    candidates: Sequence[ProjectCandidate],
    job_description: str,
    *,
    max_projects: int,
    minimum_bullets: int = 1,
) -> tuple[ProjectSelection, ...]:
    """Return each selected project with the exact job terms that justified its selection."""
    job_terms = _terms(job_description)
    return tuple(
        ProjectSelection(
            id=candidate.id,
            title=candidate.title,
            matched_terms=tuple(
                sorted(
                    (_terms(latex_to_text(candidate.latex)) | _terms(" ".join(candidate.tags)))
                    & job_terms
                )
            ),
        )
        for candidate in select_projects(
            candidates,
            job_description,
            max_projects=max_projects,
            minimum_bullets=minimum_bullets,
        )
    )
