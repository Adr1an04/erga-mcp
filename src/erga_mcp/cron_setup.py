from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_SETTINGS_NAME = "erga-mcp-monitor.json"
_MAIL_SCRIPT_NAME = "erga-mcp-mail.py"
_HISTORY_SCRIPT_NAME = "erga-mcp-history.py"

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
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (settings["module_root"], existing_python_path)
        if part
    )
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


def monitor_paths(scripts_dir: Path) -> tuple[Path, Path, Path]:
    """Return the exact Erga-owned monitor files in a selected Hermes scripts directory."""
    resolved = scripts_dir.expanduser().absolute()
    return (
        resolved / _SETTINGS_NAME,
        resolved / _MAIL_SCRIPT_NAME,
        resolved / _HISTORY_SCRIPT_NAME,
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
        "module_root": str(Path(__file__).resolve().parents[1]),
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
