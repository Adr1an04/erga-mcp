"""Native Discord bridge to subscription-authenticated coding-agent CLIs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import discord
import keyring

from .client_adapters import ClientName, client_adapter
from .config import load_config
from .private_files import restrict_private_directory, restrict_private_file

_TOKEN_SERVICE = "erga-mcp.discord"
_SETTINGS_NAME = "discord-bridge.json"
_PID_NAME = "discord-bridge.pid"
_LOG_NAME = "discord-bridge.log"
_MAX_DISCORD_MESSAGE = 1_900


@dataclass(frozen=True)
class DiscordBridgeSettings:
    client: ClientName
    client_command: str
    project_dir: Path
    allowed_user_ids: tuple[int, ...]
    allowed_usernames: tuple[str, ...] = ()
    custom_arguments: tuple[str, ...] = ()
    respond_in_servers_without_mention: bool = False
    timeout_seconds: int = 600


def settings_path(config_path: Path) -> Path:
    return config_path.expanduser().absolute().parent / _SETTINGS_NAME


def _token_account(config_path: Path) -> str:
    normalized = str(config_path.expanduser().absolute())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def store_discord_token(config_path: Path, token: str) -> None:
    """Store a bot token in the OS credential store, never in Erga configuration."""
    if not token.strip():
        raise ValueError("Discord bot token cannot be empty")
    keyring.set_password(_TOKEN_SERVICE, _token_account(config_path), token.strip())


def read_discord_token(config_path: Path) -> str:
    token = keyring.get_password(_TOKEN_SERVICE, _token_account(config_path))
    if not token:
        raise RuntimeError("Discord bot token is not configured; rerun `erga setup`")
    return token


def write_discord_settings(
    config_path: Path,
    settings: DiscordBridgeSettings,
) -> Path:
    target = settings_path(config_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(settings)
    payload["project_dir"] = str(settings.project_dir)
    payload["allowed_user_ids"] = list(settings.allowed_user_ids)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    restrict_private_file(target)
    return target


def load_discord_settings(config_path: Path) -> DiscordBridgeSettings:
    payload = json.loads(settings_path(config_path).read_text(encoding="utf-8"))
    return DiscordBridgeSettings(
        client=payload["client"],
        client_command=payload["client_command"],
        project_dir=Path(payload["project_dir"]),
        allowed_user_ids=tuple(int(value) for value in payload["allowed_user_ids"]),
        allowed_usernames=tuple(
            str(value).casefold() for value in payload.get("allowed_usernames", [])
        ),
        custom_arguments=tuple(str(value) for value in payload.get("custom_arguments", [])),
        respond_in_servers_without_mention=bool(
            payload.get("respond_in_servers_without_mention", False)
        ),
        timeout_seconds=int(payload.get("timeout_seconds", 600)),
    )


def _bundled_client_candidates(client: ClientName) -> tuple[Path, ...]:
    """Return known coding-client executables bundled inside desktop applications."""
    if client != "codex" or sys.platform != "darwin":
        return ()
    applications = (Path("/Applications"), Path.home() / "Applications")
    app_names = ("ChatGPT.app", "Codex.app")
    return tuple(
        root / app_name / "Contents" / "Resources" / "codex"
        for root in applications
        for app_name in app_names
    )


def resolve_client_command(client: ClientName, explicit: Path | None = None) -> Path:
    adapter = client_adapter(client)
    if explicit is not None:
        candidate = explicit.expanduser().absolute()
        if not candidate.is_file():
            raise FileNotFoundError(f"Coding-agent command does not exist: {candidate}")
        return candidate
    if adapter.executable is None:
        raise FileNotFoundError("The generic client requires an explicit coding-agent executable.")
    discovered = shutil.which(adapter.executable)
    if discovered is not None:
        return Path(discovered).absolute()
    for candidate in _bundled_client_candidates(client):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.absolute()
    raise FileNotFoundError(
        f"{adapter.label} command was not found on PATH or in a supported desktop app. "
        "Install the coding client and sign in, then rerun `erga setup`."
    )


def verify_subscription_login(
    client: ClientName,
    command: Path,
    custom_arguments: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """Verify both recorded login state and one minimal live coding-agent turn."""
    adapter = client_adapter(client)
    if adapter.status_arguments is not None:
        completed = subprocess.run(
            [str(command), *adapter.status_arguments],
            capture_output=True,
            text=True,
            timeout=30,
            env=_agent_environment(client),
        )
        detail = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode:
            return False, detail

    with tempfile.TemporaryDirectory() as directory:
        project_dir = Path(directory)
        output_path = project_dir / "readiness.txt"
        prompt = "Reply with exactly ERGA_READY and do not use tools."
        arguments = custom_arguments if client == "generic-mcp" else adapter.probe_arguments
        if not arguments:
            return False, "no headless argument template is configured for this client"
        probe = [
            str(command),
            *_render_arguments(
                arguments,
                prompt=prompt,
                project_dir=project_dir,
                output_path=output_path,
            ),
        ]
        live = subprocess.run(
            probe,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=60,
            env=_agent_environment(client),
        )
        if live.returncode:
            failure = (live.stderr or live.stdout or "live readiness turn failed").strip()
            return False, failure[-2_000:]
        rendered = _render_agent_output(adapter.output_source, live.stdout, output_path)
        if not rendered:
            return False, "live readiness turn returned no response"
        if rendered.strip() != "ERGA_READY":
            return False, (
                "live readiness turn did not return the expected ERGA_READY marker; "
                f"received: {rendered.strip()[-500:]}"
            )
    return True, "coding-agent subscription is ready"


def _agent_prompt(message: str) -> str:
    return (
        "You are the reasoning host for Erga's private Discord career assistant. "
        "Use the project-scoped Erga MCP tools for job intake, approved evidence, application "
        "tracking, resume proposals, and validation. Never submit an application, invent a claim, "
        "or send a message to an employer. Return concise Discord-friendly Markdown.\n\n"
        f"User message:\n{message}"
    )


def _agent_environment(client: ClientName) -> dict[str, str]:
    environment = os.environ.copy()
    adapter = client_adapter(client)
    for name in adapter.stripped_environment:
        environment.pop(name, None)
    environment.update(adapter.injected_environment)
    return environment


def _render_arguments(
    arguments: tuple[str, ...],
    *,
    prompt: str,
    project_dir: Path,
    output_path: Path,
) -> list[str]:
    replacements = {
        "{prompt}": prompt,
        "{project_dir}": str(project_dir),
        "{output_path}": str(output_path),
    }
    return [replacements.get(argument, argument) for argument in arguments]


def _render_agent_output(
    output_source: str,
    stdout: str,
    output_path: Path,
) -> str:
    if output_source == "file" and output_path.is_file():
        return output_path.read_text(encoding="utf-8").strip()
    if output_path.is_file():
        rendered_file = output_path.read_text(encoding="utf-8").strip()
        if rendered_file:
            return rendered_file
    return stdout.strip()


def build_agent_command(
    settings: DiscordBridgeSettings,
    prompt: str,
    output_path: Path,
) -> list[str]:
    adapter = client_adapter(settings.client)
    arguments = (
        settings.custom_arguments if settings.client == "generic-mcp" else adapter.run_arguments
    )
    if not arguments:
        raise ValueError("No headless arguments are configured for this coding client")
    return [
        settings.client_command,
        *_render_arguments(
            arguments,
            prompt=prompt,
            project_dir=settings.project_dir,
            output_path=output_path,
        ),
    ]


def run_agent(settings: DiscordBridgeSettings, message: str) -> str:
    """Run one bounded coding-agent turn using its existing local subscription login."""
    with tempfile.TemporaryDirectory() as directory:
        output_path = Path(directory) / "last-message.txt"
        command = build_agent_command(settings, _agent_prompt(message), output_path)
        completed = subprocess.run(
            command,
            cwd=settings.project_dir,
            capture_output=True,
            text=True,
            timeout=settings.timeout_seconds,
            env=_agent_environment(settings.client),
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout or "coding agent failed").strip()
            raise RuntimeError(detail[-2_000:])
        rendered = _render_agent_output(
            client_adapter(settings.client).output_source,
            completed.stdout,
            output_path,
        )
        if not rendered:
            raise RuntimeError("coding agent returned no final response")
        return rendered


def split_discord_message(value: str) -> list[str]:
    return [
        value[index : index + _MAX_DISCORD_MESSAGE]
        for index in range(0, len(value), _MAX_DISCORD_MESSAGE)
    ]


def is_authorized_discord_user(
    settings: DiscordBridgeSettings,
    *,
    user_id: int,
    username: str,
    is_bot: bool,
) -> bool:
    """Authorize a Discord account by stable ID or its current unique username."""
    if is_bot:
        return False
    return user_id in settings.allowed_user_ids or username.casefold() in settings.allowed_usernames


class ErgaDiscordClient(discord.Client):
    def __init__(self, settings: DiscordBridgeSettings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.settings = settings
        self._agent_lock = asyncio.Lock()

    async def on_ready(self) -> None:
        print(f"Erga Discord connected as {self.user}", flush=True)

    async def on_message(self, message: discord.Message) -> None:
        if not is_authorized_discord_user(
            self.settings,
            user_id=message.author.id,
            username=message.author.name,
            is_bot=message.author.bot,
        ):
            return
        is_direct_message = message.guild is None
        mentioned = self.user is not None and self.user in message.mentions
        if (
            not is_direct_message
            and not mentioned
            and not self.settings.respond_in_servers_without_mention
        ):
            return
        content = message.content
        if self.user is not None:
            content = content.replace(f"<@{self.user.id}>", "").strip()
        if not content:
            return
        async with message.channel.typing():
            try:
                async with self._agent_lock:
                    response = await asyncio.to_thread(run_agent, self.settings, content)
            except Exception as error:
                response = f"Erga could not complete that request: {error}"
        for chunk in split_discord_message(response):
            await message.reply(chunk, mention_author=False)


def run_discord_bridge(config_path: Path) -> int:
    settings = load_discord_settings(config_path)
    client = ErgaDiscordClient(settings)
    client.run(read_discord_token(config_path), log_handler=None)
    return 0


def _runtime_paths(config_path: Path) -> tuple[Path, Path]:
    config = load_config(config_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(config.data_dir)
    return config.data_dir / _PID_NAME, config.data_dir / _LOG_NAME


def discord_status(config_path: Path) -> dict[str, object]:
    pid_path, log_path = _runtime_paths(config_path)
    if not pid_path.is_file():
        return {"running": False, "log_path": str(log_path)}
    pid = int(pid_path.read_text(encoding="utf-8"))
    try:
        os.kill(pid, 0)
    except OSError:
        pid_path.unlink(missing_ok=True)
        return {"running": False, "log_path": str(log_path)}
    return {"running": True, "pid": pid, "log_path": str(log_path)}


def start_discord_bridge(config_path: Path) -> dict[str, object]:
    current = discord_status(config_path)
    if current["running"]:
        return current
    pid_path, log_path = _runtime_paths(config_path)
    with log_path.open("a", encoding="utf-8") as log:
        restrict_private_file(log_path)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "erga_mcp.discord_bridge",
                "--config",
                str(config_path.expanduser().absolute()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    restrict_private_file(pid_path)
    return {"running": True, "pid": process.pid, "log_path": str(log_path)}


def stop_discord_bridge(config_path: Path) -> dict[str, object]:
    current = discord_status(config_path)
    if not current["running"]:
        return current
    pid = current.get("pid")
    if not isinstance(pid, int):
        raise RuntimeError("Discord bridge status did not include a valid process ID")
    os.kill(pid, signal.SIGTERM)
    pid_path, log_path = _runtime_paths(config_path)
    pid_path.unlink(missing_ok=True)
    return {"running": False, "stopped_pid": pid, "log_path": str(log_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    return parser


def main() -> int:
    return run_discord_bridge(_parser().parse_args().config)


if __name__ == "__main__":
    raise SystemExit(main())
