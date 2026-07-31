# ADR-004: Optional, Multiplexed MCP Host Connections

- **Status:** Proposed
- **Date:** 2026-07-30

## Context

Erga's core is usable through its CLI and stores its authoritative data in private SQLite state and
managed source snapshots. Obsidian is an optional projection, and coding assistants are optional
reasoning interfaces. Requiring one host during setup would make an unrelated installation,
authentication, subscription, or vendor outage block the local career system.

Users may also use several assistants. Treating setup as one required `client` field artificially
restricts them and makes a later Discord bridge appear to own Erga's reasoning layer.

Host configuration formats are not completely interchangeable. Codex uses project
`.codex/config.toml`; several clients use `mcpServers`; OpenCode classic and OpenCode V2 currently
use different nested MCP shapes.

## Decision

Add an optional `erga connect` layer with these properties:

- selecting no host is a complete and supported outcome;
- users may configure one or several hosts independently;
- presets cover Codex, Claude Code, OpenCode classic, OpenCode V2, Gemini CLI, Cursor, GitHub
  Copilot CLI, and a standard `.mcp.json` fallback;
- the connection step renders or safely merges project-scoped local stdio configuration;
- no host executable, login, subscription, model choice, or API key is required;
- executable discovery is informational only;
- identical shared entries are reused, while conflicting entries, symlinks, and ambiguous OpenCode
  precedence are rejected instead of overwritten.

Core setup completes before offering these connections, with the opt-in prompt defaulting to no.
A failed optional connection reports its own failure while explicitly preserving core readiness.

## Rationale

This gives users control without forcing them to understand MCP configuration syntax. Keeping the
layer declarative also prevents connection convenience from becoming a hidden model invocation or
credential check.

Supporting both active OpenCode schemas explicitly is safer than guessing which installed product a
user means or silently writing a configuration accepted by only one generation.

## Consequences

- Project-scoped host files contain a machine-specific path to private Erga configuration and
  should be reviewed before being committed.
- Host-specific authentication remains outside Erga.
- Communication bridges may select one configured host as their execution backend, but cannot make
  that host part of core.

## Sources

- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [Claude Code MCP](https://docs.anthropic.com/en/docs/claude-code/mcp)
- [OpenCode MCP](https://opencode.ai/docs/mcp-servers)
- [OpenCode V2 MCP](https://opencode.ai/v2/docs/mcp-servers)
- [Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/)
- [Cursor MCP](https://docs.cursor.com/context/mcp)
- [GitHub Copilot CLI MCP](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
