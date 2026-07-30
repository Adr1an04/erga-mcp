# Onboard Erga

Erga's onboarding goal is simple: connect the coding AI subscription a student already has,
optionally connect Discord, and reach one successful `pipeline_status` call without asking for a
model API key or another agent runtime.

## Guided setup

After cloning the repository and running `uv sync`, launch:

```bash
uv run erga setup
```

Use the arrow keys and Enter to choose:

- **Full Erga:** coding AI, résumé generation, and native Discord.
- **Local Erga:** coding AI and résumé generation.
- **Custom:** select only the components you need.

The wizard includes maintained adapters for `codex`, `claude-code`, `opencode`, `gemini-cli`,
`cursor-agent`, and `github-copilot`. It verifies that the selected command is installed, checks
recorded login state when the client exposes it, and performs one tiny live readiness turn before
writing configuration. This catches expired or revoked sessions that can still appear signed in.
The final review shows the workspace, private config location, and selected components. Canceling
before confirmation makes no changes.

Immediately after the coding-AI choice, Erga locates the client and runs a minimal subscription
readiness check. On macOS, Codex is discovered either on `PATH` or inside the installed ChatGPT or
Codex desktop application. Setup stops with an actionable message at this point if the client is
missing or signed out—before asking for résumé files or Discord credentials.

For résumé generation, no manual path navigation is required:

1. Drag the complete master PDF, DOCX, or `.tex` file into the terminal and press Enter. A
   multi-page master is expected: Erga extracts every page as user-approved factual knowledge and
   creates a hash-verified private snapshot.
2. Keep **No**—the recommended default—unless you are confident another résumé's page length,
   section order, and density are better and should define every generated résumé.
3. Only in that case, answer **Yes** and drag it in as an explicit style override.

Quoted paths and the backslash-escaped spaces inserted by terminal file drops are normalized
automatically. Erga chooses `<workspace>/erga-applications` as the output directory, so onboarding
does not ask a new user to make that implementation decision. The optional style résumé is a
formatting reference only and cannot introduce factual claims.

Both submitted files are copied under the private Erga state directory using their SHA-256 content
hash. Configuration points to those managed copies, so the originals may later be renamed, moved,
or removed without breaking `resume_source_context`. Erga records the original path only in a
private provenance manifest and never modifies the original.

A dragged `.tex` master is imported as durable factual knowledge, not silently assumed to be a
complete editable template project. LaTeX documents may depend on nearby images, macros, fonts, or
other files, so configure `template_path` separately to a template bundle you intend Erga to use
for compiled proposals.

An advanced **Other MCP-capable coding CLI** option accepts an executable and a JSON argument
template. Erga executes that array directly without a shell and substitutes standalone
`{prompt}`, `{project_dir}`, and `{output_path}` entries. It generates portable project
`.mcp.json`, but the user must verify that the unknown client discovers that file and uses the
intended subscription or provider.

It never requests a model API key. For maintained clients with known provider-key variables, the
bridge removes those variables from the child process so an existing key cannot silently replace
subscription authentication.

Preview the redacted setup plan without applying it:

```bash
uv run erga setup --dry-run
```

## Native Discord

The Full experience connects a Discord bot directly to the selected coding CLI. The bot token goes
to the operating-system credential store; only the client name, workspace path, and allowlisted
Discord usernames or IDs are written to disk. Erga does not require Hermes or another intermediary
agent.

Discord requires one manual account-level step: create an application and bot in the Discord
Developer Portal, enable Message Content Intent, and copy the bot token. Enter the current unique
Discord username—for example, `student.dev`—when the wizard asks who may use the bot. Stable
numeric account IDs are also accepted but are no longer required. See [Native Discord](discord.md)
for the exact setup, permissions, commands, and trust boundary.

## Noninteractive onboarding

Automation, development environments, and users who do not need the complete wizard can configure
one coding client directly:

```bash
uv run erga onboard codex \
  --project-dir /absolute/path/to/your/resume-workspace
```

Valid client names are `codex`, `claude-code`, `opencode`, `gemini-cli`, `cursor-agent`,
`github-copilot`, and `generic-mcp`. This command:

1. Creates or reuses `~/.config/erga-mcp/config.toml`.
2. Initializes the private SQLite database under `~/.config/erga-mcp/state/`.
3. Adds Erga to the selected project's native MCP configuration.
4. Runs core health checks and prints exact next steps.

It does not configure Discord or prompt for résumé settings.

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
| Native Discord settings | `~/.config/erga-mcp/discord-bridge.json` |
| Discord bot token | Operating-system credential store |
| Managed résumé snapshots | `~/.config/erga-mcp/state/resume-sources/<sha256>/` |
| Master résumé | Hash-verified private copy; extracted as approved knowledge |
| Optional style résumé | Hash-verified private copy; never factual evidence |
| Snapshot provenance | Private `master.json` / `style.json` beside each managed copy |
| Codex project entry | `<project>/.codex/config.toml` |
| Claude Code project entry | `<project>/.mcp.json` |
| OpenCode project entry | `<project>/opencode.json` |
| Gemini CLI project entry | `<project>/.gemini/settings.json` |
| Cursor Agent project entry | `<project>/.cursor/mcp.json` |
| GitHub Copilot CLI project entry | `<project>/.mcp.json` |
| Generic MCP project entry | `<project>/.mcp.json` |

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

Install or launch the selected client, ensure its command is on `PATH`, sign in with the account
that owns your coding-tool subscription, and rerun setup.

### The coding client is installed but not signed in

Run the selected client's login flow, such as `codex login`, `claude auth login`, `gemini`,
`cursor-agent login`, or `copilot login`. Erga does not accept a model API key as a substitute for
maintained subscription-backed presets during guided setup.

### Discord starts and immediately stops

Run `uv run erga discord status`, then inspect the reported log path. The usual causes are an
invalid bot token or Message Content Intent not being enabled in the Discord Developer Portal.

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
