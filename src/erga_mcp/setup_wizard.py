"""Arrow-key setup wizard for the complete Erga experience."""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

import questionary
from questionary import Choice

from .client_adapters import CLIENT_ADAPTERS, PRESET_CLIENTS, ClientName
from .config import load_config
from .discord_bridge import (
    DiscordBridgeSettings,
    resolve_client_command,
    start_discord_bridge,
    store_discord_token,
    verify_subscription_login,
    write_discord_settings,
)
from .onboarding import onboard
from .resume_settings import update_settings
from .resume_sources import (
    SUPPORTED_RESUME_SUFFIXES,
    import_master_resume,
    load_resume_source,
)
from .store import ErgaStore

Feature = Literal["resume", "discord"]
Experience = Literal["full", "local", "custom"]


class WizardCancelled(RuntimeError):
    """Raised when setup exits before the final confirmation."""


@dataclass(frozen=True)
class SetupSelections:
    experience: Experience
    client: ClientName
    project_dir: Path
    config_path: Path
    features: tuple[Feature, ...]
    master_resume: Path | None = None
    style_resume: Path | None = None
    output_root: Path | None = None
    client_command: Path | None = None
    custom_arguments: tuple[str, ...] = ()
    discord_token: str | None = None
    discord_user_ids: tuple[int, ...] = ()
    discord_usernames: tuple[str, ...] = ()
    start_discord: bool = True


@dataclass(frozen=True)
class SetupReport:
    status: str
    client: ClientName
    resume_configured: bool
    discord_configured: bool
    discord_running: bool
    completed: list[str]
    next_steps: list[str]


def _required(value: object) -> object:
    if value is None:
        raise WizardCancelled("Setup cancelled; no changes were made.")
    return value


def _existing_directory(value: str) -> bool | str:
    return True if Path(value).expanduser().is_dir() else "Choose an existing directory."


def _normalize_dropped_path(value: str) -> Path:
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


def _master_resume_file(value: str) -> bool | str:
    path = _normalize_dropped_path(value)
    if not path.is_file():
        return "Drag an existing master resume file into this window."
    if path.suffix.casefold() not in SUPPORTED_RESUME_SUFFIXES:
        return "Use a PDF, DOCX, or LaTeX (.tex) file as the master resume."
    return True


def _style_resume_file(value: str) -> bool | str:
    path = _normalize_dropped_path(value)
    if not path.is_file():
        return "Drag an existing resume file into this window."
    if path.suffix.casefold() not in SUPPORTED_RESUME_SUFFIXES:
        return "Use a PDF, DOCX, or LaTeX (.tex) resume as the style template."
    return True


def _parse_discord_identities(value: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    user_ids: list[int] = []
    usernames: list[str] = []
    for entered in (item.strip() for item in value.split(",")):
        if not entered:
            continue
        if entered.isdigit():
            user_ids.append(int(entered))
            continue
        username = entered.removeprefix("@").casefold()
        if not re.fullmatch(r"[a-z0-9._]{2,32}", username):
            raise ValueError(f"Invalid Discord username or user ID: {entered}")
        usernames.append(username)
    if not user_ids and not usernames:
        raise ValueError("Enter at least one Discord username or numeric user ID.")
    return tuple(dict.fromkeys(user_ids)), tuple(dict.fromkeys(usernames))


def _discord_identities(value: str) -> bool | str:
    try:
        _parse_discord_identities(value)
    except ValueError as error:
        return str(error)
    return True


def _headless_arguments(value: str) -> bool | str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return 'Enter a JSON array such as ["-p", "{prompt}"].'
    if (
        not isinstance(parsed, list)
        or not parsed
        or not all(isinstance(item, str) for item in parsed)
    ):
        return "Headless arguments must be a non-empty JSON array of strings."
    if parsed.count("{prompt}") != 1:
        return 'Include "{prompt}" as exactly one standalone array item.'
    return True


def collect_setup_selections(
    *,
    default_project_dir: Path,
    default_config_path: Path,
    dry_run: bool = False,
) -> SetupSelections:
    """Collect and review choices before writing config or credentials."""
    questionary.print("\nErga Setup", style="bold fg:#7c5cff")
    questionary.print(
        "One setup for your coding AI, resume workflow, and Discord.\n"
        "Erga uses your existing coding-tool login—no model API key required.",
        style="fg:#aaaaaa",
    )
    experience = cast(
        Experience,
        _required(
            questionary.select(
                "What do you want to set up?",
                choices=[
                    Choice(
                        "Full Erga — coding AI, resume generation, and Discord",
                        value="full",
                    ),
                    Choice("Local Erga — coding AI and resume generation", value="local"),
                    Choice("Custom — choose components", value="custom"),
                ],
                default="full",
                use_shortcuts=True,
            ).ask()
        ),
    )
    client = cast(
        ClientName,
        _required(
            questionary.select(
                "Which coding AI subscription do you use?",
                choices=[
                    *[
                        Choice(CLIENT_ADAPTERS[client_id].label, value=client_id)
                        for client_id in PRESET_CLIENTS
                    ],
                    Choice(
                        "Other MCP-capable coding CLI — advanced",
                        value="generic-mcp",
                    ),
                ],
                use_shortcuts=True,
            ).ask()
        ),
    )
    client_command: Path | None = None
    custom_arguments: tuple[str, ...] = ()
    if client == "generic-mcp":
        questionary.print(
            "\nAdvanced adapter: Erga will pass arguments directly to this executable without "
            "a shell and generate portable project .mcp.json configuration. Verify that your "
            "client supports both.",
            style="fg:#e0aa55",
        )
        client_command = (
            Path(
                str(
                    _required(
                        questionary.path(
                            "Coding-agent executable:",
                            only_files=True,
                            validate=lambda value: (
                                True
                                if Path(value).expanduser().is_file()
                                else "Choose an existing executable file."
                            ),
                        ).ask()
                    )
                )
            )
            .expanduser()
            .absolute()
        )
        raw_arguments = str(
            _required(
                questionary.text(
                    "Headless argument template (JSON array):",
                    default='["-p", "--output-format", "text", "{prompt}"]',
                    validate=_headless_arguments,
                ).ask()
            )
        )
        custom_arguments = tuple(json.loads(raw_arguments))
    project_dir = (
        Path(
            str(
                _required(
                    questionary.path(
                        "Resume workspace:",
                        default=str(default_project_dir.expanduser().absolute()),
                        only_directories=True,
                        validate=_existing_directory,
                    ).ask()
                )
            )
        )
        .expanduser()
        .absolute()
    )

    if experience == "full":
        features: tuple[Feature, ...] = ("resume", "discord")
    elif experience == "local":
        features = ("resume",)
    else:
        features = tuple(
            cast(
                list[Feature],
                _required(
                    questionary.checkbox(
                        "Select components:",
                        choices=[
                            Choice("Resume generation", value="resume", checked=True),
                            Choice("Native Discord assistant", value="discord"),
                        ],
                        validate=lambda selected: (
                            bool(selected) or "Select at least one component."
                        ),
                    ).ask()
                ),
            )
        )

    master_resume: Path | None = None
    style_resume: Path | None = None
    output_root: Path | None = None
    if "resume" in features:
        questionary.print(
            "\nResume setup\n"
            "Drag your complete master resume into this window, then press Enter. It may span "
            "many pages. Erga extracts every page as user-approved factual knowledge and never "
            "modifies the original file.",
            style="fg:#e0aa55",
        )
        master_resume = _normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Drop your master resume (PDF, DOCX, or .tex) here:",
                        validate=_master_resume_file,
                    ).ask()
                )
            )
        )
        output_root = (project_dir / "erga-applications").absolute()
        questionary.print(
            "Erga's built-in one-page style is the recommended choice. Only provide another "
            "resume if you are confident its layout is how every generated resume should look.",
            style="fg:#aaaaaa",
        )
        has_separate_reference = bool(
            _required(
                questionary.confirm(
                    "Override Erga's recommended style with your own template resume?",
                    default=False,
                ).ask()
            )
        )
        if has_separate_reference:
            questionary.print(
                "Drag the resume whose page length, section order, and density best represent "
                "the final result you confidently want. This explicitly overrides Erga's "
                "recommended defaults, but can never add factual claims.",
                style="fg:#aaaaaa",
            )
            style_resume = _normalize_dropped_path(
                str(
                    _required(
                        questionary.text(
                            "Drop the optional style resume (PDF, DOCX, or .tex) here:",
                            validate=_style_resume_file,
                        ).ask()
                    )
                )
            )

    discord_token: str | None = None
    discord_user_ids: tuple[int, ...] = ()
    discord_usernames: tuple[str, ...] = ()
    start_discord = False
    if "discord" in features:
        questionary.print(
            "\nCreate a Discord application and bot at "
            "https://discord.com/developers/applications. Enable Message Content Intent, "
            "then invite it with View Channels, Send Messages, Read History, and Attach Files.",
            style="fg:#e0aa55",
        )
        discord_token = str(
            _required(
                questionary.password(
                    "Discord bot token (stored in your OS credential store):",
                    validate=lambda value: bool(value.strip()) or "A bot token is required.",
                ).ask()
            )
        )
        raw_identities = str(
            _required(
                questionary.text(
                    "Your Discord username or numeric user ID "
                    "(comma-separate additional trusted users):",
                    validate=_discord_identities,
                ).ask()
            )
        )
        discord_user_ids, discord_usernames = _parse_discord_identities(raw_identities)
        start_discord = bool(
            _required(
                questionary.confirm(
                    "Start the Erga Discord bridge after setup?",
                    default=True,
                ).ask()
            )
        )

    selections = SetupSelections(
        experience=experience,
        client=client,
        project_dir=project_dir,
        config_path=default_config_path.expanduser().absolute(),
        features=features,
        master_resume=master_resume,
        style_resume=style_resume,
        output_root=output_root,
        client_command=client_command,
        custom_arguments=custom_arguments,
        discord_token=discord_token,
        discord_user_ids=discord_user_ids,
        discord_usernames=discord_usernames,
        start_discord=start_discord,
    )
    questionary.print("\nReview", style="bold")
    questionary.print(render_setup_review(selections))
    confirmation = "Generate this dry-run plan?" if dry_run else "Apply this setup?"
    if not bool(_required(questionary.confirm(confirmation, default=True).ask())):
        raise WizardCancelled("Setup cancelled; no changes were made.")
    return selections


def render_setup_review(selections: SetupSelections) -> str:
    lines = [
        f"  Coding AI:       {selections.client}",
        f"  Workspace:       {selections.project_dir}",
        f"  Private config:  {selections.config_path}",
        f"  Components:      {', '.join(selections.features)}",
    ]
    if "resume" in selections.features:
        lines.extend(
            [
                f"  Master knowledge: {selections.master_resume or 'configure later'}",
                f"  Style template:  {selections.style_resume or 'Erga default (recommended)'}",
                f"  Resume output:   {selections.output_root or 'configure later'}",
            ]
        )
    if "discord" in selections.features:
        lines.extend(
            [
                "  Discord users:   "
                f"{len(selections.discord_user_ids) + len(selections.discord_usernames)} "
                "authorized",
                "  Discord token:   OS credential store (never config)",
            ]
        )
    if selections.client == "generic-mcp":
        lines.extend(
            [
                f"  Client command:  {selections.client_command}",
                "  Client billing:  verify in the selected CLI",
            ]
        )
    else:
        lines.append("  Model API key:   not requested")
    return "\n".join(lines)


def apply_setup(
    selections: SetupSelections,
    *,
    server_command: Path | None = None,
    client_command: Path | None = None,
) -> SetupReport:
    """Apply reviewed selections using the existing coding-agent subscription."""
    master_source = None
    style_source = None
    if (
        "resume" in selections.features
        and selections.master_resume is not None
        and selections.output_root is not None
    ):
        master_source = load_resume_source(selections.master_resume)
        style_source = (
            load_resume_source(selections.style_resume)
            if selections.style_resume is not None
            else None
        )

    explicit_client = client_command or selections.client_command
    resolved_client = resolve_client_command(selections.client, explicit_client)
    logged_in, login_detail = verify_subscription_login(
        selections.client,
        resolved_client,
        selections.custom_arguments,
    )
    if not logged_in:
        raise RuntimeError(
            f"{selections.client} is installed but not ready. Sign in first. {login_detail}"
        )
    onboard(
        selections.client,
        config_path=selections.config_path,
        project_dir=selections.project_dir,
        server_command=server_command,
        client_command=resolved_client,
    )
    completed = [f"{selections.client} subscription login", "Erga MCP connection"]
    next_steps = ['Ask your coding AI: "Show my Erga pipeline status."']

    resume_configured = False
    if "resume" in selections.features:
        if selections.master_resume is None or selections.output_root is None:
            next_steps.append("Add your master resume with `erga resume settings set`.")
        else:
            assert master_source is not None
            config = load_config(selections.config_path)
            evidence = import_master_resume(
                ErgaStore(config.data_dir / "erga.sqlite3"),
                master_source,
            )
            selections.output_root.mkdir(parents=True, exist_ok=True)
            update_settings(
                selections.config_path,
                {
                    "master_path": str(master_source.path),
                    "template_path": (
                        str(master_source.path) if master_source.format == "tex" else ""
                    ),
                    "reference_path": str(style_source.path) if style_source else "",
                    "output_root": str(selections.output_root),
                    "editable_sections": ["Experience", "Projects", "Technical-Skills"],
                    "max_pages": (
                        style_source.page_count if style_source and style_source.page_count else 1
                    ),
                },
            )
            resume_configured = True
            completed.append("Master resume knowledge")
            if style_source is not None:
                completed.append("Resume style preferences")
            next_steps.append(
                "Ask your coding AI to read `resume_source_context` before drafting a resume; "
                f"the master source is approved evidence {evidence.id}."
            )

    discord_configured = False
    discord_running = False
    if "discord" in selections.features:
        if selections.discord_token is None or not (
            selections.discord_user_ids or selections.discord_usernames
        ):
            raise ValueError("Discord setup requires a bot token and at least one authorized user")
        write_discord_settings(
            selections.config_path,
            DiscordBridgeSettings(
                client=selections.client,
                client_command=str(resolved_client),
                project_dir=selections.project_dir,
                allowed_user_ids=selections.discord_user_ids,
                allowed_usernames=selections.discord_usernames,
                custom_arguments=selections.custom_arguments,
            ),
        )
        store_discord_token(selections.config_path, selections.discord_token)
        discord_configured = True
        completed.append("Native Discord bridge")
        if selections.start_discord:
            discord_running = bool(start_discord_bridge(selections.config_path)["running"])
            completed.append("Discord bridge process")
        else:
            next_steps.append("Start Discord with `erga discord start`.")
        next_steps.append(
            "DM the bot, or @mention it in an authorized server channel, then paste a job URL."
        )

    return SetupReport(
        status="ready",
        client=selections.client,
        resume_configured=resume_configured,
        discord_configured=discord_configured,
        discord_running=discord_running,
        completed=completed,
        next_steps=next_steps,
    )


def render_setup_report(report: SetupReport) -> str:
    return "\n".join(
        [
            "",
            "Erga is ready.",
            "",
            *[f"  [ok] {item}" for item in report.completed],
            "",
            "Next:",
            *[f"  {index}. {step}" for index, step in enumerate(report.next_steps, start=1)],
        ]
    )


def write_setup_plan(selections: SetupSelections) -> str:
    payload = asdict(selections)
    for key in (
        "project_dir",
        "config_path",
        "master_resume",
        "style_resume",
        "output_root",
        "client_command",
    ):
        value = payload[key]
        payload[key] = str(value) if value is not None else None
    payload["discord_token"] = "<redacted>" if selections.discord_token else None
    return json.dumps(payload, indent=2, sort_keys=True)
