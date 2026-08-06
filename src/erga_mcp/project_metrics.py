from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .git_evidence import validate_worktree

DEFAULT_COMMIT_LIMIT = 200
_DOCUMENTATION_SUFFIXES = frozenset({".adoc", ".md", ".pdf", ".rst", ".rtf", ".txt"})
_LOCK_FILE_NAMES = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pdm.lock",
        "pipfile.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)
_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".cache",
        ".direnv",
        ".git",
        ".gradle",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        ".virtualenv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "env",
        "generated",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
        "venv",
        "virtualenv",
    }
)
_GENERATED_PATH_PARTS = frozenset({"snapshots", "__snapshots__"})
_ASSET_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".csv",
        ".db",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".json",
        ".map",
        ".mov",
        ".mp3",
        ".mp4",
        ".otf",
        ".parquet",
        ".png",
        ".sqlite",
        ".svg",
        ".ttf",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xml",
        ".yaml",
        ".yml",
    }
)
_SOURCE_SUFFIXES = frozenset(
    {
        ".bash",
        ".c",
        ".cc",
        ".clj",
        ".cljs",
        ".cpp",
        ".cs",
        ".css",
        ".cu",
        ".cuh",
        ".dart",
        ".elm",
        ".erl",
        ".ex",
        ".exs",
        ".fish",
        ".fs",
        ".fsx",
        ".go",
        ".gql",
        ".graphql",
        ".h",
        ".hcl",
        ".hh",
        ".hpp",
        ".hrl",
        ".htm",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".less",
        ".lua",
        ".m",
        ".mm",
        ".php",
        ".proto",
        ".ps1",
        ".py",
        ".pyi",
        ".r",
        ".rb",
        ".rs",
        ".sass",
        ".scala",
        ".scss",
        ".sh",
        ".sql",
        ".svelte",
        ".swift",
        ".tf",
        ".ts",
        ".tsx",
        ".vue",
        ".zsh",
    }
)
_LANGUAGE_BY_SUFFIX = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cu": "CUDA",
    ".cuh": "CUDA",
    ".cs": "C#",
    ".css": "CSS",
    ".go": "Go",
    ".h": "C/C++",
    ".hpp": "C++",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".php": "PHP",
    ".py": "Python",
    ".pyi": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
}
_TEST_DEFINITION_PATTERNS = (
    re.compile(r"\b(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\("),
    re.compile(r"\bfunc\s+(Test[A-Za-z0-9_]+)\s*\("),
    re.compile(r"\bfn\s+(test_[A-Za-z0-9_]+)\s*\("),
    re.compile(r"\b(?:it|test)\s*\(\s*['\"`]([^'\"`]+)['\"`]"),
)
_HTTP_ROUTE_PATTERNS = (
    re.compile(
        r"@(?:[A-Za-z_][\w]*\.)?(get|post|put|patch|delete)"
        r"\(\s*['\"`]([^'\"`]+)['\"`]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:app|router)\.(get|post|put|patch|delete)"
        r"\(\s*['\"`]([^'\"`]+)['\"`]",
        re.IGNORECASE,
    ),
    re.compile(
        r"@(Get|Post|Put|Patch|Delete)Mapping"
        r"\(\s*(?:value\s*=\s*)?['\"`]([^'\"`]+)['\"`]",
        re.IGNORECASE,
    ),
)
_NEXT_ROUTE_PATTERN = re.compile(
    r"\bexport\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b"
)
_CLI_COMMAND_PATTERNS = (
    re.compile(
        r"@(?:[A-Za-z_][\w]*\.)?command\(\s*['\"`]([^'\"`]+)['\"`]",
        re.IGNORECASE,
    ),
    re.compile(r"\badd_parser\(\s*['\"`]([^'\"`]+)['\"`]", re.IGNORECASE),
    re.compile(r"\b(?:app|cli|program)\.command\(\s*['\"`]([^'\"`\s]+)", re.IGNORECASE),
)
_MIN_TEST_CASE_METRIC = 5
_MIN_HTTP_ROUTE_METRIC = 3
_MIN_CLI_COMMAND_METRIC = 3


@dataclass(frozen=True)
class ProjectMetricProposal:
    repo_path: str
    attribution: str
    author_email: str | None
    commit_limit: int
    history_scope: str
    commit_count: int
    total_commit_count: int
    commit_share_percent: int
    files_changed: int
    source_files_changed: int
    test_files_changed: int
    other_files_changed: int
    test_file_share_percent: int
    lines_added: int
    lines_deleted: int
    source_lines_added: int
    source_lines_deleted: int
    test_lines_added: int
    test_lines_deleted: int
    languages: tuple[str, ...]
    attributed_test_cases: int
    attributed_http_routes: int
    attributed_cli_commands: int
    review_facts: tuple[str, ...]
    resume_metric_candidates: tuple[str, ...]
    resume_use: str = "engineering_context_only"
    requires_user_confirmation: bool = True


@dataclass(frozen=True)
class _Commit:
    sha: str
    author_email: str


@dataclass(frozen=True)
class _FileTotals:
    path: str
    added: int
    deleted: int
    category: str


@dataclass(frozen=True)
class _SemanticMarker:
    kind: str
    path: str
    identifier: str


def propose_git_project_metrics(
    repo: Path, *, author_email: str, commit_limit: int = DEFAULT_COMMIT_LIMIT
) -> ProjectMetricProposal:
    """Return bounded Git facts for review and internal engineering context."""
    normalized_email = author_email.strip().casefold()
    if not normalized_email:
        raise ValueError("author_email is required to make attributable metric proposals")
    resolved = validate_worktree(repo)
    commits = _commits(resolved, commit_limit)
    selected_shas = {
        commit.sha for commit in commits if commit.author_email.casefold() == normalized_email
    }
    if not selected_shas:
        raise ValueError("no commits matched author_email in the selected Git history")
    return summarize_git_project_metrics(
        resolved,
        attributed_commit_shas=selected_shas,
        commit_limit=commit_limit,
        attribution="author_email",
        author_email=author_email.strip(),
        commits=commits,
    )


def summarize_git_project_metrics(
    repo: Path,
    *,
    attributed_commit_shas: set[str],
    commit_limit: int = DEFAULT_COMMIT_LIMIT,
    attribution: str = "connected_github_user",
    author_email: str | None = None,
    commits: list[_Commit] | None = None,
) -> ProjectMetricProposal:
    """Summarize exact attributed commits for ranking and reviewable draft metrics.

    Only recognized implementation and test files contribute to line totals. Generated output,
    dependencies, lockfiles, documentation, data, and media are deliberately excluded. These
    accounting facts help rank and understand a project, but they are never proposed as resume
    metrics: Git cannot prove user impact, performance, adoption, or coverage, and file, commit,
    language, and line counts are not outcome claims.
    """
    if commit_limit < 1:
        raise ValueError("commit_limit must be positive")
    if not attributed_commit_shas:
        raise ValueError("attributed_commit_shas cannot be empty")
    resolved = validate_worktree(repo)
    if commits is None:
        selected = [_commit_by_sha(resolved, sha) for sha in sorted(attributed_commit_shas)]
        total_commit_count = _total_non_merge_commits(resolved)
        history_scope = "all reachable non-merge commits"
    else:
        selected = [commit for commit in commits if commit.sha in attributed_commit_shas]
        total_commit_count = len(commits)
        history_scope = f"latest {commit_limit} non-merge commits"
    if not selected:
        raise ValueError("no attributed commits were present in the selected Git history")

    latest_by_path: dict[str, _FileTotals] = {}
    source_lines_added = source_lines_deleted = 0
    test_lines_added = test_lines_deleted = 0
    for commit in selected:
        for added, deleted, path in _numstat(resolved, commit.sha):
            category = _file_category(path)
            latest_by_path[path] = _FileTotals(path, added, deleted, category)
            if category == "source":
                source_lines_added += added
                source_lines_deleted += deleted
            elif category == "test":
                test_lines_added += added
                test_lines_deleted += deleted

    source_files = {item.path for item in latest_by_path.values() if item.category == "source"}
    test_files = {item.path for item in latest_by_path.values() if item.category == "test"}
    other_files = {
        item.path for item in latest_by_path.values() if item.category in {"other", "ignored"}
    }
    code_and_test_files = source_files | test_files
    languages = tuple(
        sorted(
            {
                language
                for path in code_and_test_files
                if (language := _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.casefold()))
            }
        )
    )
    commit_share_percent = _percent(len(selected), total_commit_count)
    test_file_share_percent = _percent(len(test_files), len(code_and_test_files))
    lines_added = source_lines_added + test_lines_added
    lines_deleted = source_lines_deleted + test_lines_deleted
    semantic_markers = {
        marker
        for commit in reversed(selected)
        for marker in _semantic_markers(resolved, commit.sha)
        if _semantic_marker_is_current(resolved, marker)
    }
    attributed_test_cases = sum(marker.kind == "test_case" for marker in semantic_markers)
    attributed_http_routes = sum(marker.kind == "http_route" for marker in semantic_markers)
    attributed_cli_commands = sum(marker.kind == "cli_command" for marker in semantic_markers)
    test_fact = (
        f"{len(test_files)} test file{'s' if len(test_files) != 1 else ''}"
        if test_files
        else "no recognized test files"
    )
    candidates = (
        f"Attributed {len(selected)} of {total_commit_count} non-merge commits "
        f"({commit_share_percent}%) in the selected history.",
        f"Touched {len(source_files)} unique implementation file"
        f"{'s' if len(source_files) != 1 else ''} and {test_fact}.",
        f"Recorded {lines_added:,} additions and {lines_deleted:,} deletions only in "
        "recognized source and test files.",
    )
    resume_metric_candidates: list[str] = []
    if attributed_test_cases >= _MIN_TEST_CASE_METRIC:
        resume_metric_candidates.append(
            f"Verified {attributed_test_cases} distinct attributed test cases present in the "
            "current repository."
        )
    if attributed_http_routes >= _MIN_HTTP_ROUTE_METRIC:
        resume_metric_candidates.append(
            f"Verified {attributed_http_routes} distinct attributed HTTP routes present in the "
            "current repository."
        )
    if attributed_cli_commands >= _MIN_CLI_COMMAND_METRIC:
        resume_metric_candidates.append(
            f"Verified {attributed_cli_commands} distinct attributed CLI commands present in the "
            "current repository."
        )
    return ProjectMetricProposal(
        repo_path=str(resolved),
        attribution=attribution,
        author_email=author_email,
        commit_limit=commit_limit,
        history_scope=history_scope,
        commit_count=len(selected),
        total_commit_count=total_commit_count,
        commit_share_percent=commit_share_percent,
        files_changed=len(code_and_test_files),
        source_files_changed=len(source_files),
        test_files_changed=len(test_files),
        other_files_changed=len(other_files),
        test_file_share_percent=test_file_share_percent,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        source_lines_added=source_lines_added,
        source_lines_deleted=source_lines_deleted,
        test_lines_added=test_lines_added,
        test_lines_deleted=test_lines_deleted,
        languages=languages,
        attributed_test_cases=attributed_test_cases,
        attributed_http_routes=attributed_http_routes,
        attributed_cli_commands=attributed_cli_commands,
        review_facts=candidates,
        resume_metric_candidates=tuple(resume_metric_candidates),
        resume_use=(
            "verified_functional_scope" if resume_metric_candidates else "engineering_context_only"
        ),
    )


def project_metric_model_context(proposal: ProjectMetricProposal) -> dict[str, object]:
    """Return verified engineering signals without exposing activity counts as resume claims."""
    return {
        "attribution": proposal.attribution,
        "attributed_changes_observed": proposal.commit_count > 0,
        "has_implementation_changes": proposal.source_files_changed > 0,
        "has_test_changes": proposal.test_files_changed > 0,
        "languages": list(proposal.languages),
        "resume_metric_candidates": list(proposal.resume_metric_candidates),
        "resume_use": proposal.resume_use,
        "activity_metrics_allowed_in_resume": False,
        "requires_user_confirmation": proposal.requires_user_confirmation,
    }


def _semantic_markers(repo: Path, sha: str) -> set[_SemanticMarker]:
    result = _run_git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--no-renames",
        "--unified=0",
        "-r",
        sha,
        "--",
        *(f":(glob)**/*{suffix}" for suffix in sorted(_SOURCE_SUFFIXES)),
        *(
            f":(exclude,glob)**/{directory}/**"
            for directory in sorted(_IGNORED_DIRECTORY_NAMES | _GENERATED_PATH_PARTS)
        ),
    )
    if result.returncode != 0:
        raise ValueError(f"could not inspect semantic Git changes for commit {sha}")
    current_path = ""
    markers: set[_SemanticMarker] = set()
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            continue
        if not current_path or not raw_line.startswith("+") or raw_line.startswith("+++"):
            continue
        line = raw_line[1:]
        category = _file_category(current_path)
        if category not in {"source", "test"}:
            continue
        if category == "test":
            for pattern in _TEST_DEFINITION_PATTERNS:
                for match in pattern.finditer(line):
                    markers.add(_SemanticMarker("test_case", current_path, match.group(1)))
        for pattern in _HTTP_ROUTE_PATTERNS:
            for match in pattern.finditer(line):
                method, route = match.groups()
                markers.add(
                    _SemanticMarker("http_route", current_path, f"{method.upper()} {route}")
                )
        if "/route." in current_path.replace("\\", "/").casefold():
            for match in _NEXT_ROUTE_PATTERN.finditer(line):
                markers.add(_SemanticMarker("http_route", current_path, match.group(1).upper()))
        for pattern in _CLI_COMMAND_PATTERNS:
            for match in pattern.finditer(line):
                markers.add(_SemanticMarker("cli_command", current_path, match.group(1)))
    return markers


def _semantic_marker_is_current(repo: Path, marker: _SemanticMarker) -> bool:
    path = repo / marker.path
    if not path.is_file():
        return False
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    needle = marker.identifier.split(" ", maxsplit=1)[-1]
    return needle in source


def _commits(repo: Path, limit: int) -> list[_Commit]:
    result = _run_git(
        repo,
        "log",
        "--all",
        "--no-merges",
        f"--max-count={limit}",
        "--format=%H%x1f%ae",
    )
    if result.returncode != 0:
        raise ValueError("could not read local Git history")
    values: list[_Commit] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\x1f", maxsplit=1)
        if len(parts) == 2:
            values.append(_Commit(parts[0], parts[1]))
    return values


def _commit_by_sha(repo: Path, sha: str) -> _Commit:
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", sha) is None:
        raise ValueError("attributed commit SHAs must be hexadecimal Git object IDs")
    result = _run_git(repo, "show", "-s", "--format=%H%x1f%ae%x1f%P", sha)
    if result.returncode != 0:
        raise ValueError(f"attributed commit is unavailable in the local worktree: {sha}")
    parts = result.stdout.rstrip("\r\n").split("\x1f", maxsplit=2)
    if len(parts) != 3:
        raise ValueError(f"could not inspect attributed commit: {sha}")
    if len(parts[2].split()) > 1:
        raise ValueError(f"attributed commit must not be a merge commit: {sha}")
    return _Commit(parts[0], parts[1])


def _total_non_merge_commits(repo: Path) -> int:
    result = _run_git(repo, "rev-list", "--all", "--count", "--no-merges")
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        raise ValueError("could not count local Git history")
    return int(result.stdout.strip())


def _numstat(repo: Path, sha: str) -> list[tuple[int, int, str]]:
    result = _run_git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--numstat",
        "--no-renames",
        "-r",
        sha,
    )
    if result.returncode != 0:
        raise ValueError(f"could not inspect git diff for commit {sha}")
    values: list[tuple[int, int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", maxsplit=2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        values.append(
            (int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0, path)
        )
    return values


def _file_category(path: str) -> str:
    normalized = path.replace("\\", "/").casefold()
    pure_path = PurePosixPath(normalized)
    parts = set(pure_path.parts)
    name = pure_path.name
    suffix = pure_path.suffix
    if (
        name in _LOCK_FILE_NAMES
        or parts & _IGNORED_DIRECTORY_NAMES
        or parts & _GENERATED_PATH_PARTS
        or suffix in _DOCUMENTATION_SUFFIXES
        or suffix in _ASSET_SUFFIXES
        or name.endswith((".min.js", ".min.css", ".snap"))
    ):
        return "ignored"
    if suffix not in _SOURCE_SUFFIXES:
        return "other"
    return "test" if _is_test(normalized) else "source"


def _is_test(path: str) -> bool:
    pure_path = PurePosixPath(path)
    name = pure_path.name
    parts = pure_path.parts
    stem = pure_path.stem
    return (
        name.startswith(("test_", "spec_"))
        or stem.endswith(("_test", "_spec", ".test", ".spec"))
        or any(part in {"test", "tests", "spec", "specs", "testing"} for part in parts)
    )


def _percent(numerator: int, denominator: int) -> int:
    return round(numerator / denominator * 100) if denominator else 0


def _run_git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
