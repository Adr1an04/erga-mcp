from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from erga_mcp.models import Evidence
from erga_mcp.resumes.artifacts import latex_to_text, resume_item_texts
from erga_mcp.resumes.quality import (
    ProjectIdentityProfile,
    build_project_identity_profile,
    compare_project_profiles,
)

_TOKEN = re.compile(r"[a-z0-9+#.]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "across",
        "at",
        "by",
        "change",
        "changes",
        "create",
        "created",
        "design",
        "designed",
        "for",
        "from",
        "full",
        "in",
        "into",
        "job",
        "jobs",
        "of",
        "on",
        "or",
        "project",
        "projects",
        "recruiting",
        "resume",
        "role",
        "roles",
        "that",
        "the",
        "through",
        "to",
        "with",
        "work",
        "working",
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
_ROLE_SIGNAL_CLUSTERS: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    (
        "real-time / interactive systems",
        frozenset(
            {"3d", "communication", "interactive", "latency", "real", "realtime", "rendering"}
        ),
        frozenset(
            {
                "3d",
                "emg",
                "imu",
                "latency",
                "real",
                "realtime",
                "rendering",
                "sensor",
            }
        ),
    ),
    (
        "production scale",
        frozenset({"billion", "distributed", "global", "million", "production", "scale"}),
        frozenset(
            {
                "deployed",
                "distributed",
                "infrastructure",
                "members",
                "production",
                "scale",
                "users",
            }
        ),
    ),
    (
        "machine learning / AI",
        frozenset({"agentic", "ai", "learning", "llm", "machine", "ml", "model", "models"}),
        frozenset(
            {
                "ai",
                "inference",
                "learning",
                "llm",
                "machine",
                "ml",
                "model",
                "pytorch",
                "tensorflow",
            }
        ),
    ),
    (
        "agentic developer tooling",
        frozenset({"agentic", "coding", "developer", "tools"}),
        frozenset(
            {
                "agentic",
                "coding",
                "llm",
            }
        ),
    ),
    (
        "delivery / quality ownership",
        frozenset(
            {"coding", "deploying", "deployment", "end", "ownership", "production", "testing"}
        ),
        frozenset(
            {
                "ci",
                "deploy",
                "deployed",
                "deployment",
                "open-source",
                "pytest",
                "quality",
                "reliability",
                "shipped",
                "testing",
                "tests",
            }
        ),
    ),
    (
        "data / platform systems",
        frozenset({"data", "distributed", "platform", "processing", "systems"}),
        frozenset(
            {
                "data",
                "distributed",
                "etl",
                "processing",
                "sqlite",
                "systems",
            }
        ),
    ),
)
_INTERNAL_RESEARCH_PATTERNS = (
    (
        "raw Git churn statistics",
        re.compile(
            r"(?:\+\d[\d,]*\s*/\s*-\d[\d,]*\s+lines?|\+\d[\d,]*\s+and\s+-\d[\d,]*\s+lines?)", re.I
        ),
    ),
    (
        "generic Git research taxonomy",
        re.compile(
            r"\b(?:testing|implementation|api|ui|security|persistence)"
            r"(?:/[a-z]+)+\s+work\b",
            re.I,
        ),
    ),
    (
        "internal Git provenance wording",
        re.compile(
            r"\b(?:git-backed change set|git-reviewed lines|diff-backed validation|"
            r"reviewed diffs|git diffs|git history)\b",
            re.I,
        ),
    ),
    (
        "commit/file accounting instead of an outcome",
        re.compile(
            r"(?:\b\d[\d,.]*\+?\s+(?:(?:implementation|source|test|code)\s+)?"
            r"(?:files?|commits?|lines?|languages?)\b|"
            r"\b\d[\d,.]*%\s+of\s+(?:code/)?test\s+files?\b)",
            re.I,
        ),
    ),
    (
        "code-churn wording",
        re.compile(r"\b(?:code|lines?)\s+(?:added|removed|changed)\b", re.I),
    ),
    (
        "generic low-signal résumé wording",
        re.compile(
            r"\b(?:added code|code added|helped with|responsible for|worked on|"
            r"various (?:features|tasks)|multiple tasks)\b",
            re.I,
        ),
    ),
)


@dataclass(frozen=True)
class ProjectSelection:
    """One deterministic project choice with its job-description overlap."""

    id: str
    title: str
    matched_terms: tuple[str, ...]
    matched_signals: tuple[str, ...] = ()
    narrative_signals: tuple[str, ...] = ()
    metric_categories: tuple[str, ...] = ()
    differentiators: tuple[str, ...] = ()
    evidence_tier: str = "C"
    quality_score: int = 0
    differentiation_score: int = 100
    selection_score: float = 0


@dataclass(frozen=True)
class ProjectCandidate:
    id: str
    title: str
    latex: str
    evidence_ids: tuple[str, ...]
    bullet_evidence_ids: tuple[tuple[str, ...], ...] = ()
    tags: tuple[str, ...] = ()
    git_repositories: tuple[str, ...] = ()


def _command_argument_bounds(source: str, command: str) -> tuple[int, int] | None:
    """Return the content bounds of one balanced required command argument."""
    command_start = source.find(f"\\{command}")
    if command_start < 0:
        return None
    opening = command_start + len(command) + 1
    while opening < len(source) and source[opening].isspace():
        opening += 1
    if opening >= len(source) or source[opening] != "{":
        return None
    depth = 0
    position = opening
    while position < len(source):
        character = source[position]
        escaped = position > 0 and source[position - 1] == "\\"
        if character == "{" and not escaped:
            depth += 1
        elif character == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return opening + 1, position
        position += 1
    raise ValueError(f"unterminated \\{command} argument")


def with_canonical_project_link(candidate: ProjectCandidate) -> ProjectCandidate:
    """Link an unlinked project heading to its first configured GitHub repository."""
    if not candidate.git_repositories:
        return candidate
    heading_bounds = _command_argument_bounds(candidate.latex, "resumeProjectHeading")
    if heading_bounds is None:
        return candidate
    heading_start, heading_end = heading_bounds
    heading = candidate.latex[heading_start:heading_end]
    if r"\href{" in heading:
        return candidate

    repository_url = f"https://github.com/{candidate.git_repositories[0]}"
    title_bounds = _command_argument_bounds(heading, "textbf")
    if title_bounds is None:
        linked_heading = rf"\href{{{repository_url}}}{{{heading}}}"
    else:
        title_start, title_end = title_bounds
        textbf_start = heading.rfind(r"\textbf", 0, title_start)
        if textbf_start < 0:
            return candidate
        textbf_end = title_end + 1
        linked_title = rf"\href{{{repository_url}}}{{{heading[textbf_start:textbf_end]}}}"
        linked_heading = heading[:textbf_start] + linked_title + heading[textbf_end:]
    return replace(
        candidate,
        latex=candidate.latex[:heading_start] + linked_heading + candidate.latex[heading_end:],
    )


def limit_project_candidate_bullets(
    candidate: ProjectCandidate, maximum_bullets: int
) -> ProjectCandidate:
    """Keep the strongest leading bullets while preserving exact per-bullet provenance."""
    if maximum_bullets < 1:
        raise ValueError("maximum_bullets must be positive")
    if len(candidate.bullet_evidence_ids) <= maximum_bullets:
        return candidate

    spans: list[tuple[int, int]] = []
    position = 0
    needle = r"\resumeItem"
    while True:
        command_start = candidate.latex.find(needle, position)
        if command_start < 0:
            break
        opening = command_start + len(needle)
        while opening < len(candidate.latex) and candidate.latex[opening].isspace():
            opening += 1
        if opening >= len(candidate.latex) or candidate.latex[opening] != "{":
            position = command_start + len(needle)
            continue
        depth = 0
        closing = opening
        while closing < len(candidate.latex):
            character = candidate.latex[closing]
            escaped = closing > 0 and candidate.latex[closing - 1] == "\\"
            if character == "{" and not escaped:
                depth += 1
            elif character == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    spans.append((command_start, closing + 1))
                    position = closing + 1
                    break
            closing += 1
        else:
            raise ValueError("project candidate contains an unterminated resume bullet")

    if len(spans) != len(candidate.bullet_evidence_ids):
        raise ValueError("project candidate bullet provenance is out of sync with LaTeX")
    removed = spans[maximum_bullets:]
    chunks: list[str] = []
    cursor = 0
    for start, end in removed:
        chunks.append(candidate.latex[cursor:start])
        cursor = end
    chunks.append(candidate.latex[cursor:])
    bullet_evidence_ids = candidate.bullet_evidence_ids[:maximum_bullets]
    evidence_ids = tuple(
        dict.fromkeys(evidence_id for ids in bullet_evidence_ids for evidence_id in ids)
    )
    return replace(
        candidate,
        latex="".join(chunks),
        evidence_ids=evidence_ids,
        bullet_evidence_ids=bullet_evidence_ids,
    )


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        term
        for term in _TOKEN.findall(value.casefold())
        if len(term) > 1
        and term not in _STOP_WORDS
        and any(character.isalpha() for character in term)
    )


def project_quality_issues(candidate: ProjectCandidate) -> tuple[str, ...]:
    """Identify internal research prose that must never become résumé copy."""
    issues: list[str] = []
    for bullet in resume_item_texts(candidate.latex):
        for label, pattern in _INTERNAL_RESEARCH_PATTERNS:
            if pattern.search(bullet) and label not in issues:
                issues.append(label)
    return tuple(issues)


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
    linked_candidates: list[ProjectCandidate] = []
    for candidate in candidates:
        _validate_candidate(candidate, approved_ids)
        if candidate.id in seen_ids:
            raise ValueError(f"project inventory contains duplicate ID: {candidate.id}")
        seen_ids.add(candidate.id)
        linked_candidates.append(with_canonical_project_link(candidate))
    return tuple(linked_candidates)


def project_inventory_entries_from_master(
    master_latex: str, evidence_id: str
) -> list[dict[str, object]]:
    """Project the master resume's existing project blocks into strict inventory entries."""
    section = re.search(r"(?ms)^\\section\{Projects\}\s*$.*?(?=^\\section\{|\Z)", master_latex)
    if section is None:
        return []
    body = section.group(0)
    headings = list(re.finditer(r"(?m)^\\resumeProjectHeading\b", body))
    entries: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        closing = body.find(r"\resumeSubHeadingListEnd", heading.start(), next_heading)
        end = closing if closing >= 0 else next_heading
        latex = body[heading.start() : end].strip() + "\n"
        bullets = latex.count(r"\resumeItem{")
        title_match = re.search(r"\\textbf\{(?P<title>[^{}]+)\}", latex)
        title = title_match.group("title").strip() if title_match else ""
        if not title or not bullets:
            continue
        base_id = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-") or "project"
        candidate_id = base_id
        suffix = 2
        while candidate_id in used_ids:
            candidate_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(candidate_id)
        tags = sorted(
            {
                term
                for term in re.findall(r"[a-z0-9+#.]+", latex_to_text(latex).casefold())
                if len(term) > 1
            }
        )
        entries.append(
            {
                "id": candidate_id,
                "title": title,
                "latex": latex,
                "evidence_ids": [evidence_id],
                "bullet_evidence_ids": [[evidence_id] for _ in range(bullets)],
                "tags": tags or [candidate_id],
            }
        )
    return entries


def sync_project_inventory_from_master(
    path: Path, *, master_latex: str, evidence_id: str
) -> tuple[bool, int, int]:
    """Append missing master projects while preserving every existing catalogue entry."""
    if path.is_symlink():
        raise ValueError("project inventory must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if path.exists():
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"project inventory is not valid JSON: {path}") from error
        if not isinstance(payload, list):
            raise ValueError(f"project inventory must be a JSON array: {path}")
    else:
        payload = []

    master_entries = project_inventory_entries_from_master(master_latex, evidence_id)
    existing_ids = {
        item.get("id", "").casefold()
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    existing_titles = {
        re.sub(r"[^a-z0-9]+", "", item.get("title", "").casefold())
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    }
    additions: list[dict[str, object]] = []
    for item in master_entries:
        normalized_title = re.sub(r"[^a-z0-9]+", "", str(item["title"]).casefold())
        if normalized_title in existing_titles:
            continue
        addition = dict(item)
        base_id = str(addition["id"]).casefold()
        candidate_id = base_id
        suffix = 2
        while candidate_id in existing_ids:
            candidate_id = f"{base_id}-{suffix}"
            suffix += 1
        addition["id"] = candidate_id
        additions.append(addition)
        existing_ids.add(candidate_id)
        existing_titles.add(normalized_title)
    if created or additions:
        updated = [*payload, *additions]
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False
        ) as temporary:
            json.dump(updated, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return created, len(additions), len(payload) + len(additions)


def _candidate_terms(candidate: ProjectCandidate) -> frozenset[str]:
    return _terms(latex_to_text(candidate.latex)) | _terms(" ".join(candidate.tags))


def _role_signal_matches(candidate: ProjectCandidate, job_description: str) -> frozenset[str]:
    candidate_terms = _candidate_terms(candidate)
    job_terms = _terms(job_description)
    return frozenset(
        label
        for label, job_cluster, candidate_cluster in _ROLE_SIGNAL_CLUSTERS
        if job_terms & job_cluster and candidate_terms & candidate_cluster
    )


def _score(candidate: ProjectCandidate, job_description: str) -> int:
    candidate_terms = _candidate_terms(candidate)
    matched_terms = candidate_terms & _terms(job_description)
    required_matches = candidate_terms & _required_terms(job_description)
    role_signals = _role_signal_matches(candidate, job_description)
    return len(matched_terms) + 8 * len(required_matches) + 3 * len(role_signals)


def _contrastive_selection_score(
    candidate: ProjectCandidate,
    *,
    base_score: int,
    maximum_base_score: int,
    profile: ProjectIdentityProfile,
    selected_profiles: list[ProjectIdentityProfile],
    candidate_signals: frozenset[str],
    covered_signals: set[str],
) -> tuple[float, int]:
    """Balance role fit, approved-copy strength, and portfolio differentiation."""
    role_relevance = 40 * base_score / maximum_base_score
    quality = 30 * profile.quality_score / 100
    differentiation = (
        min(
            compare_project_profiles(selected, profile).differentiation_score
            for selected in selected_profiles
        )
        if selected_profiles
        else 100
    )
    diversity = 20 * differentiation / 100
    evidence_strength = min(
        10,
        (5 * len(candidate.bullet_evidence_ids) + 5 * len(set(candidate.evidence_ids))) / 3,
    )
    role_coverage = 2 * len(candidate_signals - covered_signals)
    repeated_role_penalty = len(candidate_signals & covered_signals)
    return (
        round(
            role_relevance
            + quality
            + diversity
            + evidence_strength
            + role_coverage
            - repeated_role_penalty,
            3,
        ),
        differentiation,
    )


def _selection_differentiators(
    profile: ProjectIdentityProfile,
    prior: list[ProjectIdentityProfile],
) -> tuple[tuple[str, ...], int]:
    if not prior:
        values = (*profile.narrative_signals, *profile.metric_categories)
        return tuple(dict.fromkeys(values)), 100
    comparisons = tuple(compare_project_profiles(item, profile) for item in prior)
    shared_narratives = {
        signal for comparison in comparisons for signal in comparison.shared_narrative_signals
    }
    shared_metrics = {
        metric for comparison in comparisons for metric in comparison.shared_metric_categories
    }
    values = (
        *(signal for signal in profile.narrative_signals if signal not in shared_narratives),
        *(metric for metric in profile.metric_categories if metric not in shared_metrics),
    )
    if not values:
        values = tuple(comparisons[-1].differentiating_terms[:4])
    return tuple(dict.fromkeys(values)), min(
        comparison.differentiation_score for comparison in comparisons
    )


def select_projects(
    candidates: Sequence[ProjectCandidate],
    job_description: str,
    *,
    max_projects: int,
    minimum_bullets: int = 1,
    require_resume_quality: bool = True,
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
    if require_resume_quality:
        eligible = tuple(
            candidate for candidate in eligible if not project_quality_issues(candidate)
        )
    remaining = {
        candidate.id: (
            candidate,
            _score(candidate, job_description),
            _role_signal_matches(candidate, job_description),
            build_project_identity_profile(candidate),
        )
        for candidate in eligible
    }
    maximum_base_score = max((item[1] for item in remaining.values()), default=0)
    if maximum_base_score <= 0:
        return ()
    selected: list[ProjectCandidate] = []
    selected_profiles: list[ProjectIdentityProfile] = []
    covered_signals: set[str] = set()
    while remaining and len(selected) < max_projects:
        scored = [
            (
                *_contrastive_selection_score(
                    candidate,
                    base_score=base_score,
                    maximum_base_score=maximum_base_score,
                    profile=profile,
                    selected_profiles=selected_profiles,
                    candidate_signals=signals,
                    covered_signals=covered_signals,
                ),
                candidate,
                base_score,
                signals,
                profile,
            )
            for candidate, base_score, signals, profile in remaining.values()
            if base_score > 0
        ]
        if not scored:
            break
        selection_score, _, candidate, base_score, signals, profile = min(
            scored,
            key=lambda item: (
                -item[0],
                -item[3],
                item[2].id,
            ),
        )
        del remaining[candidate.id]
        if selection_score <= 0 or base_score <= 0:
            break
        selected.append(candidate)
        selected_profiles.append(profile)
        covered_signals.update(signals)
    return tuple(selected)


def select_project_rationales(
    candidates: Sequence[ProjectCandidate],
    job_description: str,
    *,
    max_projects: int,
    minimum_bullets: int = 1,
) -> tuple[ProjectSelection, ...]:
    """Return each selected project with the exact job terms that justified its selection."""
    job_terms = _terms(job_description)
    selected = select_projects(
        candidates,
        job_description,
        max_projects=max_projects,
        minimum_bullets=minimum_bullets,
    )
    profiles = {candidate.id: build_project_identity_profile(candidate) for candidate in selected}
    maximum_base_score = max(
        (_score(candidate, job_description) for candidate in candidates), default=1
    )
    prior_profiles: list[ProjectIdentityProfile] = []
    covered_signals: set[str] = set()
    rationales: list[ProjectSelection] = []
    for candidate in selected:
        profile = profiles[candidate.id]
        signals = _role_signal_matches(candidate, job_description)
        differentiators, differentiation_score = _selection_differentiators(profile, prior_profiles)
        selection_score, _ = _contrastive_selection_score(
            candidate,
            base_score=_score(candidate, job_description),
            maximum_base_score=max(1, maximum_base_score),
            profile=profile,
            selected_profiles=prior_profiles,
            candidate_signals=signals,
            covered_signals=covered_signals,
        )
        rationales.append(
            ProjectSelection(
                id=candidate.id,
                title=candidate.title,
                matched_terms=tuple(sorted(_candidate_terms(candidate) & job_terms)),
                matched_signals=tuple(sorted(signals)),
                narrative_signals=profile.narrative_signals,
                metric_categories=profile.metric_categories,
                differentiators=differentiators,
                evidence_tier=profile.evidence_tier,
                quality_score=profile.quality_score,
                differentiation_score=differentiation_score,
                selection_score=selection_score,
            )
        )
        prior_profiles.append(profile)
        covered_signals.update(signals)
    return tuple(rationales)
