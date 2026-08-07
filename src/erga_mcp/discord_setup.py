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

from .discord_backends import (
    DISCORD_BACKENDS,
    PRESET_DISCORD_BACKENDS,
    DiscordBackendName,
)
from .discord_bridge import (
    DiscordBridgeSettings,
    resolve_backend_command,
    start_discord_bridge,
    store_discord_token,
    verify_backend_login,
    write_discord_settings,
)
from .host_connections import HostName, configure_hosts
from .setup_wizard import WizardCancelled, normalize_dropped_path


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


def collect_optional_discord() -> bool:
    """Offer Discord only after the provider-neutral core has completed."""
    selected = questionary.confirm(
        "Add the optional Discord bridge now?",
        default=False,
    ).ask()
    if selected is None:
        raise WizardCancelled("Optional connection setup cancelled; Erga's core remains ready.")
    return bool(selected)


def configure_discord_interactive(
    *,
    config_path: Path,
    default_project_dir: Path,
) -> DiscordSetupReport:
    """Configure one replaceable Discord execution backend without changing Erga's core."""
    questionary.print("\nOptional Discord bridge", style="bold fg:#7c5cff")
    questionary.print(
        "Discord is only another way to reach Erga. It needs one local coding CLI for "
        "unattended replies, but that choice does not become Erga's core or restrict which "
        "other assistants you connect.",
        style="fg:#aaaaaa",
    )
    backend = cast(
        DiscordBackendName,
        _required(
            questionary.select(
                "Which installed coding CLI should power Discord replies?",
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
    project_dir = normalize_dropped_path(
        str(
            _required(
                questionary.text(
                    "Project workspace for Discord-powered Erga tasks:",
                    default=str(default_project_dir.expanduser().absolute()),
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
    login_verified = False
    if bool(
        _required(
            questionary.confirm(
                "Run one small readiness turn using this existing coding-tool login?",
                default=True,
            ).ask()
        )
    ):
        questionary.print(
            f"Checking {DISCORD_BACKENDS[backend].label}...",
            style="fg:#aaaaaa",
        )
        login_verified, detail = verify_backend_login(provisional)
        if not login_verified:
            questionary.print(f"Readiness check failed: {detail}", style="fg:#e0aa55")
            if not bool(
                _required(
                    questionary.confirm(
                        "Save the bridge configuration anyway?",
                        default=False,
                    ).ask()
                )
            ):
                raise WizardCancelled("Discord configuration stopped; Erga's core remains ready.")

    questionary.print(
        "\nCreate a Discord application and bot at "
        "https://discord.com/developers/applications. Enable Message Content Intent, "
        "then invite it with View Channels, Send Messages, Embed Links, and Read Message History.",
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
                    default=False,
                ).ask()
            )
        )
    else:
        questionary.print(
            "The bridge can be configured now, but running it requires the optional "
            "`erga-mcp[discord]` package extra.",
            style="fg:#e0aa55",
        )

    questionary.print("\nReview optional Discord connection", style="bold")
    questionary.print(
        "\n".join(
            [
                f"  Backend:          {DISCORD_BACKENDS[backend].label}",
                f"  Backend command:  {backend_command}",
                f"  Project:          {project_dir}",
                f"  Trusted accounts: {len(user_ids) + len(usernames)}",
                "  Bot token:        OS credential store (never config)",
                "  Server messages:  "
                + ("all authorized" if respond_without_mention else "@mention only"),
                f"  Login check:      {'passed' if login_verified else 'not verified'}",
                "  Erga core:        unchanged",
            ]
        )
    )
    if not bool(
        _required(
            questionary.confirm(
                "Apply this optional Discord connection?",
                default=True,
            ).ask()
        )
    ):
        raise WizardCancelled("Discord configuration cancelled; Erga's core remains ready.")

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
    if not running:
        next_steps.append("Start when ready: erga discord start")
    next_steps.append("Use `erga discord status` to inspect only this optional bridge.")
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
