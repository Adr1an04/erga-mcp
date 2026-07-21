from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = """# Erga MCP stores private state outside this repository.

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
# The filename of every generated local PDF. Configure a real candidate name locally.
output_pdf_name = "Firstname_Lastname_Resume.pdf"
latexmk = "latexmk"

[mail]
# Provider selection is explicit; each connector is independently authorized.
provider = "zoho"
# Optional executable name/path for the Google Workspace CLI when provider = "gmail".
gws_command = "gws"
# Non-secret Zoho OAuth client identifier used by scheduled mail sync.
client_id = ""
accounts_url = "https://accounts.zoho.com"
folder = "Job Applications"

[tracking]
# Optional Markdown/Obsidian tracker. Keep disabled when no tracker provider is wanted.
enabled = false
tracker_dir = ""
# Explicit recruiting cycles eligible for acknowledgement-based tracker imports.
active_cycles = []

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
    output_pdf_name: str
    latexmk: str


@dataclass(frozen=True)
class TrackerSettings:
    enabled: bool
    tracker_dir: Path | None
    active_cycles: tuple[str, ...]


@dataclass(frozen=True)
class ErgaConfig:
    config_path: Path
    data_dir: Path
    vault_path: Path | None
    resume: ResumeSettings
    tracker: TrackerSettings
    mail_provider: str
    gws_command: str
    mail_client_id: str
    mail_accounts_url: str
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
    output_pdf_name = str(resume.get("output_pdf_name", "Firstname_Lastname_Resume.pdf")).strip()
    if Path(output_pdf_name).name != output_pdf_name or not output_pdf_name.endswith(".pdf"):
        raise ValueError("resume output_pdf_name must be a PDF filename without path components")
    return ResumeSettings(
        template_path=template_path,
        editable_sections=tuple(item.strip() for item in editable_sections_value),
        bullet_min_chars=bullet_lengths[0],
        bullet_target_chars=bullet_lengths[1],
        bullet_max_chars=bullet_lengths[2],
        max_pages=max_pages,
        output_root=_path(str(resume.get("output_root", "output")), base_dir),
        output_pdf_name=output_pdf_name,
        latexmk=latexmk,
    )


def load_config(config_path: Path) -> ErgaConfig:
    """Load a local-only configuration file without reading any credentials."""
    config_path = config_path.expanduser().absolute()
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = _section(document, "paths")
    mail = _section(document, "mail")
    tracking = _section(document, "tracking")
    privacy = _section(document, "privacy")

    data_dir = _path(str(paths.get("data_dir", "state")), config_path.parent)
    vault_value = str(paths.get("vault_path", "")).strip()
    vault_path = _path(vault_value, config_path.parent) if vault_value else None
    tracker_value = str(tracking.get("tracker_dir", "")).strip()
    tracker_dir = _path(tracker_value, config_path.parent) if tracker_value else None
    tracker_enabled = bool(tracking.get("enabled", False))
    active_cycles_value = tracking.get("active_cycles", [])
    if not isinstance(active_cycles_value, list) or any(
        not isinstance(cycle, str)
        or re.fullmatch(r"(?:Fall|Spring)\s+\d{4}", " ".join(cycle.split()), re.IGNORECASE) is None
        for cycle in active_cycles_value
    ):
        raise ValueError("tracking active_cycles must be a list of Fall YYYY or Spring YYYY values")
    active_cycles = tuple(" ".join(cycle.split()) for cycle in active_cycles_value)
    if tracker_enabled and tracker_dir is None:
        raise ValueError("tracking tracker_dir must be configured when tracking is enabled")

    mail_provider = str(mail.get("provider", "zoho")).strip().casefold()
    if mail_provider not in {"zoho", "gmail"}:
        raise ValueError("mail provider must be zoho or gmail")
    mail_accounts_url = str(mail.get("accounts_url", "https://accounts.zoho.com")).strip()
    if not mail_accounts_url.startswith("https://"):
        raise ValueError("mail accounts_url must use HTTPS")

    return ErgaConfig(
        config_path=config_path,
        data_dir=data_dir,
        vault_path=vault_path,
        resume=_resume_settings(document, config_path.parent),
        tracker=TrackerSettings(
            enabled=tracker_enabled, tracker_dir=tracker_dir, active_cycles=active_cycles
        ),
        mail_provider=mail_provider,
        gws_command=str(mail.get("gws_command", "gws")).strip() or "gws",
        mail_client_id=str(mail.get("client_id", "")).strip(),
        mail_accounts_url=mail_accounts_url.rstrip("/"),
        mail_folder=str(mail.get("folder", "Job Applications")),
        retain_message_bodies=bool(privacy.get("retain_message_bodies", False)),
        retain_attachments=bool(privacy.get("retain_attachments", False)),
    )
