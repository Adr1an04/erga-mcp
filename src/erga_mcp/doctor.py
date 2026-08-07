from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .resume import resolve_latexmk_executable
from .store import ErgaStore


@dataclass(frozen=True)
class DoctorReport:
    core_ready: bool
    checks: dict[str, str]
    warnings: dict[str, str]


def check_installation(config_path: Path) -> DoctorReport:
    """Check core local state and report optional integration availability."""
    config = load_config(config_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    ErgaStore(config.data_dir / "erga.sqlite3").initialize()
    checks = {
        "config": "ok",
        "data_dir": "ok",
        "database": "ok",
    }
    warnings: dict[str, str] = {}
    core_ready = True
    if config.vault_path is None:
        warnings["obsidian"] = "optional vault not configured"
    elif not config.vault_path.is_dir():
        warnings["obsidian"] = "configured vault is missing"
    else:
        checks["obsidian"] = "ok"
    if config.tracker.enabled:
        assert config.tracker.tracker_dir is not None
        config.tracker.tracker_dir.mkdir(parents=True, exist_ok=True)
        checks["tracker"] = "ok"
    else:
        warnings["tracker"] = "optional Obsidian tracker not configured"
    if config.resume.master_path is None:
        warnings["master_resume"] = "not configured; run `erga setup`"
        core_ready = False
    elif not config.resume.master_path.is_file():
        warnings["master_resume"] = "managed source is missing"
        core_ready = False
    else:
        checks["master_resume"] = "ok"
    if config.mail_provider == "gmail":
        if shutil.which("gws") is None:
            warnings["gmail"] = (
                "gws unavailable; install and authorize the Hermes Google Workspace skill"
            )
        else:
            checks["gmail"] = "gws available; verify Google Workspace OAuth before syncing"
    if config.resume.template_path is None:
        if config.resume.master_path is not None and config.resume.master_path.is_file():
            checks["resume_template"] = "will be generated from approved master on first use"
        else:
            warnings["resume_template"] = "master resume required"
    elif not config.resume.template_path.is_file():
        warnings["resume_template"] = "missing"
    else:
        checks["resume_template"] = "ok"
    try:
        resolve_latexmk_executable(Path(config.resume.latexmk))
    except FileNotFoundError:
        warnings["latexmk"] = "unavailable"
    else:
        checks["latexmk"] = "ok"
    return DoctorReport(core_ready=core_ready, checks=checks, warnings=warnings)
