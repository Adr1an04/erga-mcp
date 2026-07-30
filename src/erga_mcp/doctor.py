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
    if config.tracker.enabled:
        assert config.tracker.tracker_dir is not None
        config.tracker.tracker_dir.mkdir(parents=True, exist_ok=True)
        checks["tracker"] = "ok"
    else:
        warnings["tracker"] = "disabled"
    if config.resume.master_path is None:
        warnings["resume_master"] = "not configured"
    elif not config.resume.master_path.is_file():
        warnings["resume_master"] = "missing"
    else:
        checks["resume_master"] = "ok"
    if config.mail_provider == "gmail":
        if shutil.which("gws") is None:
            warnings["gmail"] = (
                "gws unavailable; install and authorize the Hermes Google Workspace skill"
            )
        else:
            checks["gmail"] = "gws available; verify Google Workspace OAuth before syncing"
    if config.resume.template_path is None:
        warnings["resume_template"] = "not configured"
    elif not config.resume.template_path.is_file():
        warnings["resume_template"] = "missing"
    else:
        checks["resume_template"] = "ok"
    if config.resume.reference_path is not None:
        if config.resume.reference_path.is_file():
            checks["resume_reference"] = "ok"
        else:
            warnings["resume_reference"] = "missing"
    try:
        resolve_latexmk_executable(Path(config.resume.latexmk))
    except FileNotFoundError:
        warnings["latexmk"] = "unavailable"
    else:
        checks["latexmk"] = "ok"
    return DoctorReport(core_ready=True, checks=checks, warnings=warnings)
