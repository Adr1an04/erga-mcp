"""Declarative coding-agent adapters used by onboarding and the Discord bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ClientName = Literal[
    "codex",
    "claude-code",
    "opencode",
    "gemini-cli",
    "cursor-agent",
    "github-copilot",
    "generic-mcp",
]
McpFormat = Literal["codex", "mcp-servers", "opencode"]
OutputSource = Literal["stdout", "file"]


@dataclass(frozen=True)
class ClientAdapter:
    id: ClientName
    label: str
    executable: str | None
    mcp_format: McpFormat
    mcp_target: str
    include_stdio_type: bool
    status_arguments: tuple[str, ...] | None
    probe_arguments: tuple[str, ...]
    run_arguments: tuple[str, ...]
    output_source: OutputSource
    stripped_environment: tuple[str, ...]
    restart_step: str
    injected_environment: tuple[tuple[str, str], ...] = ()


CLIENT_ADAPTERS: dict[ClientName, ClientAdapter] = {
    "codex": ClientAdapter(
        id="codex",
        label="Codex / ChatGPT",
        executable="codex",
        mcp_format="codex",
        mcp_target=".codex/config.toml",
        include_stdio_type=False,
        status_arguments=("login", "status"),
        probe_arguments=(
            "exec",
            "--cd",
            "{project_dir}",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            "{output_path}",
            "{prompt}",
        ),
        run_arguments=(
            "exec",
            "--cd",
            "{project_dir}",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            "{output_path}",
            "{prompt}",
        ),
        output_source="file",
        stripped_environment=("OPENAI_API_KEY",),
        restart_step="Restart Codex or reopen this trusted project.",
    ),
    "claude-code": ClientAdapter(
        id="claude-code",
        label="Claude Code / Claude Pro or Max",
        executable="claude",
        mcp_format="mcp-servers",
        mcp_target=".mcp.json",
        include_stdio_type=True,
        status_arguments=("auth", "status"),
        probe_arguments=(
            "--print",
            "--output-format",
            "text",
            "--permission-mode",
            "plan",
            "--max-turns",
            "1",
            "{prompt}",
        ),
        run_arguments=(
            "--print",
            "--output-format",
            "text",
            "--permission-mode",
            "acceptEdits",
            "{prompt}",
        ),
        output_source="stdout",
        stripped_environment=("ANTHROPIC_API_KEY",),
        restart_step="Restart Claude Code or run /mcp to reconnect project servers.",
    ),
    "opencode": ClientAdapter(
        id="opencode",
        label="OpenCode",
        executable="opencode",
        mcp_format="opencode",
        mcp_target="opencode.json",
        include_stdio_type=False,
        status_arguments=("models",),
        probe_arguments=("run", "--dir", "{project_dir}", "--auto", "{prompt}"),
        run_arguments=("run", "--dir", "{project_dir}", "--auto", "{prompt}"),
        output_source="stdout",
        stripped_environment=(),
        restart_step="Restart OpenCode in this project.",
    ),
    "gemini-cli": ClientAdapter(
        id="gemini-cli",
        label="Gemini CLI / Google AI",
        executable="gemini",
        mcp_format="mcp-servers",
        mcp_target=".gemini/settings.json",
        include_stdio_type=False,
        status_arguments=None,
        probe_arguments=(
            "--approval-mode",
            "plan",
            "--output-format",
            "text",
            "--prompt",
            "{prompt}",
        ),
        run_arguments=(
            "--approval-mode",
            "yolo",
            "--allowed-mcp-server-names",
            "erga-mcp",
            "--output-format",
            "text",
            "--prompt",
            "{prompt}",
        ),
        output_source="stdout",
        stripped_environment=(
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_GENAI_USE_VERTEXAI",
        ),
        restart_step="Restart Gemini CLI in this project.",
    ),
    "cursor-agent": ClientAdapter(
        id="cursor-agent",
        label="Cursor Agent",
        executable="cursor-agent",
        mcp_format="mcp-servers",
        mcp_target=".cursor/mcp.json",
        include_stdio_type=False,
        status_arguments=("status",),
        probe_arguments=(
            "--print",
            "--mode",
            "plan",
            "--output-format",
            "text",
            "--trust",
            "{prompt}",
        ),
        run_arguments=(
            "--print",
            "--force",
            "--output-format",
            "text",
            "--trust",
            "--approve-mcps",
            "{prompt}",
        ),
        output_source="stdout",
        stripped_environment=("CURSOR_API_KEY",),
        restart_step="Restart Cursor Agent in this trusted project.",
    ),
    "github-copilot": ClientAdapter(
        id="github-copilot",
        label="GitHub Copilot CLI",
        executable="copilot",
        mcp_format="mcp-servers",
        mcp_target=".mcp.json",
        include_stdio_type=True,
        status_arguments=None,
        probe_arguments=("-p", "{prompt}", "--silent", "--no-ask-user"),
        run_arguments=(
            "-p",
            "{prompt}",
            "--silent",
            "--no-ask-user",
            "--allow-tool=erga-mcp",
        ),
        output_source="stdout",
        stripped_environment=(),
        restart_step="Restart GitHub Copilot CLI in this trusted project.",
        injected_environment=(("GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP", "true"),),
    ),
    "generic-mcp": ClientAdapter(
        id="generic-mcp",
        label="Other MCP-capable coding CLI",
        executable=None,
        mcp_format="mcp-servers",
        mcp_target=".mcp.json",
        include_stdio_type=True,
        status_arguments=None,
        probe_arguments=(),
        run_arguments=(),
        output_source="stdout",
        stripped_environment=(),
        restart_step="Restart the coding CLI and verify that it loads the project .mcp.json.",
    ),
}

SUPPORTED_CLIENTS: tuple[ClientName, ...] = tuple(CLIENT_ADAPTERS)
PRESET_CLIENTS: tuple[ClientName, ...] = tuple(
    client for client in SUPPORTED_CLIENTS if client != "generic-mcp"
)


def client_adapter(client: ClientName) -> ClientAdapter:
    try:
        return CLIENT_ADAPTERS[client]
    except KeyError as error:
        raise ValueError(f"unsupported coding client: {client}") from error
