from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """# Recruiting Pipeline stores private state outside this repository.

[paths]
# Relative paths resolve from this file's directory.
data_dir = "state"
vault_path = ""

[mail]
# This is a label only. OAuth consent and credentials are configured separately.
folder = "Job Applications"

[privacy]
# Keep full message bodies and attachments disabled unless a user explicitly enables them.
retain_message_bodies = false
retain_attachments = false
"""


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    data_dir: Path
    vault_path: Path | None
    mail_folder: str
    retain_message_bodies: bool
    retain_attachments: bool


def _path(value: str, base_dir: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base_dir / candidate


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def load_config(config_path: Path) -> PipelineConfig:
    """Load a local-only configuration file without reading any credentials."""
    config_path = config_path.expanduser().absolute()
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = _section(document, "paths")
    mail = _section(document, "mail")
    privacy = _section(document, "privacy")

    data_dir = _path(str(paths.get("data_dir", "state")), config_path.parent)
    vault_value = str(paths.get("vault_path", "")).strip()
    vault_path = _path(vault_value, config_path.parent) if vault_value else None

    return PipelineConfig(
        config_path=config_path,
        data_dir=data_dir,
        vault_path=vault_path,
        mail_folder=str(mail.get("folder", "Job Applications")),
        retain_message_bodies=bool(privacy.get("retain_message_bodies", False)),
        retain_attachments=bool(privacy.get("retain_attachments", False)),
    )
