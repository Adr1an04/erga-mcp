"""Explicit headless coding-host adapters for the optional Discord bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiscordBackendName = Literal[
    "codex",
    "claude-code",
    "opencode",
    "opencode-v2",
    "gemini-cli",
    "cursor",
    "github-copilot",
    "custom",
]
OutputSource = Literal["stdout", "file"]


@dataclass(frozen=True)
class DiscordBackend:
    id: DiscordBackendName
    label: str
    executable: str | None
    status_arguments: tuple[str, ...] | None
    probe_arguments: tuple[str, ...]
    run_arguments: tuple[str, ...]
    output_source: OutputSource
    injected_environment: tuple[tuple[str, str], ...] = ()


DISCORD_BACKENDS: dict[DiscordBackendName, DiscordBackend] = {
    "codex": DiscordBackend(
        id="codex",
        label="Codex / ChatGPT",
        executable="codex",
        status_arguments=("login", "status"),
        probe_arguments=(
            "exec",
            "--ephemeral",
            "--model",
            "gpt-5.6-terra",
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
            "--ephemeral",
            "--model",
            "gpt-5.6-terra",
            "--cd",
            "{project_dir}",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            "{output_path}",
            "{prompt}",
        ),
        output_source="file",
    ),
    "claude-code": DiscordBackend(
        id="claude-code",
        label="Claude Code",
        executable="claude",
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
    ),
    "opencode": DiscordBackend(
        id="opencode",
        label="OpenCode",
        executable="opencode",
        status_arguments=("models",),
        probe_arguments=("run", "--dir", "{project_dir}", "{prompt}"),
        run_arguments=("run", "--dir", "{project_dir}", "{prompt}"),
        output_source="stdout",
    ),
    "opencode-v2": DiscordBackend(
        id="opencode-v2",
        label="OpenCode V2",
        executable="opencode2",
        status_arguments=None,
        probe_arguments=("run", "--dir", "{project_dir}", "{prompt}"),
        run_arguments=("run", "--dir", "{project_dir}", "{prompt}"),
        output_source="stdout",
    ),
    "gemini-cli": DiscordBackend(
        id="gemini-cli",
        label="Gemini CLI",
        executable="gemini",
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
    ),
    "cursor": DiscordBackend(
        id="cursor",
        label="Cursor Agent",
        executable="cursor-agent",
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
    ),
    "github-copilot": DiscordBackend(
        id="github-copilot",
        label="GitHub Copilot CLI",
        executable="copilot",
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
        injected_environment=(("GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP", "true"),),
    ),
    "custom": DiscordBackend(
        id="custom",
        label="Other headless coding CLI (advanced)",
        executable=None,
        status_arguments=None,
        probe_arguments=(),
        run_arguments=(),
        output_source="stdout",
    ),
}

PRESET_DISCORD_BACKENDS: tuple[DiscordBackendName, ...] = tuple(
    name for name in DISCORD_BACKENDS if name != "custom"
)


def discord_backend(name: DiscordBackendName) -> DiscordBackend:
    try:
        return DISCORD_BACKENDS[name]
    except KeyError as error:
        raise ValueError(f"unsupported Discord reasoning backend: {name}") from error
