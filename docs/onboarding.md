# Onboard Erga

Erga's onboarding goal is simple: reach one successful `pipeline_status` call before asking a new
user to configure résumé templates, mail, trackers, or any optional integration.

## One command

After cloning the repository and running `uv sync`, choose the coding client that will supply the
AI reasoning:

```bash
uv run erga onboard codex \
  --project-dir /absolute/path/to/your/resume-workspace
```

Valid client names are `codex`, `claude-code`, and `opencode`.

The command performs four bounded local actions:

1. Creates or reuses `~/.config/erga-mcp/config.toml`.
2. Initializes the private SQLite database under `~/.config/erga-mcp/state/`.
3. Adds Erga to the selected project's native MCP configuration.
4. Runs core health checks and prints exact next steps.

It does not request a model API key, read a résumé, connect an inbox, submit an application, or
send anything remotely.

## Verify the first success

Restart the selected coding client or reload its project, then ask:

> Show my Erga pipeline status.

The client should call `pipeline_status` and report zero or more local records. This proves that the
client can start Erga and communicate through MCP. A résumé template and `latexmk` are optional at
this stage, so warnings about them do not mean onboarding failed.

Next, paste a public job-posting URL and ask the client to run Erga intake. Configure the résumé
template before expecting a compiled tailored PDF.

## What is written

| Purpose | Default location |
| --- | --- |
| Private Erga settings | `~/.config/erga-mcp/config.toml` |
| Private database | `~/.config/erga-mcp/state/erga.sqlite3` |
| Codex project entry | `<project>/.codex/config.toml` |
| Claude Code project entry | `<project>/.mcp.json` |
| OpenCode project entry | `<project>/opencode.json` |

The MCP entry contains the Erga executable path, the non-secret Erga configuration path, and
`ERGA_MCP_TOOL_PROFILE=career`. Model authentication stays entirely with the coding client.

## Safe reruns and existing projects

`erga onboard` is idempotent. If both the private configuration and identical MCP entry already
exist, it reuses them and reports that nothing needed to be rewritten.

If the same MCP server name exists with different settings, onboarding stops instead of replacing
it. Review the existing file and use the advanced preview command to compare configurations:

```bash
uv run erga client configure codex \
  --project-dir /absolute/path/to/your/resume-workspace
```

Add `--write` only when manually configuring a project that has no conflicting `erga-mcp` entry.

## Useful options

Use another private configuration location:

```bash
uv run erga onboard codex \
  --config /absolute/private/path/erga.toml \
  --project-dir /absolute/path/to/your/resume-workspace
```

Return machine-readable output when another tool or coding agent is performing setup:

```bash
uv run erga onboard codex \
  --project-dir /absolute/path/to/your/resume-workspace \
  --json
```

Select an explicit server executable when the automatic installed-command lookup is unsuitable:

```bash
uv run erga onboard codex \
  --project-dir /absolute/path/to/your/resume-workspace \
  --server-command /absolute/path/to/erga-mcp
```

## Common problems

### The coding-client command is not found

Onboarding can create project configuration before the client executable is available. Install or
launch the selected client, ensure its command is on `PATH`, and rerun onboarding.

### The project directory does not exist

Create or clone the résumé workspace first, then pass its absolute directory to `--project-dir`.
Erga does not create a project on the user's behalf.

### A server with the same name already exists

Erga found a non-identical `erga-mcp` entry and protected it from replacement. Inspect the project
configuration listed in the error. Remove or rename the stale entry only after confirming it is no
longer needed.

### OpenCode already uses a JSONC or `.opencode` configuration

Erga will not create a competing `opencode.json` with different precedence. Preview the generated
entry with `erga client configure opencode` and merge it into the existing OpenCode configuration.

### Résumé template or `latexmk` is unavailable

Core onboarding still succeeded. Configure résumé generation later with `erga resume settings set`;
the [complete getting-started guide](getting-started.md) shows every option.
