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
# The master is approved factual knowledge; the reference contributes layout metadata only.
# template_path may be omitted; Erga generates a private standalone LaTeX template from the master.
master_path = ""
template_path = ""
reference_path = ""
editable_sections = []
bullet_min_chars = 0
bullet_target_chars = 0
bullet_max_chars = 0
max_pages = 0
# For a one-page resume, require rendered text to occupy at least this fraction of the page height.
# Erga adds supported bullets first, then spaces only the remaining gap; it never invents filler.
minimum_page_fill_ratio = 0.82
output_root = "output"
# Local JSON arsenal of approved LaTeX project blocks. Onboarding creates and requires one
# so intake cannot silently fall back to reordering only the template's existing projects.
project_inventory_path = ""
project_selection_mode = "inventory_optional"
project_count = 4
# Per-project bullet counts are selected automatically from rendered page density.
# Compatibility key retained for existing local configs. Lead-verb uniqueness is always required
# for generated resumes, including configs created before this became a pipeline invariant.
require_unique_lead_verbs = true
# The filename of every generated local PDF. Configure a real candidate name locally.
output_pdf_name = "Firstname_Lastname_Resume.pdf"
latexmk = "latexmk"

[cover_letter]
# Template contains exactly one {{BODY}} marker. The writing sample is style-only.
# A relative writing_sample_path resolves from vault_path when configured.
template_path = ""
writing_sample_path = ""

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
# Optional Obsidian tracker projection. Both `erga setup` and `erga init` may leave it disabled.
enabled = false
tracker_dir = ""
# Explicit recruiting cycles eligible for acknowledgement-based tracker imports.
active_cycles = []

[contacts]
# Optional contact projections. Each output is an explicit local sink, such as Obsidian.
outputs = []

[privacy]
# Keep full message bodies and attachments disabled unless a user explicitly enables them.
retain_message_bodies = false
retain_attachments = false

[mcp]
# Tool profiles are capability boundaries, not credentials. The default preserves every legacy tool.
# Choose career, career-private, read, research, write, or hermes for a narrower MCP client surface.
tool_profile = "default"
"""


@dataclass(frozen=True)
class ResumeSettings:
    master_path: Path | None
    template_path: Path | None
    reference_path: Path | None
    editable_sections: tuple[str, ...]
    bullet_min_chars: int
    bullet_target_chars: int
    bullet_max_chars: int
    max_pages: int
    minimum_page_fill_ratio: float
    output_root: Path
    project_inventory_path: Path | None
    project_selection_mode: str
    project_count: int
    require_unique_lead_verbs: bool
    output_pdf_name: str
    latexmk: str


@dataclass(frozen=True)
class CoverLetterSettings:
    template_path: Path | None
    writing_sample_path: Path | None


@dataclass(frozen=True)
class TrackerSettings:
    enabled: bool
    tracker_dir: Path | None
    active_cycles: tuple[str, ...]


@dataclass(frozen=True)
class ContactOutputSettings:
    kind: str
    directory: Path


@dataclass(frozen=True)
class McpSettings:
    tool_profile: str


@dataclass(frozen=True)
class ErgaConfig:
    config_path: Path
    data_dir: Path
    vault_path: Path | None
    resume: ResumeSettings
    cover_letter: CoverLetterSettings
    tracker: TrackerSettings
    contact_outputs: tuple[ContactOutputSettings, ...]
    mail_provider: str
    gws_command: str
    mail_client_id: str
    mail_accounts_url: str
    mail_folder: str
    retain_message_bodies: bool
    retain_attachments: bool
    mcp: McpSettings


def _path(value: str, base_dir: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base_dir / candidate


def _section(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def validate_output_pdf_name(value: str) -> str:
    """Reject path-like values so attachment names remain inside their package."""
    name = value.strip()
    if (
        not name
        or not name.endswith(".pdf")
        or name == ".pdf"
        or "/" in name
        or "\\" in name
        or Path(name).name != name
    ):
        raise ValueError("resume output_pdf_name must be a PDF filename without path components")
    return name


def _resume_settings(document: dict[str, Any], base_dir: Path) -> ResumeSettings:
    resume = _section(document, "resume")
    master_value = str(resume.get("master_path", "")).strip()
    master_path = _path(master_value, base_dir) if master_value else None
    template_value = str(resume.get("template_path", "")).strip()
    template_path = _path(template_value, base_dir) if template_value else None
    reference_value = str(resume.get("reference_path", "")).strip()
    reference_path = _path(reference_value, base_dir) if reference_value else None
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
    inventory_value = str(resume.get("project_inventory_path", "")).strip()
    project_inventory_path = _path(inventory_value, base_dir) if inventory_value else None
    project_selection_mode = str(resume.get("project_selection_mode", "inventory_optional")).strip()
    if project_selection_mode not in {"inventory_optional", "inventory_required", "template_only"}:
        raise ValueError(
            "resume project_selection_mode must be inventory_optional, inventory_required, "
            "or template_only"
        )
    if project_selection_mode == "inventory_required" and project_inventory_path is None:
        raise ValueError(
            "resume project_inventory_path must be configured when project_selection_mode "
            "is inventory_required"
        )
    project_count = int(resume.get("project_count", 4))
    if project_count < 1:
        raise ValueError("resume project_count must be positive")
    minimum_page_fill_ratio = float(resume.get("minimum_page_fill_ratio", 0.82))
    if not 0 <= minimum_page_fill_ratio <= 1:
        raise ValueError("resume minimum_page_fill_ratio must be between zero and one")
    # Generated bullets may never repeat lead verbs. Treat the legacy false value as an old
    # config default rather than an opt-out so existing installations receive the invariant.
    require_unique_lead_verbs = True
    latexmk = str(resume.get("latexmk", "latexmk")).strip()
    if not latexmk:
        raise ValueError("resume latexmk must be non-empty")
    output_pdf_name = validate_output_pdf_name(
        str(resume.get("output_pdf_name", "Firstname_Lastname_Resume.pdf"))
    )
    return ResumeSettings(
        master_path=master_path,
        template_path=template_path,
        reference_path=reference_path,
        editable_sections=tuple(item.strip() for item in editable_sections_value),
        bullet_min_chars=bullet_lengths[0],
        bullet_target_chars=bullet_lengths[1],
        bullet_max_chars=bullet_lengths[2],
        max_pages=max_pages,
        minimum_page_fill_ratio=minimum_page_fill_ratio,
        output_root=_path(str(resume.get("output_root", "output")), base_dir),
        project_inventory_path=project_inventory_path,
        project_selection_mode=project_selection_mode,
        project_count=project_count,
        require_unique_lead_verbs=require_unique_lead_verbs,
        output_pdf_name=output_pdf_name,
        latexmk=latexmk,
    )


def load_config(config_path: Path) -> ErgaConfig:
    """Load a local-only configuration file without reading any credentials."""
    config_path = config_path.expanduser().absolute()
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    paths = _section(document, "paths")
    mail = _section(document, "mail")
    cover_letter = _section(document, "cover_letter")
    tracking = _section(document, "tracking")
    contacts = _section(document, "contacts")
    privacy = _section(document, "privacy")
    mcp = _section(document, "mcp")

    data_dir = _path(str(paths.get("data_dir", "state")), config_path.parent)
    vault_value = str(paths.get("vault_path", "")).strip()
    vault_path = _path(vault_value, config_path.parent) if vault_value else None
    cover_letter_template = str(cover_letter.get("template_path", "")).strip()
    cover_letter_sample = str(cover_letter.get("writing_sample_path", "")).strip()
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

    contact_outputs_value = contacts.get("outputs", [])
    if not isinstance(contact_outputs_value, list):
        raise ValueError("contacts outputs must be a list")
    contact_outputs: list[ContactOutputSettings] = []
    for output in contact_outputs_value:
        if not isinstance(output, dict):
            raise ValueError("each contacts output must be a table")
        kind = str(output.get("kind", "")).strip().casefold()
        directory_value = str(output.get("directory", "")).strip()
        if kind != "obsidian" or not directory_value:
            raise ValueError("contacts outputs currently require kind = 'obsidian' and a directory")
        if vault_path is None:
            raise ValueError("paths vault_path must be configured for an Obsidian contacts output")
        directory = _path(directory_value, vault_path)
        contact_outputs.append(ContactOutputSettings(kind=kind, directory=directory))

    mail_provider = str(mail.get("provider", "zoho")).strip().casefold()
    if mail_provider not in {"zoho", "gmail"}:
        raise ValueError("mail provider must be zoho or gmail")
    mail_accounts_url = str(mail.get("accounts_url", "https://accounts.zoho.com")).strip()
    if not mail_accounts_url.startswith("https://"):
        raise ValueError("mail accounts_url must use HTTPS")
    mcp_tool_profile = str(mcp.get("tool_profile", "default")).strip().casefold()
    if mcp_tool_profile not in {
        "career",
        "career-private",
        "default",
        "read",
        "research",
        "write",
        "hermes",
    }:
        raise ValueError(
            "mcp tool_profile must be career, career-private, default, read, research, write, "
            "or hermes"
        )

    return ErgaConfig(
        config_path=config_path,
        data_dir=data_dir,
        vault_path=vault_path,
        resume=_resume_settings(document, config_path.parent),
        cover_letter=CoverLetterSettings(
            template_path=_path(cover_letter_template, config_path.parent)
            if cover_letter_template
            else None,
            writing_sample_path=_path(
                cover_letter_sample, vault_path if vault_path is not None else config_path.parent
            )
            if cover_letter_sample
            else None,
        ),
        tracker=TrackerSettings(
            enabled=tracker_enabled, tracker_dir=tracker_dir, active_cycles=active_cycles
        ),
        contact_outputs=tuple(contact_outputs),
        mail_provider=mail_provider,
        gws_command=str(mail.get("gws_command", "gws")).strip() or "gws",
        mail_client_id=str(mail.get("client_id", "")).strip(),
        mail_accounts_url=mail_accounts_url.rstrip("/"),
        mail_folder=str(mail.get("folder", "Job Applications")),
        retain_message_bodies=bool(privacy.get("retain_message_bodies", False)),
        retain_attachments=bool(privacy.get("retain_attachments", False)),
        mcp=McpSettings(tool_profile=mcp_tool_profile),
    )
