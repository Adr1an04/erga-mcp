from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .git_evidence import validate_worktree

DEFAULT_COMMIT_LIMIT = 200
_DOCUMENTATION_SUFFIXES = (".md", ".rst", ".txt")
_LOCK_FILE_NAMES = {"package-lock.json", "poetry.lock", "pdm.lock", "yarn.lock"}


@dataclass(frozen=True)
class ProjectMetricProposal:
    repo_path: str
    attribution: str
    author_email: str | None
    commit_limit: int
    commit_count: int
    total_commit_count: int
    commit_share_percent: int
    files_changed: int
    source_files_changed: int
    test_files_changed: int
    test_file_share_percent: int
    lines_added: int
    lines_deleted: int
    resume_metric_candidates: tuple[str, ...]
    requires_user_confirmation: bool = True


def propose_git_project_metrics(
    repo: Path, *, author_email: str, commit_limit: int = DEFAULT_COMMIT_LIMIT
) -> ProjectMetricProposal:
    """Propose review-only, attributable résumé metrics from bounded local Git history.

    Git line counts describe tracked changes, not users, performance, coverage, or business impact.
    The caller must keep the returned candidates review-only until the user confirms them.
    """
    if not author_email.strip():
        raise ValueError("author_email is required to make attributable metric proposals")
    if commit_limit < 1:
        raise ValueError("commit_limit must be positive")
    resolved = validate_worktree(repo)
    commits = _commits(resolved, commit_limit)
    total_commit_count = len(commits)
    selected = [
        commit for commit in commits if commit.author_email.casefold() == author_email.casefold()
    ]
    if not selected:
        raise ValueError("no commits matched author_email in the selected Git history")

    files: set[str] = set()
    source_files: set[str] = set()
    test_files: set[str] = set()
    lines_added = lines_deleted = 0
    for commit in selected:
        for added, deleted, path in _numstat(resolved, commit.sha):
            if _is_ignored(path) or _is_documentation(path):
                continue
            files.add(path)
            lines_added += added
            lines_deleted += deleted
            if _is_test(path):
                test_files.add(path)
            else:
                source_files.add(path)

    code_and_test_files = len(source_files | test_files)
    test_file_share_percent = _percent(len(test_files), code_and_test_files)
    commit_share_percent = _percent(len(selected), total_commit_count)
    candidates = (
        "Contributed "
        f"{len(selected)} of {total_commit_count} commits ({commit_share_percent}%) "
        "in the selected Git history.",
        "Changed "
        f"{len(source_files)} source file{'s' if len(source_files) != 1 else ''} and added tests "
        f"in {len(test_files)} test file{'s' if len(test_files) != 1 else ''} "
        f"({test_file_share_percent}% of code/test files touched).",
        "Added "
        f"{lines_added:,} lines of tracked code and tests across {code_and_test_files} files.",
    )
    return ProjectMetricProposal(
        repo_path=str(resolved),
        attribution="author_email",
        author_email=author_email,
        commit_limit=commit_limit,
        commit_count=len(selected),
        total_commit_count=total_commit_count,
        commit_share_percent=commit_share_percent,
        files_changed=len(files),
        source_files_changed=len(source_files),
        test_files_changed=len(test_files),
        test_file_share_percent=test_file_share_percent,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        resume_metric_candidates=candidates,
    )


@dataclass(frozen=True)
class _Commit:
    sha: str
    author_email: str


def _commits(repo: Path, limit: int) -> list[_Commit]:
    result = _run_git(repo, "log", "--no-merges", f"--max-count={limit}", "--format=%H%x1f%ae")
    if result.returncode != 0:
        raise ValueError("could not read local Git history")
    return [
        _Commit(sha, author_email)
        for line in result.stdout.splitlines()
        if line and (sha_author := line.split("\x1f", maxsplit=1)) and len(sha_author) == 2
        for sha, author_email in [sha_author]
    ]


def _numstat(repo: Path, sha: str) -> list[tuple[int, int, str]]:
    result = _run_git(repo, "diff-tree", "--root", "--no-commit-id", "--numstat", "-r", sha)
    if result.returncode != 0:
        raise ValueError(f"could not inspect git diff for commit {sha}")
    values: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        added, deleted, path = line.split("\t", maxsplit=2)
        values.append(
            (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0, path)
        )
    return values


def _is_documentation(path: str) -> bool:
    return path.casefold().endswith(_DOCUMENTATION_SUFFIXES)


def _is_ignored(path: str) -> bool:
    name = Path(path).name.casefold()
    return name in _LOCK_FILE_NAMES


def _is_test(path: str) -> bool:
    value = path.casefold()
    name = Path(value).name
    return name.startswith(("test_", "spec_")) or "/test" in value or "/spec" in value


def _percent(numerator: int, denominator: int) -> int:
    return round(numerator / denominator * 100) if denominator else 0


def _run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=False
    )
