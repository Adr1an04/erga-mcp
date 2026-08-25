"""Interactive configuration for the optional Discord bridge."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

import questionary
from questionary import Choice

from erga_mcp.integrations.discord.backends import (
    DISCORD_BACKENDS,
    PRESET_DISCORD_BACKENDS,
    DiscordBackendName,
)
from erga_mcp.integrations.discord.bridge import (
    DiscordBridgeSettings,
    resolve_backend_command,
    start_discord_bridge,
    store_discord_token,
    verify_backend_login,
    write_discord_settings,
)
from erga_mcp.integrations.hosts import HostName, configure_hosts
from erga_mcp.operations.setup_wizard import WizardCancelled, normalize_dropped_path


@dataclass(frozen=True)
class DiscordSetupReport:
    status: str
    settings_path: str
    backend: str
    project_dir: str
    authorized_identities: int
    token_storage: str
    login_verified: bool
    host_connection_written: bool
    running: bool
    next_steps: list[str]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def render_discord_setup_report(report: DiscordSetupReport) -> str:
    state = "online" if report.running else "configured"
    lines = [
        f"\nDiscord is {state}.",
        f"Trusted Discord accounts: {report.authorized_identities}",
        "Your bot token is stored only in the OS credential store.",
        "",
        "Next:",
    ]
    lines.extend(f"  • {step}" for step in report.next_steps)
    lines.append("Erga never applies, submits, or messages anyone for you.")
    return "\n".join(lines)


def _required(value: object) -> object:
    if value is None:
        raise WizardCancelled("Discord configuration cancelled; Erga's core remains ready.")
    return value


def parse_discord_identities(value: str) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Accept stable numeric IDs and Discord's current unique username format."""
    user_ids: list[int] = []
    usernames: list[str] = []
    for entered in (item.strip() for item in value.split(",")):
        if not entered:
            continue
        if entered.isdecimal():
            user_ids.append(int(entered))
            continue
        if "#" in entered:
            raise ValueError(
                "Discord discriminator names such as name#1234 are obsolete; "
                "enter a current username such as emperor_sai or a numeric user ID."
            )
        username = entered.removeprefix("@").casefold()
        if not re.fullmatch(r"[a-z0-9._]{2,32}", username):
            raise ValueError(f"Invalid Discord username or user ID: {entered}")
        usernames.append(username)
    if not user_ids and not usernames:
        raise ValueError("Enter at least one Discord username or numeric user ID.")
    return tuple(dict.fromkeys(user_ids)), tuple(dict.fromkeys(usernames))


def _discord_identities(value: str) -> bool | str:
    try:
        parse_discord_identities(value)
    except ValueError as error:
        return str(error)
    return True


def _existing_directory(value: str) -> bool | str:
    return (
        True
        if normalize_dropped_path(value).is_dir()
        else "Drag or enter an existing project directory."
    )


def _existing_file(value: str) -> bool | str:
    return (
        True if normalize_dropped_path(value).is_file() else "Drag or enter an existing executable."
    )


def _custom_arguments(value: str) -> bool | str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        return f"Enter a JSON array of arguments: {error.msg}"
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return "Enter a JSON array containing only strings."
    if not any("{prompt}" in item for item in parsed):
        return "The argument array must include {prompt}."
    return True


def _host_for_backend(backend: DiscordBackendName) -> HostName:
    return cast(HostName, "generic-mcp" if backend == "custom" else backend)


def discord_runtime_installed() -> bool:
    return importlib.util.find_spec("discord") is not None


def detected_discord_backends() -> tuple[tuple[DiscordBackendName, Path], ...]:
    """Find supported local AI sign-ins without asking users about runtime plumbing."""
    detected: list[tuple[DiscordBackendName, Path]] = []
    for name in PRESET_DISCORD_BACKENDS:
        try:
            detected.append((name, resolve_backend_command(name)))
        except FileNotFoundError:
            continue
    return tuple(detected)


def first_ready_discord_backend(
    candidates: tuple[tuple[DiscordBackendName, Path], ...],
    *,
    project_dir: Path,
) -> tuple[tuple[DiscordBackendName, Path] | None, str]:
    """Choose the first runtime that completes a real reply check, not merely a PATH lookup."""
    last_detail = "no supported AI app was detected"
    for backend, command in candidates:
        verified, detail = verify_backend_login(
            DiscordBridgeSettings(
                backend=backend,
                backend_command=str(command),
                project_dir=project_dir,
                allowed_user_ids=(1,),
            )
        )
        if verified:
            return (backend, command), detail
        last_detail = detail
    return None, last_detail


def collect_optional_discord() -> bool:
    """Offer the normal conversational interface after the private workspace is ready."""
    selected = questionary.confirm(
        "Connect Erga to Discord now?",
        default=True,
    ).ask()
    if selected is None:
        raise WizardCancelled("Discord setup skipped; your private Erga workspace is ready.")
    return bool(selected)


def configure_discord_interactive(
    *,
    config_path: Path,
    default_project_dir: Path,
    advanced: bool = False,
) -> DiscordSetupReport:
    """Configure private Discord access while hiding replaceable runtime plumbing by default."""
    questionary.print("\nConnect Erga to Discord", style="bold fg:#7c5cff")
    questionary.print(
        "After this one-time setup, you can talk to Erga normally in Discord. "
        "Only the Discord accounts you approve below can use it.",
        style="fg:#aaaaaa",
    )
    detected = detected_discord_backends()
    if advanced:
        backend = cast(
            DiscordBackendName,
            _required(
                questionary.select(
                    "Advanced: choose the local AI runtime:",
                    choices=[
                        *[
                            Choice(DISCORD_BACKENDS[name].label, value=name)
                            for name in PRESET_DISCORD_BACKENDS
                        ],
                        Choice(DISCORD_BACKENDS["custom"].label, value="custom"),
                    ],
                    use_shortcuts=True,
                ).ask()
            ),
        )
    elif not detected:
        raise FileNotFoundError(
            "Erga could not find a supported signed-in AI app on this computer. Install and "
            "sign in to Codex/ChatGPT, Claude Code, Cursor, Gemini CLI, GitHub Copilot CLI, or "
            "OpenCode, then run `erga discord configure` again."
        )
    else:
        backend = detected[0][0]

    explicit_command: Path | None = None
    custom_arguments: tuple[str, ...] = ()
    if backend == "custom":
        explicit_command = normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Drag the headless coding CLI executable here:",
                        validate=_existing_file,
                    ).ask()
                )
            )
        )
        raw_arguments = str(
            _required(
                questionary.text(
                    "JSON argument array using {prompt}, {project_dir}, or {output_path}:",
                    validate=_custom_arguments,
                ).ask()
            )
        )
        custom_arguments = tuple(cast(list[str], json.loads(raw_arguments)))

    # Resolve the optional executable before asking for a token or Discord identity.
    backend_command = resolve_backend_command(backend, explicit_command)
    project_dir = default_project_dir.expanduser().absolute()
    if advanced:
        project_dir = normalize_dropped_path(
            str(
                _required(
                    questionary.text(
                        "Advanced: local workspace for Erga tasks:",
                        default=str(project_dir),
                        validate=_existing_directory,
                    ).ask()
                )
            )
        )
    provisional = DiscordBridgeSettings(
        backend=backend,
        backend_command=str(backend_command),
        project_dir=project_dir,
        allowed_user_ids=(1,),
        custom_arguments=custom_arguments,
    )
    questionary.print("Checking that Erga can reply...", style="fg:#aaaaaa")
    if advanced:
        login_verified, detail = verify_backend_login(provisional)
    else:
        ready_backend, detail = first_ready_discord_backend(
            detected,
            project_dir=project_dir,
        )
        login_verified = ready_backend is not None
        if ready_backend is not None:
            backend, backend_command = ready_backend
    if not login_verified:
        if advanced:
            questionary.print(f"Connection check failed: {detail}", style="fg:#e0aa55")
        else:
            questionary.print(
                "Erga couldn't start any supported AI app found on this computer. "
                "Open or reinstall the app you normally use, confirm you are signed in, then "
                "run `erga discord configure` again.",
                style="fg:#e0aa55",
            )
        if not advanced or not bool(
            _required(
                questionary.confirm(
                    "Advanced: save this incomplete connection anyway?",
                    default=False,
                ).ask()
            )
        ):
            raise WizardCancelled(
                "Discord setup stopped safely. No Discord credentials or settings were saved."
            )
    else:
        questionary.print(
            f"Reply check passed using {DISCORD_BACKENDS[backend].label}.",
            style="fg:#aaaaaa",
        )

    questionary.print(
        "\nDiscord bot setup (one time, about 3 minutes)\n"
        "  1. Open https://discord.com/developers/applications and choose New Application.\n"
        "  2. Open Bot, create the bot, and enable Message Content Intent.\n"
        "  3. Copy the bot token; Erga stores it only in your OS credential store.\n"
        "  4. Under OAuth2, invite it with View Channels, Send Messages, Embed Links, "
        "Attach Files, and Read Message History.",
        style="fg:#e0aa55",
    )
    token = str(
        _required(
            questionary.password(
                "Discord bot token (stored only in your OS credential store):",
                validate=lambda value: bool(value.strip()) or "A bot token is required.",
            ).ask()
        )
    )
    raw_identities = str(
        _required(
            questionary.text(
                "Trusted Discord username or numeric user ID (comma-separate additional people):",
                validate=_discord_identities,
            ).ask()
        )
    )
    user_ids, usernames = parse_discord_identities(raw_identities)
    respond_without_mention = bool(
        _required(
            questionary.confirm(
                "In servers, respond without requiring an @mention?",
                default=False,
            ).ask()
        )
    )
    settings = DiscordBridgeSettings(
        backend=backend,
        backend_command=str(backend_command),
        project_dir=project_dir,
        allowed_user_ids=user_ids,
        allowed_usernames=usernames,
        custom_arguments=custom_arguments,
        respond_in_servers_without_mention=respond_without_mention,
    )

    can_start = discord_runtime_installed()
    start_after_setup = False
    if can_start:
        start_after_setup = bool(
            _required(
                questionary.confirm(
                    "Start the Discord bridge after configuration?",
                    default=True,
                ).ask()
            )
        )
    else:
        questionary.print(
            "The bridge can be configured now, but running it requires the optional "
            "`erga-mcp[discord]` package extra.",
            style="fg:#e0aa55",
        )

    questionary.print("\nReady to connect", style="bold")
    questionary.print(
        "\n".join(
            [
                f"  AI sign-in:       {DISCORD_BACKENDS[backend].label}",
                f"  Trusted accounts: {len(user_ids) + len(usernames)}",
                "  Bot token:        OS credential store (never config)",
                "  Server messages:  "
                + ("all authorized" if respond_without_mention else "@mention only"),
                f"  Reply check:      {'passed' if login_verified else 'not verified'}",
            ]
        )
    )
    if not bool(
        _required(
            questionary.confirm(
                "Connect Erga to Discord?",
                default=True,
            ).ask()
        )
    ):
        raise WizardCancelled("Discord setup cancelled; your private Erga workspace is unchanged.")

    connection = configure_hosts(
        (_host_for_backend(backend),),
        project_dir=project_dir,
        config_path=config_path,
        write=True,
    )[0]
    target = write_discord_settings(config_path, settings)
    store_discord_token(config_path, token)
    running = False
    if start_after_setup:
        running = bool(start_discord_bridge(config_path)["running"])

    next_steps: list[str] = []
    if not can_start:
        next_steps.append("Install the optional runtime: pip install 'erga-mcp[discord]'")
    if running:
        next_steps.append("Open Discord and send: Tailor my résumé for this job: <paste link>")
    else:
        next_steps.append("Start Erga when ready: erga discord start")
    next_steps.append("In Discord, send `help` at any time for examples.")
    return DiscordSetupReport(
        status="configured",
        settings_path=str(target),
        backend=backend,
        project_dir=str(project_dir),
        authorized_identities=len(user_ids) + len(usernames),
        token_storage="OS credential store",
        login_verified=login_verified,
        host_connection_written=bool(connection["written"]),
        running=running,
        next_steps=next_steps,
    )
