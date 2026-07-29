from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .models import GitChangeObservation, GitEvidenceCandidate, GitResearchBullet

DEFAULT_COMMIT_LIMIT = 200
MAX_DIFF_CHARACTERS = 40_000
_LOW_SIGNAL_SUFFIXES = (".lock", ".md", ".rst", ".txt")
_LOW_SIGNAL_NAMES = {"package-lock.json", "poetry.lock", "pdm.lock", "yarn.lock"}
_SYMBOL_PATTERNS = (
    re.compile(r"^\+\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.MULTILINE),
    re.compile(r"^\+\s*(?:class|function)\s+([A-Za-z_$]\w*)", re.MULTILINE),
    re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[A-Za-z0-9_./{}-]+)"),
)


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


def commits_missing_observations(
    repo: Path,
    candidates: list[GitEvidenceCandidate],
    observed_shas: set[str],
) -> list[GitCommit]:
    """Recover commits queued by an earlier metadata scan for diff analysis."""
    commits: list[GitCommit] = []
    for candidate in candidates:
        if candidate.commit_sha in observed_shas:
            continue
        record = _run_git(repo, "show", "-s", "--format=%H%x1f%P%x1f%s", candidate.commit_sha)
        if record.returncode != 0:
            raise ValueError(f"could not inspect queued git commit {candidate.commit_sha}")
        commits.append(_parse_commit(repo, record.stdout))
    return commits


def analyze_commits(repo: Path, commits: list[GitCommit]) -> list[GitChangeObservation]:
    """Analyze independent bounded local diffs concurrently without network or model calls."""
    if not commits:
        return []
    workers = min(4, len(commits))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="erga-git") as pool:
        observations = list(pool.map(lambda commit: _observe_if_available(repo, commit), commits))
    return [observation for observation in observations if observation is not None]


def _observe_if_available(repo: Path, commit: GitCommit) -> GitChangeObservation | None:
    try:
        return observe_commit(repo, commit)
    except ValueError:
        return None


def observe_commit(repo: Path, commit: GitCommit) -> GitChangeObservation:
    numstat = _run_git(repo, "diff-tree", "--root", "--no-commit-id", "--numstat", "-r", commit.sha)
    patch = _run_git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--no-ext-diff",
        "--unified=0",
        "-r",
        commit.sha,
    )
    if numstat.returncode != 0 or patch.returncode != 0:
        raise ValueError(f"could not inspect git diff for commit {commit.sha}")
    additions, deletions = _numstat_totals(numstat.stdout)
    bounded_diff = patch.stdout[:MAX_DIFF_CHARACTERS]
    return GitChangeObservation(
        repo_path=str(repo),
        commit_sha=commit.sha,
        files=list(commit.files),
        additions=additions,
        deletions=deletions,
        symbols=_extract_symbols(bounded_diff),
        change_kinds=_classify_change(commit.files, bounded_diff),
        diff_hash=hashlib.sha256(bounded_diff.encode("utf-8", errors="replace")).hexdigest(),
    )


def synthesize_diff_research(
    repo_path: str,
    observations: list[GitChangeObservation],
    candidates: list[GitEvidenceCandidate],
) -> tuple[str, list[GitResearchBullet]]:
    """Create factual, review-only workstreams from stored diff observations, never subjects."""
    candidate_ids = {candidate.commit_sha: candidate.id for candidate in candidates}
    groups: dict[str, list[GitChangeObservation]] = {}
    for observation in observations:
        if not observation.additions and not observation.deletions:
            continue
        groups.setdefault(_workstream_key(observation), []).append(observation)

    bullets: list[GitResearchBullet] = []
    for group in groups.values():
        group = group[:4]
        files = sorted({path for item in group for path in item.files})
        symbols = _unique(item for observation in group for item in observation.symbols)
        kinds = _unique(item for observation in group for item in observation.change_kinds)
        description = _describe_change(symbols, kinds, files)
        if description is None:
            continue
        bullets.append(
            GitResearchBullet(
                text=description,
                source_candidate_ids=[
                    candidate_ids[item.commit_sha]
                    for item in group
                    if item.commit_sha in candidate_ids
                ],
                source_commit_shas=[item.commit_sha for item in group],
                source_files=files,
                diff_hashes=[item.diff_hash for item in group],
                confidence=0.9 if symbols else 0.65,
            )
        )
    bullets.sort(key=lambda bullet: (-bullet.confidence, bullet.text.casefold()))
    summary = (
        f"Diff-derived local research draft for {repo_path}: "
        + (bullets[0].text if bullets else "no substantive diff-backed workstreams found")
        + ". Needs review; no evidence was auto-approved."
    )
    return summary, bullets[:4]


def synthesize_project_research(
    repo_path: str, candidates: list[GitEvidenceCandidate]
) -> tuple[str, list[GitResearchBullet]]:
    """Legacy metadata-only draft retained for the lower-level scan command."""
    groups: list[list[tuple[GitEvidenceCandidate, str]]] = []
    for candidate in candidates:
        subject = _candidate_subject(candidate)
        if not subject or _is_obvious_chore(subject):
            continue
        for group in groups:
            if _subjects_are_similar(subject, group[0][1]):
                group.append((candidate, subject))
                break
        else:
            groups.append([(candidate, subject)])
    bullets = [
        GitResearchBullet(
            text=group[0][1],
            source_candidate_ids=[candidate.id for candidate, _ in group],
            source_commit_shas=[candidate.commit_sha for candidate, _ in group],
            source_files=[],
            diff_hashes=[],
            confidence=0.5,
        )
        for group in groups
    ]
    return (
        "Generated locally from Git commit metadata; factual draft only, needs review before "
        f"approval or resume use. Repository: {repo_path}.",
        bullets[:4],
    )


def _parse_commit(repo: Path, record: str) -> GitCommit:
    sha, parents, subject = record.strip().split("\x1f", maxsplit=2)
    files = _run_git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
    if files.returncode != 0:
        raise ValueError(f"could not inspect git commit {sha}")
    return GitCommit(
        sha,
        tuple(parent for parent in parents.split() if parent),
        subject.strip(),
        tuple(files.stdout.splitlines()),
    )


def _is_high_signal(commit: GitCommit) -> bool:
    if len(commit.parents) > 1 or not commit.files:
        return False
    return any(
        Path(path).name not in _LOW_SIGNAL_NAMES
        and not path.casefold().endswith(_LOW_SIGNAL_SUFFIXES)
        for path in commit.files
    )


def _numstat_totals(text: str) -> tuple[int, int]:
    additions = deletions = 0
    for line in text.splitlines():
        added, removed, _ = line.split("\t", maxsplit=2)
        additions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
    return additions, deletions


def _extract_symbols(diff: str) -> list[str]:
    symbols: list[str] = []
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(diff):
            symbols.append(" ".join(match.groups()))
    return _unique(symbols)[:20]


def _classify_change(files: tuple[str, ...], diff: str) -> list[str]:
    text = (" ".join(files) + " " + diff).casefold()
    kinds = []
    if any(token in text for token in ("route", "endpoint", "post ", "get ", "api")):
        kinds.append("API")
    if any(token in text for token in ("sqlite", "database", "repository", "persist", "store")):
        kinds.append("persistence")
    if any(token in text for token in ("test_", "/tests/", "assert ")):
        kinds.append("testing")
    if any(token in text for token in ("auth", "csrf", "encrypt", "secret")):
        kinds.append("security")
    if any(token in text for token in ("react", "component", "template", "css")):
        kinds.append("UI")
    return kinds or ["implementation"]


def _workstream_key(observation: GitChangeObservation) -> str:
    path = observation.files[0] if observation.files else "root"
    parts = Path(path).parts
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def _describe_change(symbols: list[str], kinds: list[str], files: list[str]) -> str | None:
    if not files:
        return None
    focus = ", ".join(symbols[:2]) if symbols else ", ".join(files[:2])
    return f"Implemented {' and '.join(kinds[:2])} changes for {focus}"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _candidate_subject(candidate: GitEvidenceCandidate) -> str:
    first_line = candidate.text.splitlines()[0] if candidate.text else ""
    return first_line.removeprefix("Git commit: ").strip()


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


def _subjects_are_similar(left: str, right: str) -> bool:
    ignored = {"add", "implement", "for", "the", "a", "an", "to", "with", "and", "of"}
    left_words = _subject_words(left, ignored)
    right_words = _subject_words(right, ignored)
    overlap = len(left_words & right_words)
    return (
        bool(left_words and right_words)
        and overlap >= 2
        and overlap / len(left_words | right_words) >= 0.5
    )


def _subject_words(subject: str, ignored: set[str]) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", subject.casefold())
        if len(word) > 1 and word not in ignored
    }


def _run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=repo, capture_output=True, text=True, check=False
    )
