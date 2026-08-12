from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from erga_mcp.models import Evidence, GitEvidenceCandidate
from erga_mcp.portfolio.skill_inventory import normalize_skill_name
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.cards import CardAction, CardField, CardView

GitSkillStatus = Literal["seeded_and_confirmed", "self_reported_unconfirmed", "git_discovered"]


@dataclass(frozen=True)
class GitSkillGroup:
    skill: str
    normalized_skill: str
    source_status: GitSkillStatus
    checked: bool
    repositories: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    confidence: float
    eligible_for_approval: bool
    approved: bool
    provenance: tuple[str, ...]


# Deliberately small, reviewed vocabulary. Matching this table is discovery, not proof by itself.
_SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "AWS": ("aws", "amazon web services"),
    "C#": ("c#", "c sharp"),
    "C++": ("c++", "cpp"),
    "CUDA": ("cuda",),
    "Docker": ("docker",),
    "FastAPI": ("fastapi",),
    "Flask": ("flask",),
    "GCP": ("gcp", "google cloud platform"),
    "GitHub Actions": ("github actions",),
    "Go": ("golang", "go"),
    "Java": ("java",),
    "JavaScript": ("javascript",),
    "Kubernetes": ("kubernetes", "k8s"),
    "Next.js": ("next.js", "nextjs"),
    "Node.js": ("node.js", "nodejs"),
    "PostgreSQL": ("postgresql", "postgres"),
    "Prisma": ("prisma",),
    "PyTorch": ("pytorch",),
    "Python": ("python",),
    "React": ("react", "react.js", "reactjs"),
    "Redis": ("redis",),
    "Rust": ("rust",),
    "Spring Boot": ("spring boot",),
    "SQL": ("sql",),
    "TensorFlow": ("tensorflow",),
    "Terraform": ("terraform",),
    "TypeScript": ("typescript",),
    "Vue": ("vue", "vue.js", "vuejs"),
}


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", re.IGNORECASE)


def _canonical_seed(skill: str) -> tuple[str, str]:
    normalized = normalize_skill_name(skill)
    for canonical, aliases in _SKILL_ALIASES.items():
        if normalized in {alias.casefold() for alias in aliases}:
            return canonical, normalize_skill_name(canonical)
    return skill, normalized


def _candidate_skills(candidate: GitEvidenceCandidate) -> set[str]:
    matches: set[str] = set()
    for canonical, aliases in _SKILL_ALIASES.items():
        if any(_alias_pattern(alias).search(candidate.text) for alias in aliases):
            matches.add(canonical)
    return matches


def explicit_skills_in_texts(texts: Iterable[str]) -> tuple[str, ...]:
    """Extract only exact audited skill names that are explicitly present in approved text."""
    combined = "\n".join(texts)
    matches: list[str] = []
    for canonical, aliases in _SKILL_ALIASES.items():
        if canonical == "Go":
            # Do not turn the ordinary English verb "go" into a programming-language claim.
            found = re.search(r"(?<![A-Za-z0-9])(?:Go|Golang)(?![A-Za-z0-9])", combined) is not None
        else:
            found = any(_alias_pattern(alias).search(combined) is not None for alias in aliases)
        if found:
            matches.append(canonical)
    return tuple(matches)


def reconcile_git_skill_groups(
    store: ErgaStore,
    *,
    seed_override: tuple[str, ...] = (),
    include_skipped: bool = False,
) -> list[GitSkillGroup]:
    """Join self-reported hints to exact audited Git terms without creating evidence."""
    seed_records = store.list_skill_seeds()
    ordered_seeds: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    for record in seed_records:
        display, normalized = _canonical_seed(record.skill)
        if normalized not in seen:
            ordered_seeds.append((display, normalized, record.checked))
            seen.add(normalized)
    for skill in seed_override:
        display, normalized = _canonical_seed(skill)
        if normalized not in seen:
            ordered_seeds.append((display, normalized, False))
            seen.add(normalized)

    candidates_by_skill: dict[str, list[GitEvidenceCandidate]] = {}
    display_by_key: dict[str, str] = {}
    for candidate in store.list_git_candidates():
        for display in _candidate_skills(candidate):
            key = normalize_skill_name(display)
            candidates_by_skill.setdefault(key, []).append(candidate)
            display_by_key[key] = display

    groups: list[GitSkillGroup] = []
    seeded_keys = {normalized for _, normalized, _ in ordered_seeds}
    for display, key, checked in ordered_seeds:
        matches = candidates_by_skill.get(key, [])
        groups.append(_group(display, key, checked, matches, seeded=True))
    for key in sorted(candidates_by_skill.keys() - seeded_keys):
        groups.append(
            _group(display_by_key[key], key, False, candidates_by_skill[key], seeded=False)
        )
    if include_skipped:
        return groups
    skipped = store.skipped_git_skill_groups()
    return [group for group in groups if group.normalized_skill not in skipped]


def _group(
    display: str,
    key: str,
    checked: bool,
    candidates: list[GitEvidenceCandidate],
    *,
    seeded: bool,
) -> GitSkillGroup:
    repositories = tuple(sorted({candidate.repo_path for candidate in candidates}))
    status: GitSkillStatus
    if seeded and candidates:
        status = "seeded_and_confirmed"
    elif seeded:
        status = "self_reported_unconfirmed"
    else:
        status = "git_discovered"
    return GitSkillGroup(
        skill=display,
        normalized_skill=key,
        source_status=status,
        checked=checked,
        repositories=repositories,
        candidate_ids=tuple(candidate.id for candidate in candidates),
        confidence=0.9 if candidates else 0.0,
        eligible_for_approval=any(not candidate.approved for candidate in candidates),
        approved=any(candidate.approved for candidate in candidates),
        provenance=tuple(
            f"{Path(candidate.repo_path).name}@{candidate.commit_sha[:8]}"
            for candidate in candidates
        ),
    )


def approve_git_skill_group(store: ErgaStore, normalized_skill: str) -> list[Evidence]:
    """Explicitly approve the Git candidates in one corroborated skill group."""
    key = _canonical_seed(normalized_skill)[1]
    group = next(
        (item for item in reconcile_git_skill_groups(store) if item.normalized_skill == key),
        None,
    )
    if group is None or not group.candidate_ids:
        raise ValueError("skill group is not corroborated by Git candidates")
    candidates = {candidate.id: candidate for candidate in store.list_git_candidates()}
    return [
        store.approve_git_candidate(candidate_id)
        for candidate_id in group.candidate_ids
        if not candidates[candidate_id].approved
    ]


def build_git_skill_review_card(
    store: ErgaStore,
    *,
    page: int = 1,
    page_size: int = 5,
    source_filter: str | None = None,
    seed_override: tuple[str, ...] = (),
    primary_action_id: str = "git.scan",
    primary_action_label: str = "Scan Git projects",
    primary_action_instruction: str = (
        "Scan every repository below the configured onboarding roots, then refresh this review."
    ),
) -> CardView:
    if page_size < 1 or page_size > 10:
        raise ValueError("page_size must be between 1 and 10")
    groups = reconcile_git_skill_groups(store, seed_override=seed_override)
    if source_filter is not None:
        allowed = {
            "seeded_and_confirmed",
            "self_reported_unconfirmed",
            "git_discovered",
        }
        if source_filter not in allowed:
            raise ValueError("source_filter is not supported")
        groups = [group for group in groups if group.source_status == source_filter]
    page_count = max(1, math.ceil(len(groups) / page_size))
    if page < 1 or page > page_count:
        raise ValueError(f"page must be between 1 and {page_count}")
    visible = groups[(page - 1) * page_size : page * page_size]
    fields = tuple(
        CardField(
            f"{group.skill} - {group.source_status}",
            _group_description(group),
        )
        for group in visible
    )
    actions: list[CardAction] = [
        CardAction(
            primary_action_id,
            primary_action_label,
            primary_action_instruction,
            style="primary",
        )
    ]
    actions.append(
        CardAction(
            "project.catalogue.open",
            "Project catalogue",
            "Browse every approved-inventory and GitHub-discovered project.",
        )
    )
    for group in visible:
        actions.append(
            CardAction(
                f"git.group.inspect:{group.normalized_skill}",
                f"Inspect {group.skill}",
                "Show repository and candidate provenance.",
            )
        )
        actions.append(
            CardAction(
                f"git.group.skip:{group.normalized_skill}",
                f"Skip {group.skill}",
                "Hide this group from the default review queue without approving it.",
            )
        )
        if group.eligible_for_approval:
            actions.append(
                CardAction(
                    f"git.group.approve:{group.normalized_skill}",
                    f"Approve {group.skill}",
                    "Explicitly approve corroborated Git candidates.",
                    style="primary",
                )
            )
    if page > 1:
        actions.append(CardAction("git.review.previous", "Previous", "Show the prior page."))
    if page < page_count:
        actions.append(CardAction("git.review.next", "Next", "Show the next page."))
    return CardView(
        title="Git skill review",
        summary=(
            f"{len(groups)} review groups. Self-reported skills remain unapproved until "
            "Git-corroborated and explicitly approved."
        ),
        fields=fields or (CardField("Results", "No skill groups match this view."),),
        actions=tuple(actions),
        page=page,
        page_count=page_count,
    )


def _group_description(group: GitSkillGroup) -> str:
    repositories = len(group.repositories)
    proof = ", ".join(group.provenance[:3]) or "No Git provenance"
    return (
        f"Repositories: {repositories} | Confidence: {group.confidence:.0%} | "
        f"Checked: {'yes' if group.checked else 'no'} | Approved: "
        f"{'yes' if group.approved else 'no'}\nProvenance: {proof}"
    )
