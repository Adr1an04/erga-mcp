# Getting started

## 1. Install and start Erga

Erga is not published to a package registry yet, so `uvx erga` will not work. Use one of these
supported paths instead.

### Clone and run from the repository

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync --extra dev
uv run erga setup --config ~/.config/erga-mcp/config.toml
```

### Install the current GitHub version as a command

```bash
uv tool install git+https://github.com/Adr1an04/erga-mcp.git
erga setup --config ~/.config/erga-mcp/config.toml
```

The arrow-key setup initializes Erga's private state and local application tracking, imports a
complete master résumé, and enables the least-privilege `career` MCP profile. The generated
configuration and SQLite data directory live outside the repository. Do not place personal paths,
tokens, or imports in Git.

The wizard separates résumé input into three explicit decisions:

1. **Master résumé — factual source.** Erga may use its user-approved content for claims and reads
   every page of a PDF.
2. **Optional style résumé — non-factual reference.** Supply one only when you are confident it is
   useful layout guidance. A PDF page count prefills the maximum-page setting; observed section
   order and density guide a private standalone `.tex` template generated from the master. The
   reference file contributes no factual text, and users do not need to supply LaTeX.
3. **Resume shape.** Keep the recommended one-page and 90/105/120-character bullet settings,
   enter page and minimum/target/maximum bullet limits directly, disable bullet limits, or paste
   one or two example bullets to calibrate the numeric range. Calibration discards the examples
   and stores only the resulting numbers. Maximum pages are enforced during compilation; the
   character range is enforced when CLI or MCP workflows author new `\resumeItem{...}` bullets.
   Automatic intake also measures each rendered bullet with the configured LaTeX template: a
   wrapping project is replaced by the next approved relevant project, and a package is never
   published while any bullet still needs a second line.

For a PDF or DOCX master, Erga preserves visual PDF line boundaries, reconstructs semantic
experience/project groups, and generates the private editable template itself. One-page intake then
searches rendered content budgets locally and keeps the fullest valid page, not whichever amount of
content an initiating agent happened to select. The minimum fill ratio remains a hard publication
gate; spacing is used only if every supported content candidate that fits has already been included.

The résumé/evidence workflow and private application database are the ready-to-use career core.
Obsidian is an optional human-readable workspace and tracker view. Coding assistants, Hermes,
Discord, and mail providers are also optional connections configured separately; setup does not
require any specific tool or model API key.

## 2. Review local paths

Without Obsidian, the wizard places generated résumé packages beside Erga's private configuration
unless another output directory is selected. If the user explicitly adds Obsidian, the wizard
stores the selected vault and tracker paths in private configuration, creates `Erga/Start Here.md`
without overwriting an existing note, and recommends `Erga/Generated Resumes` for output.

The dragged PDF, DOCX, or `.tex` master becomes approved factual knowledge. Erga creates a
hash-verified private copy, so moving or deleting the original later does not break the workflow.
An optional style résumé contributes only layout metadata; it can never add factual claims. Erga
can copy a PDF's page count into the configured maximum and automatically infers section presence,
order, repeatable content pools, and project slots when generating a content-addressed, standalone
`.tex` template from the approved master. A template with no Experience or Open Source section will
not acquire one; a project-heavy template retains that shape. An
advanced user may still explicitly configure a trusted standalone template override.

Derived style-reference metadata remains part of `resume_source_context`, which is available only
through the explicit `career-private` profile. Ordinary `career` connections receive neither the
reference source nor master-resume text; the local server can still enforce the configured numeric
page and bullet constraints without disclosing either document.

The resulting constraints remain editable after onboarding:

```bash
uv run erga resume settings set \
  --config ~/.config/erga-mcp/config.toml \
  --bullet-min-chars 90 \
  --bullet-target-chars 105 \
  --bullet-max-chars 120 \
  --max-pages 1
```

Advanced or scripted installations may still use the lower-level commands:

```bash
uv run erga init --config ~/.config/erga-mcp/config.toml
uv run erga resume sources import \
  --config ~/.config/erga-mcp/config.toml \
  --master /absolute/path/to/complete-master-resume.pdf
uv run erga resume template ensure --config ~/.config/erga-mcp/config.toml
```

Pass `--style /absolute/path/to/preferred-resume.pdf` only when intentionally overriding Erga's
recommended one-page style. Importing an updated master deactivates prior master-résumé evidence,
leaving exactly one current master approved.

To discard the configured style/custom template while preserving the approved master and all
career evidence, regenerate Erga's default Jake-style template:

```bash
uv run erga resume template reset --config ~/.config/erga-mcp/config.toml
```

The reset changes configuration pointers rather than deleting old private template files.

## 3. Add optional coding hosts

Core setup finishes before asking about coding assistants. The default is to skip connections.
Select zero, one, or several hosts; each is only another interface to the same Erga state:

```bash
# Interactive arrow-key multi-select.
uv run erga connect --config ~/.config/erga-mcp/config.toml

# Repeat --host to configure several project-scoped clients.
uv run erga connect \
  --config ~/.config/erga-mcp/config.toml \
  --project-dir /absolute/path/to/project \
  --host codex \
  --host gemini-cli
```

Supported presets are `codex`, `claude-code`, `opencode`, `opencode-v2`, `gemini-cli`, `cursor`,
`github-copilot`, and `generic-mcp`. Erga does not install, authenticate, or verify a subscription
for any host. A missing executable is reported as informational connection metadata and never
changes core readiness. Preview exact changes with `--dry-run`.

Job-link intake needs a local LaTeX résumé template and an output directory. Configure them before
connecting an agent; neither path is committed to the repository:

```bash
uv run erga resume settings set \
  --config ~/.config/erga-mcp/config.toml \
  --template-path /absolute/path/to/resume.tex \
  --output-root /absolute/path/to/erga-applications \
  --output-pdf-name Candidate_Resume.pdf \
  --editable-section Experience \
  --editable-section Projects \
  --editable-section Technical-Skills \
  --bullet-min-chars 99 \
  --bullet-target-chars 105 \
  --bullet-max-chars 116 \
  --max-pages 1
```

When intake cannot infer a recruiting season from its URL-only input, it files the package under
the neutral `unsorted` cycle rather than guessing from the current date. Callers that know the
cycle can pass it explicitly. A successful LaTeX build is stored under the configured PDF filename.

## 4. Add the optional Discord bridge

Core setup also finishes before offering Discord, and skipping it is the default. Discord needs a
bot token and one explicit local headless coding CLI because messages arrive while no interactive
terminal session is open. That bridge-specific choice does not become Erga's core and does not
prevent connecting other assistants.

This bridge is intended for someone who uses a headless coding tool such as Codex, Claude Code, or
OpenCode but does not already operate a Discord gateway. If Hermes, OpenClaw, or another gateway
already owns the Discord connection, connect Erga's MCP server to that system instead of starting a
second bot.

Install the isolated runtime extra and open the bridge wizard:

```bash
uv sync --extra discord
uv run erga discord configure \
  --config ~/.config/erga-mcp/config.toml \
  --project-dir /absolute/path/to/project
```

The wizard resolves the selected backend before asking for Discord credentials. Its optional
readiness probe uses the backend's existing local login and passes only a strict allowlist of basic
runtime environment variables. The bot token is entered through a hidden prompt and stored only in
the operating-system credential store.

Authorize a current unique Discord username such as `emperor_sai`, a stable numeric user ID, or
several comma-separated identities. Old `name#1234` discriminator names are deliberately rejected
with current-format guidance. In servers, requiring an `@mention` is the default.

Run the bridge in the foreground while testing, then optionally use the managed background
process:

```bash
uv run erga discord run --config ~/.config/erga-mcp/config.toml
uv run erga discord start --config ~/.config/erga-mcp/config.toml
uv run erga discord status --config ~/.config/erga-mcp/config.toml
uv run erga discord stop --config ~/.config/erga-mcp/config.toml
```

See [`discord.md`](discord.md) for bot permissions, backend control, and failure isolation.

## 5. Use the local workflow

All state remains in the configured local SQLite database. Commands produce JSON suitable for review or scripting.

```bash
# Capture a claim. Imported Obsidian candidates are unapproved by default.
uv run erga evidence add \
  --config ~/.config/erga-mcp/config.toml \
  --source-ref 'Career.md#Project' \
  --text 'User-provided, verified outcome.' \
  --approved

# Build a draft application using approved evidence only.
uv run erga applications add \
  --config ~/.config/erga-mcp/config.toml \
  --company 'Example Company' \
  --role 'Example Role' \
  --source-url 'https://jobs.example.test/123' \
  --evidence-id ev_<approved-evidence-id>

# Create review artifacts only; the resume source and remote are unchanged.
uv run erga resume propose \
  --config ~/.config/erga-mcp/config.toml \
  --resume /absolute/path/to/resume.tex \
  --output-dir /absolute/path/to/local-proposals \
  --latex-snippet '\\item User-approved claim.' \
  --evidence-id ev_<approved-evidence-id>

# Optional, explicit local compilation of the generated proposal only.
# This does not write the original source or synchronize a remote.
uv run erga resume validate \
  --config ~/.config/erga-mcp/config.toml \
  --proposal /absolute/path/to/local-proposals/proposal.tex
```

The compiler is discovered from `PATH`. On macOS, the standard MacTeX location
`/Library/TeX/texbin/latexmk` is also detected automatically. Use `--latexmk` only to select a
different executable explicitly.

The Zoho command accepts local fixtures only. It does not use OAuth, network access, or a mailbox:

```bash
uv run erga zoho ingest-fixture \
  --config ~/.config/erga-mcp/config.toml \
  --fixture tests/fixtures/zoho_messages.json
```

## 6. Connect Zoho Mail (read-only)

The live connector uses Zoho's **Mobile-based application** OAuth type, Authorization Code + PKCE,
a fixed local redirect URI, and the operating system's credential store through Python `keyring`.
It requests only the read-only `ZohoMail.messages.READ`, `ZohoMail.folders.READ`, and
`ZohoMail.accounts.READ` scopes; messages are not writable.

1. In Zoho API Console, create a Mobile-based application and register exactly `http://127.0.0.1:8765/callback` as its redirect URI.
2. Copy the client ID (not a secret). Store the client secret locally without displaying it using:

   ```bash
   uv run erga zoho set-client-secret --client-id '<client-id>'
   ```

   The command prompts without echo and writes the secret only to the operating system credential
   store. Supported backends include macOS Keychain, Windows Credential Locker, and Linux Secret
   Service. A headless Linux host must provide and unlock a compatible keyring backend.
3. Start the consent flow:

   ```bash
   uv run erga zoho connect --client-id '<client-id>'
   ```

   Your browser opens Zoho's official consent page. On approval, the local loopback endpoint
   receives the code and the token response is stored in the same credential store. No token or
   secret is written to configuration, Git, chat, `.env`, or Obsidian.

4. Save the non-secret client ID for the unified manual and scheduled sync command:

   ```bash
   uv run erga mail configure \
     --config ~/.config/erga-mcp/config.toml \
     --provider zoho \
     --client-id '<client-id>'
   ```

## 7. Connect Hermes through MCP

### Plug-and-play registration

After initializing the local config, add the server with Hermes:

```bash
hermes mcp add erga-mcp \
  --command uv \
  --connect-timeout 30 \
  --env ERGA_MCP_CONFIG=/absolute/path/to/config.toml \
  --env ERGA_MCP_TOOL_PROFILE=career \
  --args --directory /absolute/path/to/erga-mcp run erga-mcp
```

Keep `--args` last because Hermes treats everything after it as server arguments. If the gateway
routes this chat through a named profile, use that profile for every command in this section, such
as `hermes --profile coder mcp add ...`, `hermes --profile coder mcp test erga-mcp`, and
`hermes --profile coder plugins install ...`. Alternatively, copy
`integrations/hermes/mcp.example.yaml` into the selected Hermes profile configuration and replace
its local path placeholders. Never put OAuth tokens, client secrets, résumé files, or vault
contents in that config.

Verify cold-start discovery before relying on a gateway session:

```bash
hermes mcp test erga-mcp
```

Hermes exposes tools prefixed with `mcp__erga_mcp__`:

**Read-only context**

- `pipeline_status`
- `list_applications`
- `list_evidence`

**Explicit local artifact actions**

- `update_application_status` — records a deliberate local workflow transition such as applied, OA, interview, offer, rejected, or withdrawn. It never contacts an employer or changes a remote service.
- `intake_job_url` — the primary first-turn action for a bare job URL, Markdown/chat link, or URL followed by preview text. It accepts the URL alone, atomically publishes the complete local review package, writes detailed source-cited posting research and an idempotent local application record, ranks the approved project catalogue, researches attributable Git changes for a broader shortlist, and asks a sampling-capable connected host model to draft new role-specific project bullets with per-bullet evidence IDs. Deterministic validation rejects invented numbers, cross-project citations, duplicate lead verbs, unsafe LaTeX, excessive length, and rendered wrapping. Clients without MCP sampling use the approved-copy fallback. It compiles the exact configured PDF, creates/synchronizes the appropriate Obsidian cycle tracker, and reuses current repeats of the same listing (including tracking-only URL variants). Legacy packages are upgraded once using a freshly sanitized snapshot; incomplete legacy files are retained under `legacy-backup/` after a clean rebuild. Jobs with no discoverable time bucket go to `Unscheduled Application Tracker.md` and `Unscheduled Application Notes/`.
- `record_secondary_research` — records bounded host-provided web/community search results after intake, clearly separated from official-posting facts and labeled unverified.
- `prepare_job_workspace` — an advanced second-stage variant for callers that already have company, role, cycle, and slug metadata and explicitly need tracker integration. It is not the entry point for pasted links.
- `create_tailored_resume` — writes only a reviewable tailored `.tex`, diff, and claim report inside that package, gated by supplied approved evidence IDs and configured editable sections.
- `validate_tailored_resume` — explicitly compiles the selected proposal locally and enforces the configured page-count and fill guarantees; it never publishes or submits it.

The recommended `career` profile deliberately excludes mail and monitor tools. It also withholds
`resume_source_context` and master-resume evidence from `list_evidence`, as well as `export_data`
and `cover_letter_style_context`, so an ordinary connected career host cannot receive complete
master-resume text, the private career archive, or full writing-style source material. Use
`career-private` only as an explicit opt-in for a trusted local host: it deliberately restores
master-resume context and those private materials. Use the separately bounded `hermes` profile for
mail integration rather than broadening an ordinary career client.

The MCP server has no outbound application or message tool. Zoho credentials remain in the
operating system credential store and are never sent to Hermes.

### Deterministic pasted-link routing for Hermes

MCP descriptions make the right tool easier for models to select, but the MCP protocol does not
guarantee that a model will choose a tool over a competing browser. For the standing behavior
“pasting a job link means run local intake,” install the optional Hermes router. It requires Hermes
Agent 0.18.2 or newer because it uses the stable `pre_llm_call` context hook and
`ctx.dispatch_tool(name, args)` interface:

```bash
hermes --version
hermes plugins install \
  Adr1an04/erga-mcp/integrations/hermes/plugins/erga-mcp-router \
  --enable
hermes gateway restart
```

The opt-in plugin detects recognized ATS/company-careers links in the current user message and
dispatches `mcp__erga_mcp__intake_job_url` before the model turn. It respects explicit
requests such as “summarize only” or “don't intake,” while correctly treating “don't just
summarize—run the pipeline” as an intake request. It ignores imported page content, reports the tool
result back into the turn, and does not submit applications or send messages. `/intake-job <url>`
is available as an explicit fallback.

On messaging platforms, a successful validated PDF is emitted through Hermes' document-upload
directive so Discord/Telegram/etc. receive an actual attachment rather than a server-local path.
The PDF is the compiled tailored proposal, not a stale baseline build. When the connected MCP
client advertises sampling, Erga sends the host model a bounded set of approved project bullets and
authenticated Git-diff evidence. The model may synthesize new wording, but every returned bullet
must cite evidence IDs belonging to that project. Erga rejects unsupported numbers, cross-project
citations, raw Git accounting prose, duplicate lead verbs, unsafe LaTeX, and excessive length.
Without sampling, intake keeps the deterministic approved-copy behavior. All output records
per-claim provenance in `claim-report.json`. An exact TeX width preflight selects another approved
project or retries model wording when a candidate would wrap; a final width check prevents
publication if any bullet still needs a second line. A configured `max_pages` is enforced with the
same pure-Python PDF parser on macOS, Linux, and Windows.
Before ranking, the fetcher keeps visible official job text and bounded structured job metadata but
removes scripts, styles, navigation, and footer content. Relevance matching is boundary-aware and
does not treat substrings inside unrelated words as skill matches.
The router also calls the host's generic `web_search` tool for a Reddit/community query and a broad
company/role query, then records those results separately as unverified secondary research. This
uses the host's configured search backend and is OS-agnostic; unavailable search never blocks the
official-posting intake or résumé delivery.

### Scheduled mail alerts and history

Run `/setup-erga-monitor` in the private connected chat that should receive notifications.
The router prepares the scripts and asks Hermes cron to create two named jobs while the current
chat/thread origin is available:

- every 15 minutes: bounded read-only mail sync, silent unless a new relevant event appears;
- daily at 9:00: application-status counts and the last seven days of recruiting history.

The deterministic classifier recognizes application acknowledgements, coding assessments,
interview scheduling, offers, decisions, and likely recruiter leads. Only message ID, timestamp,
sender, subject, classification, confidence, and review flag are retained. Message previews are
not persisted or delivered. Use `/setup-erga-monitor 14` to make the daily digest cover 14
days. The equivalent explicit CLI setup is documented in `cron/README.md`.

After reviewing an event, record the local status deliberately:

```bash
uv run erga applications update-status \
  --config ~/.config/erga-mcp/config.toml \
  --application-id '<application-id>' \
  --status interview
```

Create a portable private export of the entire pipeline database view and generated job packages:

```bash
uv run erga export \
  --config ~/.config/erga-mcp/config.toml \
  --output ./exports/erga-mcp.zip
```

The archive contains evidence and résumé artifacts; handle it as sensitive personal data.
In a connected Hermes conversation, `/export-erga` creates and uploads this ZIP directly so
the user never needs access to the server-local filesystem.

MCP discovery can still be finishing when the gateway receives its first message. For that startup
window, the router retries only Hermes' exact `Unknown tool` and `MCP server ... is not connected`
errors. The default wait is 30 seconds and is hard-capped at 30 seconds. Set
`ERGA_MCP_READY_TIMEOUT_SECONDS=0` in the Hermes process environment to disable the
wait, or use another value from 0 through 30. All operational intake errors return immediately
without retrying.

After upgrading the server code or changing its configuration, run `/reload-mcp` in the active
Hermes session or restart the gateway so the long-running stdio process and tool inventory refresh.

## 8. Add the workflow skill

For a personal Hermes installation, tap this repository with `hermes skills tap add Adr1an04/erga-mcp`, then install `skills/productivity/erga-mcp/SKILL.md` through the chosen skill workflow. The skill contains workflow and safety policy only; it contains no integration code or credentials.

## 9. Verify

```bash
uv run erga status --config ~/.config/erga-mcp/config.toml
uv run ruff check .
uv run python -m unittest discover -v
```

## Remove Erga

Preview every Erga-owned path, credential, process, and recorded MCP connection that would be
removed:

```bash
uv run erga uninstall --dry-run
```

Then run `uv run erga uninstall`. Erga prints the same bounded inventory and requires the exact
`DELETE ERGA` confirmation phrase. Use `--yes` only for deliberate non-interactive cleanup. Pass
`--project-dir /path/to/workspace` again for an older project connection created before Erga began
recording connection locations.

Immediately before mutation, Erga rebuilds that inventory from the current configuration. It
removes only the intersection of the reviewed and current plans, rechecks resolved parent paths
before each deletion, and reports anything that changed as skipped rather than expanding scope.

Uninstall stops only a verified Erga Discord process; deletes private configuration, SQLite state,
managed résumé copies, generated packages in Erga-owned output directories, optional Obsidian
projection files, Erga keychain entries, optional Hermes monitor files, and legacy `.erga`/platform
data locations; and removes only Erga's server entry from shared client configuration. It never
deletes the original résumé files you imported, an AI client's own login, the source checkout,
`.venv`, or uv's shared package cache. Remove the checkout separately with your normal file manager
after the command exits if you no longer want the code itself.

## Deliberately bounded adapters

- **Zoho live access:** Authorization Code + PKCE with read-only scopes and bounded metadata polling. It cannot modify mail.
- **Obsidian:** the importer is read-only and limited to an explicitly configured vault path. Imported candidates still require approval before use.
- **Overleaf:** use a local Git worktree and the reviewable LaTeX patch; remote synchronization stays an explicit, user-initiated operation.

All adapters remain separately configured and authorized.
