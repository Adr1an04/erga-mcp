from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
