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

[resume]
# Configure these per template. Empty/zero values mean no constraint has been selected yet.
template_path = ""
editable_sections = []
bullet_min_chars = 0
bullet_target_chars = 0
bullet_max_chars = 0
max_pages = 0
output_root = "output"
latexmk = "latexmk"

[mail]
# This is a label only. OAuth consent and credentials are configured separately.
folder = "Job Applications"

[privacy]
# Keep full message bodies and attachments disabled unless a user explicitly enables them.
retain_message_bodies = false
retain_attachments = false
"""


@dataclass(frozen=True)
class ResumeSettings:
    template_path: Path | None
    editable_sections: tuple[str, ...]
    bullet_min_chars: int
    bullet_target_chars: int
    bullet_max_chars: int
    max_pages: int
    output_root: Path
    latexmk: str


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    data_dir: Path
    vault_path: Path | None
    resume: ResumeSettings
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


def _resume_settings(document: dict[str, Any], base_dir: Path) -> ResumeSettings:
    resume = _section(document, "resume")
    template_value = str(resume.get("template_path", "")).strip()
    template_path = _path(template_value, base_dir) if template_value else None
    editable_sections_value = resume.get("editable_sections", [])
    if not isinstance(editable_sections_value, list) or any(
        not isinstance(item, str) or not item.strip() for item in editable_sections_value
    ):
        raise ValueError("resume editable_sections must be a list of non-empty strings")
    bullet_lengths = tuple(
        int(resume.get(name, 0))
        for name in ("bullet_min_chars", "bullet_target_chars", "bullet_max_chars")
    )
    configured_bullet_lengths = any(bullet_lengths)
    ordered_bullet_lengths = 0 < bullet_lengths[0] <= bullet_lengths[1] <= bullet_lengths[2]
    if any(value < 0 for value in bullet_lengths) or (
        configured_bullet_lengths and not ordered_bullet_lengths
    ):
        raise ValueError("resume bullet character lengths must be zero or ordered positive values")
    max_pages = int(resume.get("max_pages", 0))
    if max_pages < 0:
        raise ValueError("resume max_pages must be zero or positive")
    latexmk = str(resume.get("latexmk", "latexmk")).strip()
    if not latexmk:
        raise ValueError("resume latexmk must be non-empty")
    return ResumeSettings(
        template_path=template_path,
        editable_sections=tuple(item.strip() for item in editable_sections_value),
        bullet_min_chars=bullet_lengths[0],
        bullet_target_chars=bullet_lengths[1],
        bullet_max_chars=bullet_lengths[2],
        max_pages=max_pages,
        output_root=_path(str(resume.get("output_root", "output")), base_dir),
        latexmk=latexmk,
    )


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
        resume=_resume_settings(document, config_path.parent),
        mail_folder=str(mail.get("folder", "Job Applications")),
        retain_message_bodies=bool(privacy.get("retain_message_bodies", False)),
        retain_attachments=bool(privacy.get("retain_attachments", False)),
    )
