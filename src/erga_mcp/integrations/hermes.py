from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_SETTINGS_NAME = "erga-mcp-monitor.json"
_MAIL_SCRIPT_NAME = "erga-mcp-mail.py"
_HISTORY_SCRIPT_NAME = "erga-mcp-history.py"
_UPDATE_SETTINGS_NAME = "erga-mcp-update.json"
_UPDATE_SCRIPT_NAME = "erga-mcp-update.py"
_ROUTER_PLUGIN_NAME = "erga-mcp-router"
_ROUTER_PLUGIN_FILES = ("__init__.py", "plugin.yaml", "after-install.md")

_RUNNER = """from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MODE = {mode!r}
SETTINGS = Path(__file__).with_name("erga-mcp-monitor.json")


def main() -> int:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    command = [
        settings["python_executable"],
        "-m",
        "erga_mcp.cli",
    ]
    if MODE == "mail":
        command.extend(["mail", "sync", "--config", settings["config_path"], "--notify"])
    else:
        command.extend(
            [
                "mail",
                "history",
                "--config",
                settings["config_path"],
                "--days",
                str(settings["history_days"]),
            ]
        )
    environment = os.environ.copy()
    # Hermes itself can run from a different Python environment. Never pass its
    # interpreter-specific paths into Erga's selected interpreter: compiled
    # packages such as lxml cannot be shared safely across Python versions.
    for variable in ("PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(variable, None)
    environment["PYTHONPATH"] = settings["module_root"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
    )
    if completed.returncode:
        sys.stderr.write(completed.stderr or completed.stdout)
        return completed.returncode
    sys.stdout.write(completed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

_UPDATE_RUNNER = """from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SETTINGS = Path(__file__).with_name("erga-mcp-update.json")


def main() -> int:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    command = [
        settings["python_executable"],
        "-m",
        "erga_mcp.cli",
        "update",
        "--scheduled",
        "--config",
        settings["config_path"],
        "--hermes-home",
        settings["hermes_home"],
    ]
    environment = os.environ.copy()
    for variable in ("PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(variable, None)
    environment["PYTHONPATH"] = settings["module_root"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=600,
        env=environment,
    )
    if completed.returncode:
        sys.stderr.write(completed.stderr or completed.stdout)
        return completed.returncode
    sys.stdout.write(completed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def monitor_paths(scripts_dir: Path) -> tuple[Path, Path, Path]:
    """Return the exact Erga-owned monitor files in a selected Hermes scripts directory."""
    resolved = scripts_dir.expanduser().absolute()
    return (
        resolved / _SETTINGS_NAME,
        resolved / _MAIL_SCRIPT_NAME,
        resolved / _HISTORY_SCRIPT_NAME,
    )


def update_monitor_paths(scripts_dir: Path) -> tuple[Path, Path]:
    """Return the exact Erga-owned auto-update files in a Hermes scripts directory."""
    resolved = scripts_dir.expanduser().absolute()
    return (
        resolved / _UPDATE_SETTINGS_NAME,
        resolved / _UPDATE_SCRIPT_NAME,
    )


def _write_atomic(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def install_hermes_monitor_scripts(
    *,
    config_path: Path,
    scripts_dir: Path,
    python_executable: Path | None = None,
    history_days: int = 7,
    replace: bool = False,
) -> dict[str, object]:
    """Install no-agent monitor runners; creating delivery jobs remains an explicit action."""
    if history_days < 1 or history_days > 365:
        raise ValueError("history_days must be between 1 and 365")
    resolved_config = config_path.expanduser().resolve(strict=True)
    resolved_scripts = scripts_dir.expanduser().resolve()
    resolved_scripts.mkdir(parents=True, exist_ok=True)
    targets = list(monitor_paths(resolved_scripts))
    existing = [path for path in targets if path.exists()]
    if existing and not replace:
        raise FileExistsError(f"monitor files already exist: {', '.join(map(str, existing))}")
    settings = {
        "config_path": str(resolved_config),
        "history_days": history_days,
        "module_root": str(Path(__file__).resolve().parents[2]),
        # Preserve virtual-environment launcher symlinks. Resolving them can bypass the
        # environment's site-packages and leave scheduled runners without dependencies.
        "python_executable": str(
            (python_executable or Path(sys.executable)).expanduser().absolute()
        ),
    }
    _write_atomic(targets[0], json.dumps(settings, indent=2, sort_keys=True) + "\n")
    _write_atomic(targets[1], _RUNNER.format(mode="mail"))
    _write_atomic(targets[2], _RUNNER.format(mode="history"))
    return {
        "settings": str(targets[0]),
        "mail_script": _MAIL_SCRIPT_NAME,
        "history_script": _HISTORY_SCRIPT_NAME,
        "suggested_jobs": [
            {
                "name": "erga-mail-monitor",
                "schedule": "*/15 * * * *",
                "script": _MAIL_SCRIPT_NAME,
                "no_agent": True,
                "deliver": "origin",
            },
            {
                "name": "erga-history-digest",
                "schedule": "0 9 * * *",
                "script": _HISTORY_SCRIPT_NAME,
                "no_agent": True,
                "deliver": "origin",
            },
        ],
    }


def install_hermes_update_script(
    *,
    config_path: Path,
    scripts_dir: Path,
    python_executable: Path | None = None,
    hermes_home: Path | None = None,
    replace: bool = False,
) -> dict[str, object]:
    """Install an opt-in no-agent updater; scheduling remains a separate explicit action."""
    resolved_config = config_path.expanduser().resolve(strict=True)
    resolved_scripts = scripts_dir.expanduser().resolve()
    resolved_scripts.mkdir(parents=True, exist_ok=True)
    targets = list(update_monitor_paths(resolved_scripts))
    existing = [path for path in targets if path.exists()]
    if existing and not replace:
        raise FileExistsError(f"update files already exist: {', '.join(map(str, existing))}")
    resolved_hermes_home = (
        hermes_home.expanduser().resolve() if hermes_home is not None else resolved_scripts.parent
    )
    settings = {
        "config_path": str(resolved_config),
        "hermes_home": str(resolved_hermes_home),
        "module_root": str(Path(__file__).resolve().parents[2]),
        "python_executable": str(
            (python_executable or Path(sys.executable)).expanduser().absolute()
        ),
    }
    _write_atomic(targets[0], json.dumps(settings, indent=2, sort_keys=True) + "\n")
    _write_atomic(targets[1], _UPDATE_RUNNER)
    return {
        "settings": str(targets[0]),
        "update_script": _UPDATE_SCRIPT_NAME,
        "suggested_job": {
            "name": "erga-auto-update",
            "schedule": "*/15 * * * *",
            "script": _UPDATE_SCRIPT_NAME,
            "no_agent": True,
        },
    }


def synchronize_router_plugin(*, checkout_root: Path, hermes_home: Path) -> bool:
    """Refresh an installed Erga Hermes plugin from the verified checkout without Git metadata."""
    source = (
        checkout_root.expanduser().resolve() / "integrations/hermes/plugins" / _ROUTER_PLUGIN_NAME
    )
    target = hermes_home.expanduser().resolve() / "plugins" / _ROUTER_PLUGIN_NAME
    if not target.exists():
        return False
    if source.is_symlink() or target.is_symlink() or not source.is_dir() or not target.is_dir():
        raise RuntimeError("Erga's Hermes plugin path is not a regular directory")
    for root in (source, target):
        manifest = root / "plugin.yaml"
        if manifest.is_symlink() or not manifest.is_file():
            raise RuntimeError("Erga's Hermes plugin manifest is missing or unsafe")
        if f"name: {_ROUTER_PLUGIN_NAME}" not in manifest.read_text(encoding="utf-8"):
            raise RuntimeError("Erga's Hermes plugin manifest has an unexpected name")

    changed = False
    for name in _ROUTER_PLUGIN_FILES:
        source_file = source / name
        target_file = target / name
        if source_file.is_symlink() or not source_file.is_file():
            raise RuntimeError(f"Erga's checkout has an unsafe plugin file: {name}")
        content = source_file.read_bytes()
        if (
            target_file.is_file()
            and not target_file.is_symlink()
            and target_file.read_bytes() == content
        ):
            continue
        if target_file.is_symlink():
            raise RuntimeError(f"Erga's installed plugin has an unsafe file: {name}")
        with tempfile.NamedTemporaryFile(dir=target, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(target_file)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        changed = True
    return changed


def request_hermes_gateway_restart(
    *,
    hermes_home: Path,
    launcher: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> bool:
    """Request a detached profile-scoped gateway restart after plugin files change."""
    executable = shutil.which("hermes")
    if executable is None:
        return False
    environment = os.environ.copy()
    for variable in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(variable, None)
    environment["HERMES_HOME"] = str(hermes_home.expanduser().resolve())
    launcher(
        [executable, "gateway", "restart"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        start_new_session=os.name != "nt",
    )
    return True
