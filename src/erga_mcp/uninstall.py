"""Bounded, explicit removal of Erga-owned local state and integrations."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ErgaConfig, load_config
from .cron_setup import monitor_paths
from .discord_bridge import (
    delete_discord_token,
    stop_discord_bridge,
)
from .discord_bridge import (
    settings_path as discord_settings_path,
)
from .host_connections import (
    HOST_ADAPTERS,
    HostConnectionRecord,
    connection_registry_path,
    load_connection_registry,
    remove_host_connection,
)
from .zoho_oauth import delete_credentials as delete_zoho_credentials

_CONFIRMATION = "DELETE ERGA"
_CANONICAL_CONFIG_DIRECTORY = Path(".config") / "erga-mcp"
_KNOWN_DATA_FILES = (
    "erga.sqlite3",
    "erga.sqlite3-shm",
    "erga.sqlite3-wal",
    "discord-bridge-process.json",
    "discord-bridge.pid",
    "discord-bridge.log",
)


@dataclass(frozen=True)
class RemovalTarget:
    path: str
    kind: str
    canonical_path: str


@dataclass(frozen=True)
class UninstallPlan:
    config_path: str
    home: str
    cwd: str
    hermes_home: str
    targets: tuple[RemovalTarget, ...]
    host_connections: tuple[HostConnectionRecord, ...]
    credential_accounts: tuple[str, ...]
    preserved_sources: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "config_path": self.config_path,
            "home": self.home,
            "cwd": self.cwd,
            "hermes_home": self.hermes_home,
            "targets": [asdict(target) for target in self.targets],
            "host_connections": [asdict(record) for record in self.host_connections],
            "credential_accounts": list(self.credential_accounts),
            "preserved_sources": list(self.preserved_sources),
            "warnings": list(self.warnings),
        }


def _absolute(path: Path) -> Path:
    return path.expanduser().absolute()


def _canonical_deletion_path(path: Path) -> Path:
    """Resolve parent links without following the final entry that Erga may unlink."""
    return path.parent.resolve(strict=False) / path.name


def _legacy_targets(home: Path) -> tuple[Path, ...]:
    paths = [
        home / ".erga",
        home / ".erga-mcp",
        home / ".cache" / "erga-mcp",
        home / ".local" / "share" / "erga-mcp",
    ]
    for variable, suffix in (
        ("XDG_CONFIG_HOME", "erga-mcp"),
        ("XDG_CACHE_HOME", "erga-mcp"),
        ("XDG_DATA_HOME", "erga-mcp"),
    ):
        root = os.environ.get(variable)
        if root:
            paths.append(_absolute(Path(root)) / suffix)
    if sys.platform == "darwin":
        library = home / "Library"
        paths.extend(
            [
                library / ".erga",
                library / ".erga-mcp",
                library / "Application Support" / "Erga",
                library / "Application Support" / "Erga MCP",
                library / "Application Support" / "erga-mcp",
                library / "Application Support" / ".erga",
                library / "Application Support" / ".erga-mcp",
                library / "Caches" / "Erga",
                library / "Caches" / "erga-mcp",
                library / "Caches" / ".erga",
                library / "Caches" / ".erga-mcp",
                library / "Logs" / "Erga",
                library / "Logs" / "erga-mcp",
                library / "Preferences" / "com.erga-mcp.plist",
                library / "Preferences" / "org.erga-mcp.plist",
            ]
        )
    elif os.name == "nt":
        for variable in ("APPDATA", "LOCALAPPDATA"):
            root = os.environ.get(variable)
            if root:
                base = _absolute(Path(root))
                paths.extend((base / "Erga", base / "erga-mcp", base / ".erga-mcp"))
    return tuple(paths)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_owned_directory(path: Path, *, config: ErgaConfig) -> bool:
    home = Path.home().absolute()
    protected = {
        Path(path.anchor),
        home,
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        config.config_path.parent,
    }
    if path in protected:
        return False
    if path == config.data_dir:
        return path.name.casefold() in {"state", "erga", "erga-mcp", ".erga", ".erga-mcp"}
    if path == config.resume.output_root:
        name = path.name.casefold()
        return (
            name.startswith("erga")
            or name in {"output", "generated-resumes", "generated resumes"}
            or _is_within(path, config.config_path.parent)
            or (config.vault_path is not None and _is_within(path, config.vault_path / "Erga"))
        )
    return True


def _preserved_resume_sources(config: ErgaConfig) -> tuple[Path, ...]:
    managed_root = config.data_dir / "resume-sources"
    candidates = (
        config.resume.master_path,
        config.resume.template_path,
        config.resume.reference_path,
        config.cover_letter.template_path,
        config.cover_letter.writing_sample_path,
    )
    return tuple(
        _absolute(path)
        for path in candidates
        if path is not None and not _is_within(_absolute(path), _absolute(managed_root))
    )


def _contains_preserved_source(target: Path, preserved: tuple[Path, ...]) -> bool:
    return any(source == target or _is_within(source, target) for source in preserved)


def _discord_project(config_path: Path) -> Path | None:
    path = discord_settings_path(config_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("project_dir") if isinstance(payload, dict) else None
    return _absolute(Path(str(value))) if value else None


def _candidate_host_records(
    config_path: Path,
    project_dirs: tuple[Path, ...],
) -> tuple[tuple[HostConnectionRecord, ...], tuple[str, ...]]:
    warnings: list[str] = []
    try:
        records = list(load_connection_registry(config_path))
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        records = []
        warnings.append(f"Could not read the host connection registry: {error}")
    projects = {_absolute(path) for path in project_dirs}
    discord_project = _discord_project(config_path)
    if discord_project is not None:
        projects.add(discord_project)
    for project in projects:
        if not project.is_dir():
            warnings.append(f"Host workspace no longer exists: {project}")
            continue
        for adapter in HOST_ADAPTERS.values():
            target = project / adapter.project_target
            if not target.is_file():
                continue
            records.append(
                HostConnectionRecord(
                    host=adapter.id,
                    project_dir=str(project),
                    target_path=str(target),
                )
            )
    unique: dict[tuple[str, str, str], HostConnectionRecord] = {}
    for record in records:
        key = (HOST_ADAPTERS[record.host].format, record.target_path, record.server_name)
        unique[key] = record
    return tuple(unique.values()), tuple(warnings)


def build_uninstall_plan(
    config_path: Path,
    *,
    project_dirs: tuple[Path, ...] = (),
    home: Path | None = None,
    cwd: Path | None = None,
    hermes_home: Path | None = None,
) -> UninstallPlan:
    """Inventory only exact Erga-owned locations; never crawl a user's home directory."""
    normalized_config = _absolute(config_path)
    home = _absolute(home or Path.home())
    cwd = _absolute(cwd or Path.cwd())
    selected_hermes_home = _absolute(
        hermes_home or Path(os.environ.get("HERMES_HOME", home / ".hermes"))
    )
    warnings: list[str] = []
    targets: list[RemovalTarget] = []
    config: ErgaConfig | None = None
    if normalized_config.is_file():
        try:
            config = load_config(normalized_config)
        except (OSError, ValueError) as error:
            warnings.append(
                f"Config could not be parsed; only standard Erga paths are safe: {error}"
            )

    preserved = _preserved_resume_sources(config) if config is not None else ()

    def add_target(path: Path, kind: str, *, require_owned: bool = False) -> None:
        normalized = _absolute(path)
        if not normalized.exists() and not normalized.is_symlink():
            return
        if (
            require_owned
            and config is not None
            and not _safe_owned_directory(normalized, config=config)
        ):
            warnings.append(f"Skipped unsafe configured directory: {normalized}")
            return
        if normalized.is_dir() and _contains_preserved_source(normalized, preserved):
            warnings.append(f"Skipped directory containing an original user source: {normalized}")
            return
        targets.append(
            RemovalTarget(
                str(normalized),
                kind,
                str(_canonical_deletion_path(normalized)),
            )
        )

    canonical_root = home / _CANONICAL_CONFIG_DIRECTORY
    if normalized_config == canonical_root / "config.toml":
        add_target(canonical_root, "Erga private configuration and state")
    add_target(normalized_config, "Erga configuration")
    add_target(discord_settings_path(normalized_config), "Discord bridge settings")
    add_target(connection_registry_path(normalized_config), "MCP host connection registry")
    if config is not None:
        if _safe_owned_directory(config.data_dir, config=config):
            add_target(config.data_dir, "Erga private state")
        else:
            warnings.append(f"Skipped unsafe configured directory: {config.data_dir}")
            for name in _KNOWN_DATA_FILES:
                add_target(config.data_dir / name, "Erga private state file")
            add_target(config.data_dir / "resume-sources", "managed resume copies")
        add_target(config.resume.output_root, "generated resume packages", require_owned=True)
        if config.vault_path is not None:
            add_target(config.vault_path / "Erga", "Erga Obsidian projection")

    for path in _legacy_targets(home):
        add_target(path, "legacy Erga artifact")

    hermes_scripts = selected_hermes_home / "scripts"
    for path in monitor_paths(hermes_scripts):
        add_target(path, "optional Hermes monitor file")

    host_records, host_warnings = _candidate_host_records(
        normalized_config,
        (*project_dirs, cwd),
    )
    warnings.extend(host_warnings)
    credentials = ["Discord bot token"]
    if config is not None and config.mail_client_id:
        credentials.append(f"Zoho OAuth credentials for {config.mail_client_id}")

    unique_targets: dict[str, RemovalTarget] = {}
    for target in targets:
        unique_targets[target.path] = target
    return UninstallPlan(
        config_path=str(normalized_config),
        home=str(home),
        cwd=str(cwd),
        hermes_home=str(selected_hermes_home),
        targets=tuple(sorted(unique_targets.values(), key=lambda item: item.path)),
        host_connections=host_records,
        credential_accounts=tuple(credentials),
        preserved_sources=tuple(sorted(str(path) for path in preserved)),
        warnings=tuple(warnings),
    )


def render_uninstall_plan(plan: UninstallPlan) -> str:
    lines = ["", "Erga uninstall plan", "", "Permanently delete:"]
    lines.extend(f"  - {target.path} ({target.kind})" for target in plan.targets)
    if not plan.targets:
        lines.append("  - No Erga filesystem artifacts found")
    lines.extend(["", "Also remove:"])
    lines.extend(f"  - {name}" for name in plan.credential_accounts)
    lines.append(f"  - Erga entries from {len(plan.host_connections)} known host configuration(s)")
    if plan.preserved_sources:
        lines.extend(["", "Always preserve original user sources:"])
        lines.extend(f"  - {path}" for path in plan.preserved_sources)
    lines.extend(
        [
            "",
            "The source checkout, its .venv, uv's shared cache, and host-owned credentials are not "
            "Erga data and will not be deleted.",
        ]
    )
    if plan.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    return "\n".join(lines)


def _verified_legacy_bridge_pid(config_path: Path, data_dir: Path) -> int | None:
    pid_path = data_dir / "discord-bridge.pid"
    if not pid_path.is_file():
        return None
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if sys.platform.startswith("linux"):
        try:
            rendered = (
                (Path("/proc") / str(pid) / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
        except OSError:
            return None
    elif os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        rendered = completed.stdout.strip() if completed.returncode == 0 else ""
    else:
        command = ["ps", "-p", str(pid), "-o", "command="]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        rendered = completed.stdout.strip() if completed.returncode == 0 else ""
    expected_config = str(_absolute(config_path))
    return pid if "erga_mcp.discord_bridge" in rendered and expected_config in rendered else None


def _stop_bridges(config_path: Path, config: ErgaConfig | None) -> list[str]:
    actions: list[str] = []
    if config is None:
        return actions
    legacy_pid = _verified_legacy_bridge_pid(config_path, config.data_dir)
    if legacy_pid is not None:
        os.kill(legacy_pid, signal.SIGTERM)
        actions.append(f"stopped verified legacy Discord bridge process {legacy_pid}")
        for _attempt in range(50):
            try:
                os.kill(legacy_pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("verified legacy Discord bridge did not stop; no files were deleted")
    try:
        status = stop_discord_bridge(config_path)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return actions
    stopped_pid = status.get("stopped_pid")
    if isinstance(stopped_pid, int):
        actions.append(f"stopped Discord bridge process {stopped_pid}")
    if status.get("running"):
        raise RuntimeError("verified Discord bridge did not stop; no files were deleted")
    return actions


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def apply_uninstall(plan: UninstallPlan) -> dict[str, object]:
    """Apply only targets that remain safe under a freshly derived uninstall plan."""
    config_path = Path(plan.config_path)
    fresh_plan = build_uninstall_plan(
        config_path,
        project_dirs=tuple(Path(record.project_dir) for record in plan.host_connections),
        home=Path(plan.home),
        cwd=Path(plan.cwd),
        hermes_home=Path(plan.hermes_home),
    )
    fresh_targets = {target.path: target for target in fresh_plan.targets}
    approved_targets: list[RemovalTarget] = []
    revalidation_warnings: list[str] = []
    for reviewed in plan.targets:
        current = fresh_targets.get(reviewed.path)
        if current is None:
            revalidation_warnings.append(
                f"Skipped target that is no longer authorized by current configuration: "
                f"{reviewed.path}"
            )
        elif current.canonical_path != reviewed.canonical_path:
            revalidation_warnings.append(
                f"Skipped target whose parent resolution changed after review: {reviewed.path}"
            )
        else:
            approved_targets.append(current)
    approved_paths = {target.path for target in approved_targets}
    config_is_stable = str(config_path) in approved_paths and not config_path.is_symlink()
    try:
        config = load_config(config_path) if config_is_stable and config_path.is_file() else None
    except (OSError, ValueError):
        config = None
    actions = _stop_bridges(config_path, config)
    credential_results: list[str] = []
    if delete_discord_token(config_path):
        credential_results.append("Discord bot token")
    if config is not None and config.mail_client_id:
        credential_results.extend(delete_zoho_credentials(config.mail_client_id))

    host_results: list[dict[str, object]] = []
    for record in plan.host_connections:
        try:
            host_results.append(asdict(remove_host_connection(record, config_path=config_path)))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            host_results.append(
                {
                    "host": record.host,
                    "target_path": record.target_path,
                    "removed": False,
                    "reason": str(error),
                }
            )

    deleted: list[str] = []
    for target in sorted(
        approved_targets,
        key=lambda item: len(Path(item.path).parts),
        reverse=True,
    ):
        path = Path(target.path)
        if str(_canonical_deletion_path(path)) != target.canonical_path:
            revalidation_warnings.append(
                f"Skipped target whose parent resolution changed during uninstall: {path}"
            )
            continue
        if path.exists() or path.is_symlink():
            _remove_path(path)
            deleted.append(str(path))
    return {
        "status": "uninstalled",
        "deleted_paths": sorted(deleted),
        "deleted_credentials": credential_results,
        "host_connections": host_results,
        "process_actions": actions,
        "preserved_sources": list(plan.preserved_sources),
        "warnings": [*fresh_plan.warnings, *revalidation_warnings],
    }


def confirmation_phrase() -> str:
    return _CONFIRMATION
