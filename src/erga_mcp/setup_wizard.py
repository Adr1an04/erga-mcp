"""Interactive, client-neutral setup for Erga's local core."""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

import questionary
from questionary import Choice

from .config import DEFAULT_CONFIG, load_config
from .private_files import restrict_private_directory, restrict_private_file
from .resume_settings import update_settings
from .resume_sources import (
    SUPPORTED_RESUME_SUFFIXES,
    import_master_resume,
    load_resume_source,
    snapshot_resume_source,
)
from .store import ErgaStore

VaultMode = Literal["existing", "new"]
_ERGA_VAULT_DIRECTORY = "Erga"
_TRACKER_DIRECTORY = "Applications"
_OUTPUT_DIRECTORY = "Generated Resumes"
_START_NOTE = """# Welcome to Erga

Erga keeps career knowledge, application notes, and résumé work under your control.

## What was configured

- Your master résumé was copied into private, hash-verified Erga storage.
- This optional Obsidian vault is a human-readable view of your Erga workspace.
- Erga's local MCP server is ready without a separate model API key.

## Optional connections

Coding assistants, Obsidian, and chat bridges are optional ways to work with the same local Erga
system. Add only the connections and projections you want.

Erga never submits applications, sends messages, or invents résumé claims.
"""


class WizardCancelled(RuntimeError):
    """Raised when setup exits before the final confirmation."""


@dataclass(frozen=True)
class CoreSetupSelections:
    config_path: Path
    master_resume: Path
    style_resume: Path | None = None
    output_root: Path | None = None
    obsidian_enabled: bool = False
    vault_mode: VaultMode | None = None
    vault_path: Path | None = None


@dataclass(frozen=True)
class CoreSetupReport:
    status: str
    config_path: str
    data_dir: str
    vault_path: str | None
    tracker_dir: str | None
    output_root: str
    master_sha256: str
    style_configured: bool
    obsidian_configured: bool
    welcome_note_created: bool
    completed: list[str]
    next_steps: list[str]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def _required(value: object) -> object:
    if value is None:
        raise WizardCancelled("Setup cancelled; no changes were made.")
    return value


def normalize_dropped_path(value: str) -> Path:
    """Normalize quoted or shell-escaped paths inserted by terminal file drops."""
    entered = value.strip()
    if len(entered) >= 2 and entered[0] == entered[-1] and entered[0] in {'"', "'"}:
        entered = entered[1:-1]
    if os.name != "nt":
        try:
            parsed = shlex.split(entered)
        except ValueError:
            parsed = []
        if len(parsed) == 1:
            entered = parsed[0]
    return Path(entered).expanduser().absolute()


def _existing_directory(value: str) -> bool | str:
    return (
        True
        if normalize_dropped_path(value).is_dir()
        else "Drag or enter an existing Obsidian vault folder."
    )


def _new_vault_directory(value: str) -> bool | str:
    path = normalize_dropped_path(value)
    if path.exists() and not path.is_dir():
        return "The new vault path must be a directory."
    if not path.parent.is_dir():
        return "Choose a location whose parent directory already exists."
    return True


def _resume_file(value: str) -> bool | str:
    path = normalize_dropped_path(value)
    if not path.is_file():
        return "Drag an existing resume file into this window."
    if path.suffix.casefold() not in SUPPORTED_RESUME_SUFFIXES:
        return "Use a PDF, DOCX, or LaTeX (.tex) resume."
    return True


def _output_directory(value: str) -> bool | str:
    path = normalize_dropped_path(value)
    if path.exists() and not path.is_dir():
        return "The resume output path must be a directory."
    if not path.parent.is_dir():
        return "Choose a location whose parent directory already exists."
    return True


def collect_core_setup_selections(
    *,
    default_config_path: Path,
    default_vault_path: Path | None = None,
) -> CoreSetupSelections:
    """Collect and review core choices before writing any local state."""
    questionary.print("\nErga Setup", style="bold fg:#7c5cff")
    questionary.print(
        "Set up Erga's private state, resume knowledge, application tracking, and local MCP "
        "server.\nObsidian, coding assistants, and chat bridges are optional additions.",
        style="fg:#aaaaaa",
    )
    questionary.print(
        "\nResume knowledge\n"
        "Your master resume is Erga's source of facts. PDF, DOCX, and .tex files all work here. "
        "Erga reads every page, creates a private hash-verified copy, and never modifies the "
        "original.\n"
        "To generate compiled LaTeX resumes later, you can add an editable .tex template after "
        "setup.",
        style="fg:#e0aa55",
    )
    master_resume = normalize_dropped_path(
        str(
            _required(
                questionary.text(
                    "Drop your master resume (PDF, DOCX, or .tex) here:",
                    validate=_resume_file,
                ).ask()
            )
        )
    )
    questionary.print(
        "\nResume layout (optional)\n"
        "Erga can use a separate resume or template only as a layout reference. It will not use "
        "that file for factual claims. You can change this later.",
        style="fg:#aaaaaa",
    )
    style_resume: Path | None = None
    if bool(
        _required(
            questionary.confirm(
                "Use a separate resume or template as the layout reference?",
                default=False,
            ).ask()
        )
    ):
        style_resume = normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Drop a resume or template whose layout you like here:",
                        validate=_resume_file,
                    ).ask()
                )
            )
        )

    obsidian_enabled = bool(
        _required(
            questionary.confirm(
                "Do you already use Obsidian and want an optional workspace folder?",
                default=False,
            ).ask()
        )
    )
    vault_mode: VaultMode | None = None
    vault_path: Path | None = None
    if obsidian_enabled:
        vault_mode = cast(
            VaultMode,
            _required(
                questionary.select(
                    "How should Erga configure Obsidian?",
                    choices=[
                        Choice("I already have an Obsidian folder (vault)", value="existing"),
                        Choice("Create a new folder for Obsidian", value="new"),
                    ],
                    default="existing",
                    use_shortcuts=True,
                ).ask()
            ),
        )
        suggested_vault = (
            (default_vault_path or Path.home() / "Documents" / "Erga Vault").expanduser().absolute()
        )
        if vault_mode == "existing":
            vault_path = normalize_dropped_path(
                str(
                    _required(
                        questionary.text(
                            "Drag your Obsidian vault folder here:",
                            default=str(suggested_vault) if suggested_vault.is_dir() else "",
                            validate=_existing_directory,
                        ).ask()
                    )
                )
            )
        else:
            vault_path = normalize_dropped_path(
                str(
                    _required(
                        questionary.text(
                            "New Obsidian vault location:",
                            default=str(suggested_vault),
                            validate=_new_vault_directory,
                        ).ask()
                    )
                )
            )

    recommended_output = (
        vault_path / _ERGA_VAULT_DIRECTORY / _OUTPUT_DIRECTORY
        if vault_path is not None
        else default_config_path.expanduser().absolute().parent / "generated-resumes"
    )
    output_root = recommended_output
    if not bool(
        _required(
            questionary.confirm(
                f"Store generated resume packages in {recommended_output}?",
                default=True,
            ).ask()
        )
    ):
        output_root = normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Resume output directory:",
                        validate=_output_directory,
                    ).ask()
                )
            )
        )

    selections = CoreSetupSelections(
        config_path=default_config_path.expanduser().absolute(),
        master_resume=master_resume,
        style_resume=style_resume,
        output_root=output_root,
        obsidian_enabled=obsidian_enabled,
        vault_mode=vault_mode,
        vault_path=vault_path,
    )
    questionary.print("\nReview", style="bold")
    questionary.print(render_core_setup_review(selections))
    if not bool(_required(questionary.confirm("Apply this core setup?", default=True).ask())):
        raise WizardCancelled("Setup cancelled; no changes were made.")
    return selections


def render_core_setup_review(selections: CoreSetupSelections) -> str:
    """Render a plain-language review before setup writes private local state."""
    obsidian = "not set up"
    if selections.obsidian_enabled:
        action = "create" if selections.vault_mode == "new" else "use"
        obsidian = f"{action} the Obsidian folder at {selections.vault_path}"
    style = (
        f"use {selections.style_resume.name} as a style reference"
        if selections.style_resume is not None
        else "use Erga's clean one-page layout (recommended)"
    )
    return "\n".join(
        [
            "What Erga will set up",
            "",
            f"  Your master resume: copied privately from {selections.master_resume}",
            f"  Resume style: {style}",
            f"  Generated resumes: {selections.output_root}",
            "  Application tracking: a private local database",
            f"  Obsidian: {obsidian}",
            "",
            "Not being connected: coding AI, Discord, mail, or any model API key.",
            "You can change these settings later. You can cancel now with no changes.",
        ]
    )


def write_core_setup_plan(selections: CoreSetupSelections) -> str:
    """Return a machine-readable dry-run plan without personal file contents."""
    payload = asdict(selections)
    for key in ("config_path", "vault_path", "master_resume", "style_resume", "output_root"):
        value = payload[key]
        payload[key] = str(value) if value is not None else None
    return json.dumps(payload, indent=2, sort_keys=True)


def _replace_table(raw: str, name: str, lines: list[str]) -> str:
    table = "\n".join([f"[{name}]", *lines])
    pattern = rf"(?ms)^\[{re.escape(name)}\]\n.*?(?=^\[|\Z)"
    if re.search(pattern, raw):
        return re.sub(pattern, lambda _match: f"{table}\n\n", raw)
    separator = "" if not raw or raw.endswith("\n\n") else "\n"
    return f"{raw}{separator}{table}\n"


def _atomic_write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(path.parent)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}-",
        delete=False,
    ) as temporary:
        temporary.write(text)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        restrict_private_file(temporary_path)
        temporary_path.replace(path)
        restrict_private_file(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _configure_core_paths(
    *,
    config_path: Path,
    vault_path: Path | None,
    tracker_dir: Path | None,
) -> None:
    if config_path.exists():
        existing = load_config(config_path)
        raw = config_path.read_text(encoding="utf-8")
        data_dir = existing.data_dir
        active_cycles = list(existing.tracker.active_cycles)
    else:
        raw = DEFAULT_CONFIG
        data_dir = config_path.parent / "state"
        active_cycles = []
    raw = _replace_table(
        raw,
        "paths",
        [
            f"data_dir = {json.dumps(str(data_dir))}",
            f"vault_path = {json.dumps(str(vault_path) if vault_path is not None else '')}",
        ],
    )
    raw = _replace_table(
        raw,
        "tracking",
        [
            f"enabled = {'true' if tracker_dir is not None else 'false'}",
            f"tracker_dir = {json.dumps(str(tracker_dir) if tracker_dir is not None else '')}",
            f"active_cycles = {json.dumps(active_cycles)}",
        ],
    )
    raw = _replace_table(raw, "mcp", ['tool_profile = "career"'])
    tomllib.loads(raw)
    _atomic_write_private(config_path, raw)
    load_config(config_path)


def _write_start_note(path: Path) -> bool:
    if path.exists():
        if not path.is_file():
            raise ValueError(f"Erga start note path is not a regular file: {path}")
        return False
    path.write_text(_START_NOTE, encoding="utf-8")
    return True


def apply_core_setup(selections: CoreSetupSelections) -> CoreSetupReport:
    """Initialize the complete local core without requiring an external reasoning host."""
    master = load_resume_source(selections.master_resume)
    style = (
        load_resume_source(selections.style_resume) if selections.style_resume is not None else None
    )
    vault_path: Path | None = None
    tracker_dir: Path | None = None
    erga_vault_dir: Path | None = None
    if selections.obsidian_enabled:
        if selections.vault_mode is None or selections.vault_path is None:
            raise ValueError("Obsidian setup requires a vault action and path")
        vault_path = selections.vault_path.expanduser().absolute()
        if selections.vault_mode == "existing" and not vault_path.is_dir():
            raise NotADirectoryError(f"Obsidian vault does not exist: {vault_path}")
        if vault_path.exists() and not vault_path.is_dir():
            raise NotADirectoryError(f"Obsidian vault path is not a directory: {vault_path}")
        vault_path.mkdir(parents=selections.vault_mode == "new", exist_ok=True)
        erga_vault_dir = vault_path / _ERGA_VAULT_DIRECTORY
        tracker_dir = erga_vault_dir / _TRACKER_DIRECTORY
        tracker_dir.mkdir(parents=True, exist_ok=True)
    elif selections.vault_mode is not None or selections.vault_path is not None:
        raise ValueError("Obsidian vault choices require obsidian_enabled=true")

    output_root = (
        selections.output_root.expanduser().absolute()
        if selections.output_root is not None
        else (
            erga_vault_dir / _OUTPUT_DIRECTORY
            if erga_vault_dir is not None
            else selections.config_path.expanduser().absolute().parent / "generated-resumes"
        )
    )
    output_root.mkdir(parents=True, exist_ok=True)
    _configure_core_paths(
        config_path=selections.config_path,
        vault_path=vault_path,
        tracker_dir=tracker_dir,
    )

    config = load_config(selections.config_path)
    store = ErgaStore(config.data_dir / "erga.sqlite3")
    store.initialize()
    managed_master = snapshot_resume_source(master, data_dir=config.data_dir, role="master")
    managed_style = (
        snapshot_resume_source(style, data_dir=config.data_dir, role="style")
        if style is not None
        else None
    )
    evidence = import_master_resume(
        store,
        managed_master,
        source_name=master.path.name,
    )
    current_resume = config.resume
    update_settings(
        selections.config_path,
        {
            "master_path": str(managed_master.path),
            "reference_path": str(managed_style.path) if managed_style is not None else "",
            "output_root": str(output_root),
            "max_pages": (
                managed_style.page_count
                if managed_style is not None and managed_style.page_count
                else current_resume.max_pages or 1
            ),
        },
    )
    welcome_note_created = (
        _write_start_note(erga_vault_dir / "Start Here.md") if erga_vault_dir is not None else False
    )

    completed = [
        "Private Erga configuration and database",
        "Private local application tracking",
        "Managed master resume knowledge",
        "Client-neutral local MCP profile",
    ]
    if managed_style is not None:
        completed.append("Managed resume style preference")
    if vault_path is not None:
        completed.append("Optional Obsidian workspace and tracker view")
    next_steps = [
        "Run `erga status` to confirm your local setup.",
        "Optionally connect any MCP-capable coding assistant you already use.",
        "Optionally add Obsidian, communication, or mail integrations later.",
        f"Approved master evidence is ready as {evidence.id}.",
    ]
    if erga_vault_dir is not None:
        next_steps.insert(0, "Open the vault in Obsidian and read Erga/Start Here.md.")
    return CoreSetupReport(
        status="ready",
        config_path=str(selections.config_path),
        data_dir=str(config.data_dir),
        vault_path=str(vault_path) if vault_path is not None else None,
        tracker_dir=str(tracker_dir) if tracker_dir is not None else None,
        output_root=str(output_root),
        master_sha256=managed_master.sha256,
        style_configured=managed_style is not None,
        obsidian_configured=vault_path is not None,
        welcome_note_created=welcome_note_created,
        completed=completed,
        next_steps=next_steps,
    )


def render_core_setup_report(report: CoreSetupReport) -> str:
    """Render a concise core-completion message."""
    return "\n".join(
        [
            "",
            "Erga's local core is ready.",
            "",
            *[f"  [ok] {item}" for item in report.completed],
            "",
            "No Obsidian installation, coding-AI subscription, Discord bot, or model API key "
            "was required.",
            "",
            "Next:",
            *[f"  {index}. {step}" for index, step in enumerate(report.next_steps, start=1)],
        ]
    )
