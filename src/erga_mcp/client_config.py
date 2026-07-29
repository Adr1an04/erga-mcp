"""Portable MCP client configuration for subscription-backed coding agents."""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ClientName = Literal["codex", "claude-code", "opencode"]
SUPPORTED_CLIENTS: tuple[ClientName, ...] = ("codex", "claude-code", "opencode")
DEFAULT_SERVER_NAME = "erga-mcp"
DEFAULT_TOOL_PROFILE = "career"


@dataclass(frozen=True)
class ClientConfiguration:
    client: ClientName
    target_path: Path
    server_name: str
    tool_profile: str
    content: str


def resolve_server_command(explicit: Path | None = None) -> Path:
    """Resolve the installed erga-mcp executable without assuming a source checkout."""
    if explicit is not None:
        candidate = explicit.expanduser().absolute()
        if not candidate.is_file():
            raise FileNotFoundError(f"Erga MCP server command does not exist: {candidate}")
        return candidate
    discovered = shutil.which("erga-mcp")
    if discovered is not None:
        return Path(discovered).absolute()
    launcher_sibling = Path(sys.argv[0]).resolve().parent / "erga-mcp"
    if launcher_sibling.is_file():
        return launcher_sibling
    sibling = Path(sys.executable).resolve().parent / "erga-mcp"
    if sibling.is_file():
        return sibling
    raise FileNotFoundError(
        "Could not find the erga-mcp executable. Run this command from `uv run erga ...` "
        "or pass --server-command."
    )


def _quoted(value: str) -> str:
    return json.dumps(value)


def _codex_content(
    *,
    command: Path,
    server_args: tuple[str, ...],
    config_path: Path,
    server_name: str,
    tool_profile: str,
) -> str:
    args = ", ".join(_quoted(value) for value in server_args)
    return (
        f"[mcp_servers.{_quoted(server_name)}]\n"
        f"command = {_quoted(str(command))}\n"
        f"args = [{args}]\n"
        "startup_timeout_sec = 60\n"
        "tool_timeout_sec = 300\n"
        'default_tools_approval_mode = "writes"\n\n'
        f"[mcp_servers.{_quoted(server_name)}.env]\n"
        f"ERGA_MCP_CONFIG = {_quoted(str(config_path))}\n"
        f"ERGA_MCP_TOOL_PROFILE = {_quoted(tool_profile)}\n"
    )


def _claude_content(
    *,
    command: Path,
    server_args: tuple[str, ...],
    config_path: Path,
    server_name: str,
    tool_profile: str,
) -> str:
    return (
        json.dumps(
            {
                "mcpServers": {
                    server_name: {
                        "type": "stdio",
                        "command": str(command),
                        "args": list(server_args),
                        "env": {
                            "ERGA_MCP_CONFIG": str(config_path),
                            "ERGA_MCP_TOOL_PROFILE": tool_profile,
                        },
                    }
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _opencode_content(
    *,
    command: Path,
    server_args: tuple[str, ...],
    config_path: Path,
    project_dir: Path,
    server_name: str,
    tool_profile: str,
) -> str:
    return (
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    "servers": {
                        server_name: {
                            "type": "local",
                            "command": [str(command), *server_args],
                            "cwd": str(project_dir),
                            "environment": {
                                "ERGA_MCP_CONFIG": str(config_path),
                                "ERGA_MCP_TOOL_PROFILE": tool_profile,
                            },
                        }
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def render_client_configuration(
    client: ClientName,
    *,
    project_dir: Path,
    config_path: Path,
    server_command: Path,
    server_args: tuple[str, ...] = (),
    server_name: str = DEFAULT_SERVER_NAME,
    tool_profile: str = DEFAULT_TOOL_PROFILE,
) -> ClientConfiguration:
    """Render a project-scoped, no-model-API MCP configuration."""
    project_dir = project_dir.expanduser().absolute()
    config_path = config_path.expanduser().absolute()
    server_command = server_command.expanduser().absolute()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Erga config does not exist: {config_path}. Run `erga init --config ...` first."
        )
    if not project_dir.is_dir():
        raise NotADirectoryError(f"Project directory does not exist: {project_dir}")
    if not server_name.strip():
        raise ValueError("server_name cannot be empty")
    if tool_profile not in {"career", "default", "read", "research", "write", "hermes"}:
        raise ValueError("unsupported Erga MCP tool profile")

    if client == "codex":
        target = project_dir / ".codex" / "config.toml"
        content = _codex_content(
            command=server_command,
            server_args=server_args,
            config_path=config_path,
            server_name=server_name,
            tool_profile=tool_profile,
        )
    elif client == "claude-code":
        target = project_dir / ".mcp.json"
        content = _claude_content(
            command=server_command,
            server_args=server_args,
            config_path=config_path,
            server_name=server_name,
            tool_profile=tool_profile,
        )
    elif client == "opencode":
        target = project_dir / "opencode.json"
        content = _opencode_content(
            command=server_command,
            server_args=server_args,
            config_path=config_path,
            project_dir=project_dir,
            server_name=server_name,
            tool_profile=tool_profile,
        )
    else:
        raise ValueError(f"unsupported MCP client: {client}")
    return ClientConfiguration(
        client=client,
        target_path=target,
        server_name=server_name,
        tool_profile=tool_profile,
        content=content,
    )


def _merge_json(existing: str, generated: str, *, client: ClientName, server_name: str) -> str:
    document = json.loads(existing) if existing.strip() else {}
    addition = json.loads(generated)
    if not isinstance(document, dict):
        raise ValueError("existing client configuration must contain a JSON object")
    if client == "claude-code":
        servers = document.setdefault("mcpServers", {})
        generated_server = addition["mcpServers"][server_name]
    else:
        mcp = document.setdefault("mcp", {})
        servers = mcp.setdefault("servers", {})
        generated_server = addition["mcp"]["servers"][server_name]
        document.setdefault("$schema", "https://opencode.ai/config.json")
    if not isinstance(servers, dict):
        raise ValueError("existing MCP server configuration must contain a JSON object")
    if server_name in servers:
        raise ValueError(f"MCP server {server_name!r} already exists; refusing to overwrite it")
    servers[server_name] = generated_server
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _merge_codex(existing: str, generated: str, *, server_name: str) -> str:
    if existing.strip():
        document = tomllib.loads(existing)
        servers = document.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("existing Codex mcp_servers configuration must be a TOML table")
        if server_name in servers:
            raise ValueError(f"MCP server {server_name!r} already exists; refusing to overwrite it")
    separator = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + separator + "# Added by `erga client configure codex --write`.\n" + generated


def write_client_configuration(configuration: ClientConfiguration) -> dict[str, object]:
    """Merge one Erga server entry while preserving unrelated client settings."""
    target = configuration.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if configuration.client == "opencode" and not target.exists():
        alternatives = (
            target.with_suffix(".jsonc"),
            target.parent / ".opencode" / "opencode.json",
            target.parent / ".opencode" / "opencode.jsonc",
        )
        configured_alternative = next((path for path in alternatives if path.exists()), None)
        if configured_alternative is not None:
            raise ValueError(
                f"OpenCode configuration already exists at {configured_alternative}; "
                "preview the generated entry and merge it there instead of creating a "
                "second-precedence configuration"
            )
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if configuration.client == "codex":
        merged = _merge_codex(
            existing,
            configuration.content,
            server_name=configuration.server_name,
        )
    else:
        merged = _merge_json(
            existing,
            configuration.content,
            client=configuration.client,
            server_name=configuration.server_name,
        )
    target.write_text(merged, encoding="utf-8")
    return {
        "already_configured": False,
        "client": configuration.client,
        "model_api_required": False,
        "server_name": configuration.server_name,
        "target_path": str(target),
        "tool_profile": configuration.tool_profile,
        "written": True,
    }


def _configured_server(configuration: ClientConfiguration, content: str) -> object:
    if configuration.client == "codex":
        document = tomllib.loads(content)
        if not isinstance(document, dict):
            return None
        servers = document.get("mcp_servers", {})
    else:
        document = json.loads(content)
        if not isinstance(document, dict):
            return None
        if configuration.client == "claude-code":
            servers = document.get("mcpServers", {})
        else:
            mcp = document.get("mcp", {})
            servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
    if not isinstance(servers, dict):
        return None
    return servers.get(configuration.server_name)


def ensure_client_configuration(configuration: ClientConfiguration) -> dict[str, object]:
    """Create an MCP entry or reuse an identical entry during idempotent onboarding."""
    target = configuration.target_path
    if target.exists():
        existing_server = _configured_server(
            configuration,
            target.read_text(encoding="utf-8"),
        )
        generated_server = _configured_server(configuration, configuration.content)
        if existing_server == generated_server:
            return {
                "already_configured": True,
                "client": configuration.client,
                "model_api_required": False,
                "server_name": configuration.server_name,
                "target_path": str(target),
                "tool_profile": configuration.tool_profile,
                "written": False,
            }
    return write_client_configuration(configuration)
