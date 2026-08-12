from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from erga_mcp.config import ErgaConfig
from erga_mcp.models import Evidence
from erga_mcp.portfolio.enrichment import merge_github_project_catalogue
from erga_mcp.portfolio.git_evidence import discover_worktrees
from erga_mcp.portfolio.github import GitHubProject, index_local_github_worktrees
from erga_mcp.portfolio.inventory import (
    ProjectCandidate,
    load_project_inventory,
    project_quality_issues,
)
from erga_mcp.resumes.quality import build_project_identity_profile
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.cards import CardAction, CardField, CardView

_GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ProjectCatalogueEntry:
    id: str
    title: str
    repository: str | None
    repository_url: str | None
    repositories: tuple[str, ...]
    repository_urls: tuple[str, ...]
    technologies: tuple[str, ...]
    last_activity: str | None
    local_clone: bool
    approved_evidence_count: int
    supported_bullet_count: int
    bullet_quality_score: int
    evidence_tier: str
    metric_categories: tuple[str, ...]
    narrative_signals: tuple[str, ...]
    resume_eligibility: str
    source: str
    quality_issues: tuple[str, ...]


@dataclass(frozen=True)
class ProjectCataloguePage:
    entries: tuple[ProjectCatalogueEntry, ...]
    total_entries: int
    page: int
    page_count: int
    page_size: int
    query: str
    eligible_entries: int
    needs_evidence_entries: int
    needs_review_entries: int
    warnings: tuple[str, ...]
    card: CardView

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["card"] = self.card.as_dict()
        return payload


def load_cached_github_projects(cache_path: Path) -> tuple[GitHubProject, ...]:
    """Read the private discovery cache without contacting GitHub or accepting bad slugs."""
    if not cache_path.is_file():
        return ()
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("GitHub project cache is unreadable") from error
    if not isinstance(payload, list):
        raise ValueError("GitHub project cache must be a list")
    projects: list[GitHubProject] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        repository = item.get("repository")
        name = item.get("name")
        if (
            not isinstance(repository, str)
            or _GITHUB_REPOSITORY.fullmatch(repository) is None
            or not isinstance(name, str)
            or not name.strip()
        ):
            continue
        description = item.get("description")
        language = item.get("language")
        topics = item.get("topics")
        projects.append(
            GitHubProject(
                repository=repository,
                name=name.strip(),
                description=description if isinstance(description, str) else "",
                language=language if isinstance(language, str) else "",
                topics=(
                    tuple(value for value in topics if isinstance(value, str) and value.strip())
                    if isinstance(topics, list)
                    else ()
                ),
            )
        )
    return tuple(sorted(projects, key=lambda item: item.repository.casefold()))


def build_project_catalogue(
    config: ErgaConfig,
    store: ErgaStore,
    *,
    page: int = 1,
    page_size: int = 6,
    query: str = "",
) -> ProjectCataloguePage:
    if page_size < 1 or page_size > 10:
        raise ValueError("page_size must be between 1 and 10")
    evidence = store.list_evidence()
    candidates, warnings = _catalogue_candidates(config, evidence)
    local_worktrees = _known_local_worktrees(config, store, warnings)
    local_worktrees_by_repository = index_local_github_worktrees(local_worktrees)
    entries = tuple(
        _catalogue_entry(candidate, evidence, local_worktrees_by_repository)
        for candidate in candidates
    )
    normalized_query = " ".join(query.casefold().split())
    filtered = tuple(
        entry for entry in entries if not normalized_query or _matches(entry, normalized_query)
    )
    page_count = max(1, math.ceil(len(filtered) / page_size))
    if page < 1 or page > page_count:
        raise ValueError(f"page must be between 1 and {page_count}")
    visible = filtered[(page - 1) * page_size : page * page_size]
    eligible = sum(entry.resume_eligibility == "Eligible" for entry in filtered)
    needs_evidence = sum(
        entry.resume_eligibility == "Needs approved evidence" for entry in filtered
    )
    needs_review = len(filtered) - eligible - needs_evidence
    card = _catalogue_card(
        visible=visible,
        total=len(filtered),
        all_total=len(entries),
        eligible=eligible,
        needs_evidence=needs_evidence,
        needs_review=needs_review,
        page=page,
        page_count=page_count,
        query=query.strip(),
        warnings=warnings,
    )
    return ProjectCataloguePage(
        entries=visible,
        total_entries=len(filtered),
        page=page,
        page_count=page_count,
        page_size=page_size,
        query=query.strip(),
        eligible_entries=eligible,
        needs_evidence_entries=needs_evidence,
        needs_review_entries=needs_review,
        warnings=tuple(warnings),
        card=card,
    )


def _catalogue_candidates(
    config: ErgaConfig, evidence: list[Evidence]
) -> tuple[tuple[ProjectCandidate, ...], list[str]]:
    warnings: list[str] = []
    curated: tuple[ProjectCandidate, ...] = ()
    inventory_path = config.resume.project_inventory_path
    if inventory_path is None:
        warnings.append("No approved project inventory is configured.")
    else:
        try:
            curated = load_project_inventory(inventory_path, evidence)
        except (FileNotFoundError, OSError, ValueError) as error:
            warnings.append(f"Approved project inventory unavailable: {error}")
    cache_path = config.data_dir / "github-project-catalogue.json"
    try:
        discovered = load_cached_github_projects(cache_path)
    except ValueError as error:
        warnings.append(str(error))
        discovered = ()
    if not discovered:
        warnings.append(
            "GitHub discovery cache is empty; refresh the catalogue to find more projects."
        )
    return merge_github_project_catalogue(curated, discovered), warnings


def _known_local_worktrees(config: ErgaConfig, store: ErgaStore, warnings: list[str]) -> list[Path]:
    worktrees: list[Path] = []
    if config.portfolio_roots:
        try:
            worktrees.extend(discover_worktrees(list(config.portfolio_roots)))
        except ValueError as error:
            warnings.append(f"Configured Git roots could not be read: {error}")
    for draft in store.list_git_research_drafts():
        candidate = Path(draft.repo_path)
        if draft.source == "git" and candidate.is_dir() and candidate not in worktrees:
            worktrees.append(candidate)
    return worktrees


def _catalogue_entry(
    candidate: ProjectCandidate,
    evidence: list[Evidence],
    local_worktrees: dict[str, Path],
) -> ProjectCatalogueEntry:
    approved_ids = {item.id for item in evidence if item.approved}
    approved_count = len(set(candidate.evidence_ids) & approved_ids)
    issues = project_quality_issues(candidate) if candidate.bullet_evidence_ids else ()
    if not candidate.evidence_ids or not candidate.bullet_evidence_ids:
        eligibility = "Needs approved evidence"
    elif issues:
        eligibility = "Needs copy review"
    elif approved_count != len(set(candidate.evidence_ids)):
        eligibility = "Needs evidence review"
    else:
        eligibility = "Eligible"
    repositories = tuple(candidate.git_repositories)
    repository = repositories[0] if repositories else None
    matched_worktrees = tuple(
        local_worktrees[item.casefold()]
        for item in repositories
        if item.casefold() in local_worktrees
    )
    activities = tuple(
        activity
        for activity in (_last_activity(worktree) for worktree in matched_worktrees)
        if activity is not None
    )
    identity = build_project_identity_profile(candidate)
    return ProjectCatalogueEntry(
        id=candidate.id,
        title=candidate.title,
        repository=repository,
        repository_url=f"https://github.com/{repository}" if repository else None,
        repositories=repositories,
        repository_urls=tuple(f"https://github.com/{item}" for item in repositories),
        technologies=tuple(dict.fromkeys(candidate.tags))[:10],
        last_activity=max(activities) if activities else None,
        local_clone=bool(matched_worktrees),
        approved_evidence_count=approved_count,
        supported_bullet_count=len(candidate.bullet_evidence_ids),
        bullet_quality_score=identity.quality_score,
        evidence_tier=identity.evidence_tier,
        metric_categories=identity.metric_categories,
        narrative_signals=identity.narrative_signals,
        resume_eligibility=eligibility,
        source="Approved inventory" if candidate.evidence_ids else "GitHub discovery",
        quality_issues=issues,
    )


def _last_activity(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cI"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        return None


def _matches(entry: ProjectCatalogueEntry, query: str) -> bool:
    haystack = " ".join(
        (
            entry.id,
            entry.title,
            entry.repository or "",
            " ".join(entry.technologies),
            entry.resume_eligibility,
            entry.source,
        )
    ).casefold()
    return all(term in haystack for term in query.split())


def _catalogue_card(
    *,
    visible: tuple[ProjectCatalogueEntry, ...],
    total: int,
    all_total: int,
    eligible: int,
    needs_evidence: int,
    needs_review: int,
    page: int,
    page_count: int,
    query: str,
    warnings: list[str],
) -> CardView:
    fields = tuple(CardField(entry.title, _entry_text(entry)) for entry in visible) or (
        CardField("Results", "No projects match this search."),
    )
    actions: list[CardAction] = [
        CardAction(
            "project.catalogue.refresh",
            "Refresh GitHub projects",
            "Refresh the private GitHub discovery cache, then reopen this catalogue.",
        ),
        CardAction(
            "git.scan",
            "Scan local Git projects",
            "Collect review-only local Git evidence from configured roots.",
        ),
    ]
    if page > 1:
        actions.append(CardAction("project.catalogue.previous", "Previous", "Show prior projects."))
    if page < page_count:
        actions.append(CardAction("project.catalogue.next", "Next", "Show more projects."))
    shown = f"{total} matching" if query else f"{all_total} total"
    warning_summary = f" {len(warnings)} source warning(s)." if warnings else ""
    review_summary = f", and {needs_review} needing copy/evidence review" if needs_review else ""
    return CardView(
        title="Project catalogue",
        summary=(
            f"{shown}: {eligible} résumé-eligible, {needs_evidence} awaiting approved evidence"
            f"{review_summary}."
            f"{warning_summary}"
        ),
        fields=fields,
        actions=tuple(actions),
        page=page,
        page_count=page_count,
    )


def _entry_text(entry: ProjectCatalogueEntry) -> str:
    repositories = (
        ", ".join(
            f"[{repository}]({repository_url})"
            for repository, repository_url in zip(
                entry.repositories, entry.repository_urls, strict=True
            )
        )
        or "No repository linked"
    )
    technologies = ", ".join(entry.technologies) or "Not recorded"
    activity = entry.last_activity or (
        "Local clone found; no commits" if entry.local_clone else "Not scanned locally"
    )
    quality = (
        f"{entry.bullet_quality_score}/100 · Evidence tier {entry.evidence_tier}"
        if entry.approved_evidence_count
        else "Awaiting approved evidence"
    )
    metrics = ", ".join(entry.metric_categories) or "No supported metric category yet"
    return "\n".join(
        (
            f"Repository: {repositories}",
            f"Technologies/tags: {technologies}",
            f"Git activity: {activity}",
            f"Evidence: {entry.approved_evidence_count} approved · "
            f"{entry.supported_bullet_count} bullets · Quality: {quality}",
            f"Résumé: {entry.resume_eligibility} · Metrics: {metrics} · Source: {entry.source}",
        )
    )
