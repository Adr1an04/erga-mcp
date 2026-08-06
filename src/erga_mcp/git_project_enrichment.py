from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .git_evidence import (
    analyze_commits,
    scan_authored_commits,
    synthesize_diff_research,
)
from .github_projects import (
    GitHubProject,
    connected_github_login,
    ensure_github_worktree,
    github_authored_commit_shas,
)
from .models import Evidence, GitResearchBullet
from .project_inventory import ProjectCandidate, select_projects
from .store import ErgaStore

_TOKEN = re.compile(r"[a-z0-9+#.]+")


@dataclass(frozen=True)
class GitProjectEnrichment:
    candidates: tuple[ProjectCandidate, ...]
    evidence: tuple[Evidence, ...]
    reports: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    catalogue_candidate_count: int


def merge_github_project_catalogue(
    curated: tuple[ProjectCandidate, ...], discovered: tuple[GitHubProject, ...]
) -> tuple[ProjectCandidate, ...]:
    """Add lightweight GitHub-index candidates without replacing curated résumé metadata."""
    mapped = {
        repository.casefold() for candidate in curated for repository in candidate.git_repositories
    }
    used_ids = {candidate.id for candidate in curated}
    generated: list[ProjectCandidate] = []
    for project in discovered:
        if project.repository.casefold() in mapped:
            continue
        base_id = re.sub(r"[^a-z0-9]+", "-", project.repository.casefold()).strip("-")
        project_id = f"github-{base_id}"
        suffix = 2
        while project_id in used_ids:
            project_id = f"github-{base_id}-{suffix}"
            suffix += 1
        used_ids.add(project_id)
        title = re.sub(r"[-_]+", " ", project.name).strip().title()
        summary = project.description.strip() or f"GitHub repository {project.repository}"
        technology = f" $|$ \\textit{{{_latex_text(project.language)}}}" if project.language else ""
        tags = {
            *project.topics,
            *(
                token
                for token in _TOKEN.findall(
                    f"{project.name} {project.description} {project.language}".casefold()
                )
                if len(token) > 1
            ),
        }
        generated.append(
            ProjectCandidate(
                id=project_id,
                title=title,
                latex="\n".join(
                    [
                        rf"\resumeProjectHeading{{\textbf{{{_latex_text(title)}}}{technology}}}{{}}",
                        r"\resumeItemListStart",
                        rf"\resumeItem{{{_latex_text(summary)}}}",
                        r"\resumeItemListEnd",
                    ]
                ),
                evidence_ids=(),
                bullet_evidence_ids=(),
                tags=tuple(sorted(tags)) or ("github",),
                git_repositories=(project.repository,),
            )
        )
    return (*curated, *generated)


def _latex_text(value: str) -> str:
    normalized = " ".join(value.replace("\\", " ").split())
    normalized = normalized.replace("{", "(").replace("}", ")")
    normalized = normalized.replace("~", "-").replace("^", "")
    for character in ("&", "%", "$", "#", "_"):
        normalized = normalized.replace(character, f"\\{character}")
    return normalized


def _bullet_score(bullet: GitResearchBullet, job_description: str) -> tuple[int, float, str]:
    job_terms = set(_TOKEN.findall(job_description.casefold()))
    bullet_terms = set(_TOKEN.findall(bullet.text.casefold()))
    return len(job_terms & bullet_terms), bullet.confidence, bullet.text.casefold()


def _evidence_source_ref(project_id: str, bullet: GitResearchBullet) -> str:
    digest = hashlib.sha256(
        "\n".join(
            [
                bullet.text,
                *sorted(bullet.source_commit_shas),
                *sorted(bullet.diff_hashes),
            ]
        ).encode("utf-8")
    ).hexdigest()[:20]
    anchor = sorted(bullet.source_commit_shas)[0][:12]
    return f"git-derived:{project_id}@{anchor}:{digest}"


def _approved_bullet_evidence(
    *, store: ErgaStore, project_id: str, bullet: GitResearchBullet
) -> Evidence:
    source_ref = _evidence_source_ref(project_id, bullet)
    existing = next(
        (item for item in store.list_evidence() if item.source_ref == source_ref),
        None,
    )
    if existing is not None:
        if not existing.approved or existing.text != bullet.text:
            raise ValueError("stored Git-derived evidence does not match its provenance hash")
        return existing
    return store.add_evidence(source_ref=source_ref, text=bullet.text, approved=True)


def enrich_ranked_projects_from_git(
    *,
    candidates: tuple[ProjectCandidate, ...],
    job_description: str,
    project_count: int,
    bullets_per_project: int,
    bullet_min_characters: int,
    bullet_target_characters: int,
    bullet_max_characters: int,
    store: ErgaStore,
    cache_root: Path,
) -> GitProjectEnrichment:
    """Rank approved JSON projects and collect Git provenance without rewriting résumé copy."""
    resume_candidates = tuple(
        candidate
        for candidate in candidates
        if candidate.evidence_ids and len(candidate.bullet_evidence_ids) >= bullets_per_project
    )
    ranked = select_projects(
        resume_candidates,
        job_description,
        max_projects=max(1, len(resume_candidates)),
        minimum_bullets=bullets_per_project,
    )
    if not ranked:
        return GitProjectEnrichment(resume_candidates, (), (), (), len(resume_candidates))

    login: str | None = None
    local_candidates = [
        Path(draft.repo_path)
        for draft in store.list_git_research_drafts()
        if draft.source == "git" and Path(draft.repo_path).is_dir()
    ]
    researched = 0
    derived_evidence: list[Evidence] = []
    reports: list[dict[str, object]] = []
    warnings: list[str] = []

    for candidate in ranked:
        if researched >= project_count:
            break
        researched += 1
        if not candidate.git_repositories:
            warnings.append(
                f"{candidate.title} has no git_repositories mapping; retained approved "
                "catalogue bullets."
            )
            continue
        project_bullets: list[GitResearchBullet] = []
        project_commits: set[str] = set()
        project_files: set[str] = set()
        repository_reports: list[dict[str, object]] = []
        try:
            if login is None:
                login = connected_github_login()
            for repository in candidate.git_repositories:
                worktree = ensure_github_worktree(
                    repository,
                    cache_root=cache_root,
                    local_candidates=local_candidates,
                )
                authored_shas = github_authored_commit_shas(repository, login=login)
                commits = scan_authored_commits(worktree, authored_shas)
                observations = analyze_commits(worktree, commits)
                for observation in observations:
                    store.save_git_change_observation(observation)
                summary, bullets = synthesize_diff_research(
                    str(worktree),
                    observations,
                    store.list_git_candidates(repo_path=str(worktree)),
                    minimum_characters=bullet_min_characters,
                    target_characters=bullet_target_characters,
                    maximum_characters=bullet_max_characters,
                )
                store.save_git_research_draft(
                    repo_path=str(worktree),
                    summary=summary,
                    bullet_candidates=bullets,
                    generated_from_git_diffs=True,
                )
                project_bullets.extend(bullets)
                project_commits.update(item.commit_sha for item in observations)
                project_files.update(path for item in observations for path in item.files)
                repository_reports.append(
                    {
                        "repository": repository,
                        "authored_commits": len(commits),
                        "observed_commits": len(observations),
                    }
                )
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
            warnings.append(
                f"Git research for {candidate.title} was unavailable; retained approved "
                f"catalogue bullets: {error}"
            )
            continue

        ranked_bullets = sorted(
            project_bullets,
            key=lambda bullet: (
                -_bullet_score(bullet, job_description)[0],
                -_bullet_score(bullet, job_description)[1],
                _bullet_score(bullet, job_description)[2],
            ),
        )[:bullets_per_project]
        if not ranked_bullets:
            warnings.append(
                f"Git research found no attributable code changes for {candidate.title}; "
                "retained approved catalogue bullets."
            )
            continue
        bullet_evidence = [
            _approved_bullet_evidence(store=store, project_id=candidate.id, bullet=bullet)
            for bullet in ranked_bullets
        ]
        derived_evidence.extend(bullet_evidence)
        reports.append(
            {
                "project_id": candidate.id,
                "title": candidate.title,
                "repositories": repository_reports,
                "authored_commits": len(project_commits),
                "files": len(project_files),
                "generated_bullets": len(ranked_bullets),
                "resume_bullets_source": "approved_catalogue",
                "evidence_ids": [item.id for item in bullet_evidence],
            }
        )

    return GitProjectEnrichment(
        candidates=resume_candidates,
        evidence=tuple(derived_evidence),
        reports=tuple(reports),
        warnings=tuple(warnings),
        catalogue_candidate_count=len(resume_candidates),
    )
