# Optional Discord bridge

The Discord bridge is a power-up around Erga's complete local system. It is not part of the core
installation contract: résumé knowledge, private application state, CLI commands, and the MCP
server remain usable when Discord is absent, stopped, misconfigured, or deleted. Any optional
Obsidian projection is independent of Discord as well.

Use this bridge when you want Discord access through a headless coding tool you already use—such
as Codex, Claude Code, or OpenCode—and you do **not** already have a messaging gateway such as
Hermes or OpenClaw managing Discord. If an existing gateway already owns your Discord connection,
connect Erga's MCP server to that gateway instead of running a second bot and bridge.

## Install and configure

Install the bridge's isolated dependency:

```bash
uv sync --extra discord
```

Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications), enable
Message Content Intent, and invite it with only:

- View Channels
- Send Messages
- Embed Links
- Read Message History

Then run:

```bash
uv run erga discord configure \
  --config ~/.config/erga-mcp/config.toml \
  --project-dir /absolute/path/to/project
```

The wizard asks which local coding CLI should execute unattended Discord turns. Presets cover
Codex, Claude Code, OpenCode, OpenCode V2, Gemini CLI, Cursor Agent, and GitHub Copilot CLI. The
advanced custom option accepts an executable and a JSON argument array, runs it without a shell,
and requires a `{prompt}` placeholder. `{project_dir}` and `{output_path}` are also available.

This backend selection belongs only to Discord. You can use no other coding assistant, connect
several through `erga connect`, or replace the Discord backend later.

## Login and credentials

The optional readiness check runs one minimal headless turn using the coding tool's existing local
login. Every backend, including the advanced custom backend, receives a strict allowlist of basic
operating-system and runtime variables. Arbitrary parent-process variables and credentials are not
inherited, so a bridge turn cannot silently consume an ambient model key or unrelated secret.

The Discord bot token is hidden during entry and stored in the operating-system credential store.
It never appears in Erga's TOML configuration, `discord-bridge.json`, process arguments, or project
MCP files.

Discord now uses unique usernames without four-digit discriminators. Enter a username such as
`emperor_sai`, a stable numeric user ID, or comma-separated values for several trusted people.
`name#1234` is rejected because it is no longer the current identity format. Numeric IDs remain the
more stable authorization choice if a user might rename their account.

## Run and manage

Test in the foreground first:

```bash
uv run erga discord run --config ~/.config/erga-mcp/config.toml
```

Then use the optional background lifecycle:

```bash
uv run erga discord connect --config ~/.config/erga-mcp/config.toml
uv run erga discord status --config ~/.config/erga-mcp/config.toml
uv run erga discord stop --config ~/.config/erga-mcp/config.toml
```

`connect` reuses the existing settings and keyring token after a restart and returns only after the
Discord gateway reports ready. If Discord rotates the token, run `erga discord set-token` and
connect again; full setup is unnecessary.

Direct messages from trusted users are accepted. Server messages require an explicit bot mention
unless the owner knowingly disables that safeguard during configuration. Bot-authored messages
are always ignored, only one backend turn runs at a time, incoming content is bounded, and long
responses are split below Discord's message limit.

## Live request experience and color system

Erga acknowledges an accepted request immediately with one live Discord card. For résumé work, the
card shows the evidence/tailoring/validation pipeline, a truthful current status, elapsed time, and
the review-only safety boundary. It refreshes in place every 12 seconds while the local backend
works, then becomes the final result card. This avoids both a silent multi-minute wait and a channel
full of disposable progress messages. Long results continue in matching detail cards.

The visual system follows a 60–30–10 hierarchy derived from Erga's existing wordmark, onboarding,
and orbit mark:

- **60% — Erga Ink (`#171717`)** comes from the wordmark and provides the structural foundation.
- **30% — Orbit Violet (`#7C5CFF`)** identifies active work and live progress.
- **10% — orbit accents** communicate outcomes: Leaf (`#83FE7F`) for validated/ready, Sun
  (`#FEF17F`) for review-required, Coral (`#FE7F7F`) for a stopped turn, and Sky (`#7FC2FE`) for
  continuation details.

Discord owns the light or dark message canvas, so Erga applies this hierarchy to the embed rail,
titles, fields, and status language rather than forcing a background color that may become
unreadable in the user's theme. Progress text never claims a pipeline stage has completed unless
Erga has actually returned the result.

Résumé requests that contain a job URL are routed through Erga's canonical `intake_job_url`
operation. The reasoning backend is instructed not to hand-edit generated files or invoke a PDF
renderer directly, and it may report a PDF as ready only after Erga's one-page fill validation
succeeds. The same rule is injected for every supported reasoning backend.

Private runtime settings live beside Erga's private config. Logs and the nonce-bearing background
process record live in Erga's owner-only data directory.

Codex-backed turns use `--dangerously-bypass-approvals-and-sandbox` because a background process
cannot answer MCP approval prompts. An allowlisted Discord identity therefore has the permissions
of the OS account running Erga, not merely access to the configured project. Use a private bot and
the smallest possible allowlist. Each invocation also uses `--ephemeral`, so Codex does not persist
session rollout files and no Discord turn is resumed by a later message. Both readiness checks and
real bridge turns explicitly select `gpt-5.6-terra` for predictable everyday latency and tool use.

## Failure boundaries

A missing Discord package, bot token, coding CLI, login, or process affects only the bridge. Every
related error states that the local core remains ready. Re-running `erga discord configure` safely
replaces the optional settings and credential; it does not re-import résumé knowledge or rewrite
an optional Obsidian workspace.

The bridge may prepare local research, records, and résumé proposals through Erga. It never grants
authority to submit an application, send employer messages, approve invented evidence, or mutate
remote mail.
