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

from .config import DEFAULT_CONFIG, load_config, validate_output_pdf_name
from .private_files import restrict_private_directory, restrict_private_file
from .resume_settings import update_settings
from .resume_sources import (
    SUPPORTED_RESUME_SUFFIXES,
    import_master_resume,
    load_resume_source,
    snapshot_resume_source,
)
from .store import ErgaStore
from .toml_edit import update_table

VaultMode = Literal["existing", "new"]
_ERGA_VAULT_DIRECTORY = "Erga"
_TRACKER_DIRECTORY = "Applications"
_OUTPUT_DIRECTORY = "Generated Resumes"
_RECOMMENDED_BULLET_LENGTHS = (90, 105, 120)
_MAX_STYLE_EXAMPLE_CHARS = 500
_DEFAULT_OUTPUT_PDF_NAME = "Firstname_Lastname_Resume.pdf"
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
    bullet_min_chars: int = _RECOMMENDED_BULLET_LENGTHS[0]
    bullet_target_chars: int = _RECOMMENDED_BULLET_LENGTHS[1]
    bullet_max_chars: int = _RECOMMENDED_BULLET_LENGTHS[2]
    max_pages: int | None = None
    output_root: Path | None = None
    output_pdf_name: str = _DEFAULT_OUTPUT_PDF_NAME
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


def normalize_output_pdf_name(value: str) -> str:
    """Accept a user-chosen resume label and return one safe PDF filename."""
    name = value.strip()
    if name and not name.casefold().endswith(".pdf"):
        name += ".pdf"
    elif name.casefold().endswith(".pdf"):
        name = name[:-4] + ".pdf"
    try:
        return validate_output_pdf_name(name)
    except ValueError as error:
        raise ValueError("Enter a PDF filename without path components.") from error


def _output_pdf_name(value: str) -> bool | str:
    try:
        normalize_output_pdf_name(value)
    except ValueError as error:
        return str(error)
    return True


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


def _positive_integer(value: str) -> bool | str:
    try:
        parsed = int(value.strip())
    except ValueError:
        return "Enter a positive whole number."
    return True if parsed > 0 else "Enter a positive whole number."


def _style_example(value: str, *, required: bool) -> bool | str:
    normalized = value.strip()
    if required and not normalized:
        return "Paste one representative resume bullet."
    if len(normalized) > _MAX_STYLE_EXAMPLE_CHARS:
        return f"Keep the example under {_MAX_STYLE_EXAMPLE_CHARS} characters."
    return True


def bullet_lengths_from_examples(examples: tuple[str, ...]) -> tuple[int, int, int]:
    """Derive a practical character range without retaining style-example wording."""
    raw_examples = tuple(example.strip() for example in examples if example.strip())
    if any(len(example) > _MAX_STYLE_EXAMPLE_CHARS for example in raw_examples):
        raise ValueError(f"bullet examples must be at most {_MAX_STYLE_EXAMPLE_CHARS} characters")
    normalized = tuple(
        re.sub(r"^(?:[\u2022*\-]+|\\item)\s*", "", example.strip()) for example in raw_examples
    )
    if not normalized:
        raise ValueError("at least one non-empty bullet example is required")
    lengths = tuple(len(example) for example in normalized)
    target = round(sum(lengths) / len(lengths))
    tolerance = max(10, round(target * 0.1))
    return max(1, min(lengths) - tolerance), target, max(lengths) + tolerance


def _resume_shape_defaults(config_path: Path) -> tuple[tuple[int, int, int], int]:
    if not config_path.expanduser().is_file():
        return _RECOMMENDED_BULLET_LENGTHS, 1
    resume = load_config(config_path).resume
    bullet_lengths = (
        resume.bullet_min_chars,
        resume.bullet_target_chars,
        resume.bullet_max_chars,
    )
    return (
        bullet_lengths,
        resume.max_pages or 1,
    )


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
        "\n1. Factual source: your master resume\n"
        "This is the knowledge base Erga may use for claims. PDF, DOCX, and .tex files all work, "
        "including multi-page master resumes. Erga reads every page, creates a private "
        "hash-verified copy, and never modifies the original.\n"
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
        "\n2. Style reference (optional)\n"
        "Erga's clean one-page defaults are recommended. Add a second resume only if you are "
        "confident it is a useful layout reference. A PDF page count can prefill the maximum; "
        "section order and density are recorded as descriptive metadata. The editable .tex "
        "template still controls rendered layout. Erga never treats reference wording as factual "
        "evidence.",
        style="fg:#aaaaaa",
    )
    style_resume: Path | None = None
    if bool(
        _required(
            questionary.confirm(
                "Override Erga's recommended style with a separate resume or template?",
                default=False,
            ).ask()
        )
    ):
        style_resume = normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Drop the resume or template you confidently want recorded as style:",
                        validate=_resume_file,
                    ).ask()
                )
            )
        )

    bullet_lengths, max_pages = _resume_shape_defaults(default_config_path)
    if style_resume is not None:
        style_page_count = load_resume_source(style_resume).page_count
        if style_page_count:
            max_pages = style_page_count
    questionary.print(
        "\n3. Resume shape\n"
        "Erga enforces maximum page count when compiling and checks newly authored bullets in "
        "supported LaTeX templates against the configured character range. Keep the recommended "
        "values, enter exact limits, or calibrate from examples. Example wording is discarded "
        "after calibration and never becomes career evidence.",
        style="fg:#aaaaaa",
    )
    if bool(
        _required(
            questionary.confirm(
                "Customize page count or bullet length?",
                default=False,
            ).ask()
        )
    ):
        max_pages = int(
            str(
                _required(
                    questionary.text(
                        "Maximum pages:",
                        default=str(max_pages),
                        validate=_positive_integer,
                    ).ask()
                )
            )
        )
        bullet_mode = str(
            _required(
                questionary.select(
                    "How should Erga constrain bullet length?",
                    choices=[
                        Choice(
                            "Keep recommended/current limits "
                            f"({bullet_lengths[0]} / {bullet_lengths[1]} / "
                            f"{bullet_lengths[2]} characters)",
                            value="recommended",
                        ),
                        Choice("Enter minimum / target / maximum", value="manual"),
                        Choice("Calibrate from one or two example bullets", value="examples"),
                        Choice("Do not enforce bullet length", value="disabled"),
                    ],
                    default="recommended",
                    use_shortcuts=True,
                ).ask()
            )
        )
        if bullet_mode == "manual":
            minimum = int(
                str(
                    _required(
                        questionary.text(
                            "Minimum bullet characters:",
                            default=str(bullet_lengths[0]),
                            validate=_positive_integer,
                        ).ask()
                    )
                )
            )

            def target_length(value: str) -> bool | str:
                valid = _positive_integer(value)
                return (
                    valid
                    if valid is not True or int(value.strip()) >= minimum
                    else f"Enter a target of at least {minimum}."
                )

            target = int(
                str(
                    _required(
                        questionary.text(
                            "Target bullet characters:",
                            default=str(max(minimum, bullet_lengths[1])),
                            validate=target_length,
                        ).ask()
                    )
                )
            )

            def maximum_length(value: str) -> bool | str:
                valid = _positive_integer(value)
                return (
                    valid
                    if valid is not True or int(value.strip()) >= target
                    else f"Enter a maximum of at least {target}."
                )

            maximum = int(
                str(
                    _required(
                        questionary.text(
                            "Maximum bullet characters:",
                            default=str(max(target, bullet_lengths[2])),
                            validate=maximum_length,
                        ).ask()
                    )
                )
            )
            bullet_lengths = (minimum, target, maximum)
        elif bullet_mode == "examples":
            first_example = str(
                _required(
                    questionary.text(
                        "Paste a representative bullet (the wording will not be stored):",
                        validate=lambda value: _style_example(value, required=True),
                    ).ask()
                )
            )
            second_example = str(
                _required(
                    questionary.text(
                        "Paste a second bullet, or press Enter to skip:",
                        validate=lambda value: _style_example(value, required=False),
                    ).ask()
                )
            )
            bullet_lengths = bullet_lengths_from_examples((first_example, second_example))
            questionary.print(
                "Erga calibrated minimum / target / maximum to "
                f"{bullet_lengths[0]} / {bullet_lengths[1]} / {bullet_lengths[2]} characters. "
                "Only these numbers will be saved.",
                style="fg:#e0aa55",
            )
        elif bullet_mode == "disabled":
            bullet_lengths = (0, 0, 0)

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

    configured_output_name = (
        load_config(default_config_path).resume.output_pdf_name
        if default_config_path.expanduser().is_file()
        else _DEFAULT_OUTPUT_PDF_NAME
    )
    output_pdf_name = normalize_output_pdf_name(
        str(
            _required(
                questionary.text(
                    "Name every generated resume PDF (you can omit .pdf):",
                    default=configured_output_name,
                    validate=_output_pdf_name,
                ).ask()
            )
        )
    )

    selections = CoreSetupSelections(
        config_path=default_config_path.expanduser().absolute(),
        master_resume=master_resume,
        style_resume=style_resume,
        bullet_min_chars=bullet_lengths[0],
        bullet_target_chars=bullet_lengths[1],
        bullet_max_chars=bullet_lengths[2],
        max_pages=max_pages,
        output_root=output_root,
        output_pdf_name=output_pdf_name,
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
        f"record {selections.style_resume.name} as non-factual style metadata"
        if selections.style_resume is not None
        else "use Erga's clean one-page layout (recommended)"
    )
    bullets = (
        "not enforced"
        if selections.bullet_min_chars == 0
        else (
            f"{selections.bullet_min_chars} / {selections.bullet_target_chars} / "
            f"{selections.bullet_max_chars} characters (minimum / target / maximum)"
        )
    )
    return "\n".join(
        [
            "What Erga will set up",
            "",
            f"  Your master resume: copied privately from {selections.master_resume}",
            f"  Resume style: {style}",
            "  Maximum resume length: "
            + (
                f"{selections.max_pages} page(s)"
                if selections.max_pages is not None
                else (
                    "match the style reference's page count"
                    if selections.style_resume is not None
                    else "1 page (recommended)"
                )
            ),
            f"  Bullet length: {bullets}",
            f"  Generated resumes: {selections.output_root}",
            f"  Generated PDF filename: {normalize_output_pdf_name(selections.output_pdf_name)}",
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
    raw = update_table(
        raw,
        "paths",
        {
            "data_dir": str(data_dir),
            "vault_path": str(vault_path) if vault_path is not None else "",
        },
    )
    raw = update_table(
        raw,
        "tracking",
        {
            "enabled": tracker_dir is not None,
            "tracker_dir": str(tracker_dir) if tracker_dir is not None else "",
            "active_cycles": active_cycles,
        },
    )
    raw = update_table(raw, "mcp", {"tool_profile": "career"})
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
    resolved_max_pages = (
        selections.max_pages
        or (
            managed_style.page_count
            if managed_style is not None and managed_style.page_count
            else None
        )
        or current_resume.max_pages
        or 1
    )
    update_settings(
        selections.config_path,
        {
            "master_path": str(managed_master.path),
            "reference_path": str(managed_style.path) if managed_style is not None else "",
            "output_root": str(output_root),
            "output_pdf_name": normalize_output_pdf_name(selections.output_pdf_name),
            "bullet_min_chars": selections.bullet_min_chars,
            "bullet_target_chars": selections.bullet_target_chars,
            "bullet_max_chars": selections.bullet_max_chars,
            "max_pages": resolved_max_pages,
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
