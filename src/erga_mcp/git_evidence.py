from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import GitEvidenceCandidate, GitResearchBullet

DEFAULT_COMMIT_LIMIT = 200
_LOW_SIGNAL_SUFFIXES = (".lock", ".md", ".rst", ".txt")
_LOW_SIGNAL_NAMES = {"package-lock.json", "poetry.lock", "pdm.lock", "yarn.lock"}


@dataclass(frozen=True)
class GitCommit:
    sha: str
    parents: tuple[str, ...]
    subject: str
    files: tuple[str, ...]


def validate_worktree(repo: Path) -> Path:
    resolved = repo.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("repository path must be an existing local git worktree")
    result = _run_git(resolved, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError("repository path must be an existing local git worktree")
    return resolved


def discover_worktrees(roots: list[Path]) -> list[Path]:
    """Find distinct local Git worktrees below explicit roots, skipping dependency metadata."""
    found: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"git scan root must be an existing directory: {resolved}")
        for directory, names, _ in os.walk(resolved):
            names[:] = [
                name for name in names if name not in {".git", ".venv", "node_modules", "vendor"}
            ]
            candidate = Path(directory)
            if (candidate / ".git").exists():
                found.add(validate_worktree(candidate))
    return sorted(found)


def scan_commits(repo: Path, checkpoint: str | None) -> tuple[list[GitCommit], str | None]:
    resolved = validate_worktree(repo)
    head = _run_git(resolved, "rev-parse", "HEAD")
    if head.returncode != 0:
        return [], None
    head_sha = head.stdout.strip()
    revision = f"{checkpoint}..HEAD" if checkpoint else "HEAD"
    arguments = ["log", "--format=%H%x1f%P%x1f%s%x1e", "--no-merges"]
    if checkpoint is None:
        arguments.extend([f"--max-count={DEFAULT_COMMIT_LIMIT}"])
    arguments.append(revision)
    result = _run_git(resolved, *arguments)
    if result.returncode != 0:
        raise ValueError("saved git checkpoint is not reachable from this worktree")
    commits = [
        _parse_commit(resolved, item) for item in result.stdout.split("\x1e") if item.strip()
    ]
    return [commit for commit in commits if _is_high_signal(commit)], head_sha


def _parse_commit(repo: Path, record: str) -> GitCommit:
    sha, parents, subject = record.strip().split("\x1f", maxsplit=2)
    files = _run_git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
    if files.returncode != 0:
        raise ValueError(f"could not inspect git commit {sha}")
    return GitCommit(
        sha=sha,
        parents=tuple(parent for parent in parents.split() if parent),
        subject=subject.strip(),
        files=tuple(path for path in files.stdout.splitlines() if path),
    )


def _is_high_signal(commit: GitCommit) -> bool:
    if len(commit.parents) > 1 or not commit.files or len(commit.subject) < 12:
        return False
    return any(
        Path(path).name not in _LOW_SIGNAL_NAMES
        and not path.casefold().endswith(_LOW_SIGNAL_SUFFIXES)
        for path in commit.files
    )


def synthesize_project_research(
    repo_path: str, candidates: list[GitEvidenceCandidate]
) -> tuple[str, list[GitResearchBullet]]:
    """Create a deterministic, local-only review draft from saved commit metadata."""
    groups: list[list[tuple[GitEvidenceCandidate, str]]] = []
    for candidate in candidates:
        subject = _candidate_subject(candidate)
        if not subject or _is_obvious_chore(subject):
            continue
        for group in groups:
            if _subjects_are_similar(subject, group[0][1]):
                group.append((candidate, subject))
                if len(_subject_words(subject)) > len(_subject_words(group[0][1])):
                    group.insert(0, group.pop())
                break
        else:
            groups.append([(candidate, subject)])

    bullets = sorted(
        (
            GitResearchBullet(
                text=group[0][1],
                source_candidate_ids=[candidate.id for candidate, _ in group],
                source_commit_shas=[candidate.commit_sha for candidate, _ in group],
            )
            for group in groups
        ),
        key=lambda bullet: (-_subject_priority(bullet.text), bullet.text.casefold()),
    )[:4]
    summary = (
        "Generated locally from Git commit metadata; factual draft only, needs review before "
        f"approval or resume use. Repository: {repo_path}."
    )
    return summary, bullets


def _candidate_subject(candidate: GitEvidenceCandidate) -> str:
    prefix = "Git commit: "
    first_line = candidate.text.splitlines()[0] if candidate.text else ""
    return first_line.removeprefix(prefix).strip()


def _is_obvious_chore(subject: str) -> bool:
    raw = subject.casefold().strip()
    if raw.startswith(("chore", "ci:", "build:", "test:", "docs:")):
        return True
    normalized = re.sub(r"^[a-z]+(?:\([^)]*\))?:\s*", "", raw)
    return bool(
        re.match(
            r"(?:release\b|prepare release\b|bump version\b|"
            r"(?:update|configure) (?:ci|workflow|build|dependencies|deps|lockfile)\b|"
            r"(?:ci|build|test|format|formatting|lint|style|prettier|black|isort|ruff)\b)",
            normalized,
        )
    )


def _subject_priority(subject: str) -> int:
    normalized = subject.casefold().strip()
    if re.match(
        r"(?:feat|feature|implement|add|build|create|design|architect|engineer):?\b", normalized
    ):
        return 2
    if re.match(r"(?:fix|optimiz|improv|harden|refactor):?\b", normalized):
        return 1
    return 0


def _subjects_are_similar(left: str, right: str) -> bool:
    left_words = _subject_words(left)
    right_words = _subject_words(right)
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words)
    return overlap >= 2 and overlap / len(left_words | right_words) >= 0.5


def _subject_words(subject: str) -> set[str]:
    ignored = {"add", "implement", "for", "the", "a", "an", "to", "with", "and", "of"}
    return {
        word
        for word in re.findall(r"[a-z0-9]+", subject.casefold())
        if len(word) > 1 and word not in ignored
    }


def _run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
