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
- Attach Files
- Read Message History

Then run the guided connection:

```bash
uv run erga discord configure --config ~/.config/erga-mcp/config.toml
```

The normal wizard auto-detects a supported signed-in local AI app, verifies that it can reply, and
uses the current Erga folder. Users never enter commands, argument arrays, or assistant protocol
settings. If several signed-in apps are available, the wizard asks which account to use. Runtime
and custom executable controls remain available only through `--advanced` for maintainers.

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

Once connected, no command vocabulary is required. A user can send `help`, paste a job link, or say
“Tailor my résumé for this job: <link>”. Erga shows plain-language progress, attaches only a PDF
that passed its checks, and repeats that nothing was submitted. A bare job link defaults to résumé
tailoring unless the message clearly asks for research, summarization, or tracking instead.

### Update from Discord

When Erga is running from the supported Git checkout, a trusted user can write `@Erga update` in a
server or `update` in a direct message. The command bypasses the reasoning backend and checks only
`origin/main` for the official `Adr1an04/erga-mcp` GitHub repository. It refuses non-`main`
branches, tracked local changes, divergent history, detached checkouts, and remotes that do not
match the official repository. A safe update uses a Git fast-forward, runs
`uv sync --extra discord --frozen`, and restarts the bridge into the updated code. Discord keeps
the interaction compact: one small status card reports “Erga updated,” “Erga is current,” or
“Try again,” with a semantic color and no technical detail. Technical details remain in the private
bridge log.
A clean local `main` checkout that is already ahead of GitHub is also treated as current rather than
being overwritten.

Git-installed package copies do not expose a working checkout to the bridge, so they are reported
as unsupported rather than being modified. Reinstall those copies from the official repository.

Hermes users can explicitly opt into the same guarded update path with `/erga-updates on`. Erga
installs a zero-model-token runner and asks Hermes to poll `origin/main` every 15 minutes. The job
stays silent when the checkout is current, refuses dirty/non-main/divergent/unofficial checkouts,
updates frozen dependencies after a fast-forward, refreshes the installed Erga router files, and
requests a profile-scoped gateway restart. `/erga-updates status` shows whether the job exists;
`/erga-updates off` removes it. Ordinary installation creates no automatic-update job.

## Erga Orbit

Send `orbit` (or `erga orbit`) to create the live aggregate application-flow dashboard in the
current channel. `orbit Summer 2027` limits it to one exact recruiting cycle. Erga keeps one Orbit
message per channel: repeating the command edits that message instead of posting another, and the
bridge checks every 60 seconds but uploads a replacement image only when the underlying tracker
state changes. Send `orbit stop` to disable those updates.

Orbit is deterministic and does not call an AI model. It begins at **Applications** and renders a
strict binary outcome tree: every internal node has at most two children, first separating open
from closed applications and then recursively separating waiting, active-pipeline, offer-decision,
rejection, and administrative outcomes. Draft, researching, and ready-to-apply rows are excluded.
Recorded local status events decide the truthful leaf; missing history never invents intermediate
recruiting rounds. The image contains aggregate counts, not employer names. It is also available
locally with `erga tracker orbit` and through the `application_orbit` MCP tool. A Hermes-managed
Discord connection can use `/erga-orbit [cycle]` or the **Orbit** control in `/erga-tracker` for an
on-demand snapshot. Hermes attaches the PNG natively and never prints its local path. Preview files
are deleted from the Erga host after Discord confirms upload by default; the ✅/❌ controls on the
preview and the **Orbit images** action in `/erga-settings` change that preset for future renders.

After `/intake-job` or a pasted job link generates and attaches a validated résumé, the same Discord
message asks whether the application was actually submitted. ✅ **Applied with this résumé** moves
that exact application to Applied and records the immutable, validated résumé version; **Applied
another way** moves it without claiming the generated file was used; ❌ **Still drafting** keeps it
out of the submitted funnel. The confirmation is application-bound and rejects stale or mismatched
résumé versions before changing status. Each choice also synchronizes an unambiguous configured
Obsidian tracker row.

Job intake does not ask users to rank or choose projects. Erga compares the complete eligible
shortlist for required-role fit, approved evidence strength, bullet quality, and differentiation,
then the connected model chooses the strongest portfolio during generation. The user reviews the
tailoring strategy and final résumé; clients without model sampling use the deterministic
approved-copy selector.

If local generation fails before a validated PDF exists, the response includes a fresh **Retry
generation** control bound to the same reviewed plan. Failed controls are never the user's only way
forward.

Later high-confidence recruiting mail can advance the canonical record and is mirrored back into
the tracker. Ambiguous, older, or sensitive messages stay unchanged in a metadata-only review
queue. Hermes users can open that queue with `/erga-mail-review`, choose a candidate application or
ignore the message, and use `/erga-mail-review retry` after adding older application records. No
mail body is retained by the reconciliation record, and these controls never send email or mutate a
remote mailbox.

## Live request experience and color system

Erga acknowledges an accepted request immediately with one live Discord card. For résumé work, the
card shows the evidence/tailoring/validation pipeline, a truthful current status, elapsed time, and
the review-only safety boundary. It refreshes in place every 12 seconds while the local backend
works, then becomes the final result card. This avoids both a silent multi-minute wait and a channel
full of disposable progress messages. When Erga returns a validated PDF artifact, that same final
card shows a rendered first-page preview and carries the PDF as a Discord attachment, so the result
can be reviewed or downloaded without finding a local path. Erga only attaches regular PDF files
inside its configured data or résumé output directories and only from an `artifacts` package; a path
merely mentioned by an untrusted job page or reasoning response is never uploaded. Long results
continue in matching detail cards.

The visual system follows a 60–30–10 hierarchy derived from Erga's existing wordmark, onboarding,
and orbit mark:

- **60% — Erga Ink (`#171717`)** comes from the wordmark and provides the structural foundation.
- **30% — Orbit Violet (`#7C5CFF`)** identifies active work and live progress.
- **10% — orbit accents** communicate outcomes: Leaf (`#83FE7F`) for validated/ready, Sun
  (`#FEF17F`) for review-required, Coral (`#FE7F7F`) for a stopped turn, and Sky (`#7FC2FE`) for
  continuation details.

The Orbit flow image uses this same palette end to end and includes the complete Erga mark and
wordmark. Counts and outcome labels are rendered as separate, deliberately spaced lines so dense
branches remain readable in Discord previews as well as full-size exports.

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
