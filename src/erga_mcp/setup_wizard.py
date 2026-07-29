"""Arrow-key setup wizard for the complete Erga experience."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

import questionary
from questionary import Choice

from .client_config import ClientName
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
    resume_template: Path | None = None
    output_root: Path | None = None
    discord_token: str | None = None
    discord_user_ids: tuple[int, ...] = ()
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


def _existing_file_or_blank(value: str) -> bool | str:
    if not value.strip() or Path(value).expanduser().is_file():
        return True
    return "Choose an existing LaTeX resume, or leave this blank."


def _discord_ids(value: str) -> bool | str:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if values and all(item.isdigit() for item in values):
        return True
    return "Enter at least one numeric Discord user ID."


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
                    Choice("Codex / ChatGPT", value="codex"),
                    Choice("Claude Code / Claude Pro or Max", value="claude-code"),
                    Choice("OpenCode", value="opencode"),
                ],
                use_shortcuts=True,
            ).ask()
        ),
    )
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

    resume_template: Path | None = None
    output_root: Path | None = None
    if "resume" in features:
        entered = str(
            _required(
                questionary.path(
                    "Master LaTeX resume (blank to configure later):",
                    validate=_existing_file_or_blank,
                ).ask()
            )
        ).strip()
        if entered:
            resume_template = Path(entered).expanduser().absolute()
            output_root = (
                Path(
                    str(
                        _required(
                            questionary.path(
                                "Application output directory:",
                                default=str(project_dir / "erga-applications"),
                            ).ask()
                        )
                    )
                )
                .expanduser()
                .absolute()
            )

    discord_token: str | None = None
    discord_user_ids: tuple[int, ...] = ()
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
        raw_ids = str(
            _required(
                questionary.text(
                    "Your Discord user ID (comma-separate additional trusted users):",
                    validate=_discord_ids,
                ).ask()
            )
        )
        discord_user_ids = tuple(
            int(value.strip()) for value in raw_ids.split(",") if value.strip()
        )
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
        resume_template=resume_template,
        output_root=output_root,
        discord_token=discord_token,
        discord_user_ids=discord_user_ids,
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
        lines.append(f"  Resume template: {selections.resume_template or 'configure later'}")
    if "discord" in selections.features:
        lines.extend(
            [
                f"  Discord users:   {len(selections.discord_user_ids)} authorized",
                "  Discord token:   OS credential store (never config)",
            ]
        )
    lines.append("  Model API key:   not requested")
    return "\n".join(lines)


def apply_setup(
    selections: SetupSelections,
    *,
    server_command: Path | None = None,
    client_command: Path | None = None,
) -> SetupReport:
    """Apply reviewed selections using the existing coding-agent subscription."""
    resolved_client = resolve_client_command(selections.client, client_command)
    logged_in, login_detail = verify_subscription_login(selections.client, resolved_client)
    if not logged_in:
        raise RuntimeError(
            f"{selections.client} is installed but not ready. Sign in first. {login_detail}"
        )
    onboard(
        selections.client,
        config_path=selections.config_path,
        project_dir=selections.project_dir,
        server_command=server_command,
    )
    completed = [f"{selections.client} subscription login", "Erga MCP connection"]
    next_steps = ['Ask your coding AI: "Show my Erga pipeline status."']

    resume_configured = False
    if "resume" in selections.features:
        if selections.resume_template is None or selections.output_root is None:
            next_steps.append("Add your master LaTeX resume with `erga resume settings set`.")
        else:
            selections.output_root.mkdir(parents=True, exist_ok=True)
            update_settings(
                selections.config_path,
                {
                    "template_path": str(selections.resume_template),
                    "output_root": str(selections.output_root),
                    "editable_sections": ["Experience", "Projects", "Technical-Skills"],
                    "max_pages": 1,
                },
            )
            resume_configured = True
            completed.append("Resume generation")

    discord_configured = False
    discord_running = False
    if "discord" in selections.features:
        if selections.discord_token is None or not selections.discord_user_ids:
            raise ValueError("Discord setup requires a bot token and at least one authorized user")
        write_discord_settings(
            selections.config_path,
            DiscordBridgeSettings(
                client=selections.client,
                client_command=str(resolved_client),
                project_dir=selections.project_dir,
                allowed_user_ids=selections.discord_user_ids,
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
    for key in ("project_dir", "config_path", "resume_template", "output_root"):
        value = payload[key]
        payload[key] = str(value) if value is not None else None
    payload["discord_token"] = "<redacted>" if selections.discord_token else None
    return json.dumps(payload, indent=2, sort_keys=True)
