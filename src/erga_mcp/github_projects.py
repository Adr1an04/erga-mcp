from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .private_files import restrict_private_directory

_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-fA-F]{40,64}")
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GitHubProject:
    repository: str
    name: str
    description: str
    language: str
    topics: tuple[str, ...]


def _run(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _validated_repository(repository: str) -> str:
    if _REPOSITORY.fullmatch(repository) is None:
        raise ValueError("GitHub project repositories must use an owner/repo name")
    return repository


def connected_github_login(*, runner: Runner = subprocess.run) -> str:
    """Return the authenticated GitHub login without reading or persisting credentials."""
    result = _run(["gh", "api", "user", "--jq", ".login"], runner=runner)
    login = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[A-Za-z0-9-]+", login) is None:
        raise RuntimeError("GitHub CLI is not connected; run `gh auth login` first")
    return login


def github_authored_commit_shas(
    repository: str,
    *,
    login: str,
    runner: Runner = subprocess.run,
) -> set[str]:
    """List every connected-user commit reachable from a repository's default branch."""
    repository = _validated_repository(repository)
    result = _run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repository}/commits?author={login}&per_page=100",
            "--jq",
            ".[].sha",
        ],
        runner=runner,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GitHub commit discovery failed for {repository}")
    return {line for line in result.stdout.splitlines() if _SHA.fullmatch(line)}


def discover_github_projects(
    *, cache_path: Path, runner: Runner = subprocess.run
) -> tuple[GitHubProject, ...]:
    """Refresh a private JSON index of owned/direct-collaborator GitHub repositories."""
    result = _run(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "user/repos?affiliation=owner,collaborator&sort=updated&per_page=100",
        ],
        runner=runner,
    )
    if result.returncode != 0:
        raise RuntimeError("GitHub project discovery failed")
    try:
        pages = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("GitHub project discovery returned invalid JSON") from error
    if not isinstance(pages, list):
        raise RuntimeError("GitHub project discovery returned an invalid repository list")
    projects: list[GitHubProject] = []
    for page in pages:
        if not isinstance(page, list):
            continue
        for item in page:
            if (
                not isinstance(item, dict)
                or item.get("archived") is True
                or item.get("disabled") is True
            ):
                continue
            repository = item.get("full_name")
            name = item.get("name")
            if not isinstance(repository, str) or not isinstance(name, str):
                continue
            language = item.get("language")
            description = item.get("description")
            topics = item.get("topics")
            projects.append(
                GitHubProject(
                    repository=_validated_repository(repository),
                    name=name,
                    description=description if isinstance(description, str) else "",
                    language=language if isinstance(language, str) else "",
                    topics=(
                        tuple(value for value in topics if isinstance(value, str) and value.strip())
                        if isinstance(topics, list)
                        else ()
                    ),
                )
            )
    projects.sort(key=lambda project: project.repository.casefold())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(cache_path.parent)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=cache_path.parent,
        prefix=f".{cache_path.name}-",
        delete=False,
    ) as temporary:
        json.dump([asdict(project) for project in projects], temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.chmod(0o600)
        temporary_path.replace(cache_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return tuple(projects)


def _remote_repository(repo: Path, *, runner: Runner = subprocess.run) -> str | None:
    result = _run(["git", "remote", "get-url", "origin"], cwd=repo, runner=runner)
    if result.returncode != 0:
        return None
    remote = result.stdout.strip().removesuffix(".git")
    match = re.search(r"github\.com[/:](?P<repository>[^/\s]+/[^/\s]+)$", remote)
    return match.group("repository") if match is not None else None


def find_local_github_worktree(
    repository: str,
    candidates: list[Path],
    *,
    runner: Runner = subprocess.run,
) -> Path | None:
    """Resolve an explicit GitHub slug only against already-known local worktrees."""
    repository = _validated_repository(repository).casefold()
    for candidate in candidates:
        remote = _remote_repository(candidate, runner=runner) if candidate.is_dir() else None
        if (remote or "").casefold() == repository:
            return candidate.resolve()
    return None


def ensure_github_worktree(
    repository: str,
    *,
    cache_root: Path,
    local_candidates: list[Path],
    runner: Runner = subprocess.run,
) -> Path:
    """Use a known local clone or refresh one selected repository in Erga's private cache."""
    repository = _validated_repository(repository)
    local = find_local_github_worktree(repository, local_candidates, runner=runner)
    if local is not None:
        refreshed = _run(
            ["git", "fetch", "origin", "--prune", "--no-tags"],
            cwd=local,
            runner=runner,
        )
        if refreshed.returncode != 0:
            raise RuntimeError(f"GitHub repository refresh failed for {repository}")
        return local

    cache_root.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(cache_root)
    owner, name = repository.split("/", maxsplit=1)
    target = cache_root / f"{owner.casefold()}--{name.casefold()}"
    if target.is_symlink():
        raise ValueError("GitHub cache worktrees must not be symlinks")
    if target.exists():
        remote = _remote_repository(target, runner=runner) if target.is_dir() else None
        if not target.is_dir() or (remote or "").casefold() != repository.casefold():
            raise ValueError("GitHub cache path contains a different repository")
        refreshed = _run(
            ["git", "fetch", "origin", "--prune", "--no-tags"],
            cwd=target,
            runner=runner,
        )
        if refreshed.returncode != 0:
            raise RuntimeError(f"GitHub repository refresh failed for {repository}")
        return target.resolve()

    with tempfile.TemporaryDirectory(prefix=".git-clone-", dir=cache_root) as directory:
        staging = Path(directory) / "repository"
        cloned = _run(
            [
                "gh",
                "repo",
                "clone",
                repository,
                str(staging),
                "--",
                "--filter=blob:none",
                "--no-tags",
            ],
            runner=runner,
        )
        if cloned.returncode != 0 or not staging.is_dir():
            raise RuntimeError(f"GitHub repository clone failed for {repository}")
        staging.replace(target)
    restrict_private_directory(target)
    return target.resolve()
