# Getting started

## 1. Install locally

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync --extra mcp --extra dev
uv run erga init --config ~/.config/erga-mcp/config.toml
```

The generated configuration and SQLite data directory live outside the repository. Do not place a personal vault path, tokens, or imports in Git.

## 2. Choose local paths

Edit the generated configuration only on the local machine. `data_dir` and `vault_path` may be relative to that configuration file. Start with the vault path empty until an Obsidian adapter is installed.

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

## 3. Use the local workflow

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

## 4. Connect Zoho Mail (read-only)

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

## 5. Connect Hermes through MCP

### Plug-and-play registration

After initializing the local config, add the server with Hermes:

```bash
hermes mcp add erga-mcp \
  --command uv \
  --connect-timeout 30 \
  --env ERGA_MCP_CONFIG=/absolute/path/to/config.toml \
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
- `list_mail_events`

**Explicit local artifact actions**

- `intake_job_url` — the primary first-turn action for a bare job URL, Markdown/chat link, or URL followed by preview text. It accepts the URL alone, atomically publishes the complete local review package, writes detailed source-cited posting research and an idempotent local application record, deterministically reorders existing résumé bullets/projects/all skill categories, compiles the exact configured PDF, creates/synchronizes the appropriate Obsidian cycle tracker, and reuses current repeats of the same listing (including tracking-only URL variants). Legacy packages are upgraded once using a freshly sanitized snapshot; incomplete legacy files are retained under `legacy-backup/` after a clean rebuild. Jobs with no discoverable time bucket go to `Unscheduled Application Tracker.md` and `Unscheduled Application Notes/`.
- `record_secondary_research` — records bounded host-provided web/community search results after intake, clearly separated from official-posting facts and labeled unverified.
- `install_mail_monitor_scripts` — prepares deterministic no-agent mail-alert and history scripts under the Hermes scripts directory. It does not create a delivery job by itself.
- `export_data` — creates a private ZIP containing local records, history, evidence, and generated job packages for native attachment delivery.
- `prepare_job_workspace` — an advanced second-stage variant for callers that already have company, role, cycle, and slug metadata and explicitly need tracker integration. It is not the entry point for pasted links.
- `create_tailored_resume` — writes only a reviewable tailored `.tex`, diff, and claim report inside that package, gated by supplied approved evidence IDs and configured editable sections.
- `validate_tailored_resume` — explicitly compiles the selected proposal locally; it never publishes or submits it.

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
The PDF is the compiled tailored proposal, not a stale baseline build. Automatic tailoring changes
ordering only: every claim remains byte-for-byte sourced from the user-provided template, with
per-claim provenance in `claim-report.json`. Configured bullet limits prevent new violations and a
configured `max_pages` is enforced with the same pure-Python PDF parser on macOS, Linux, and
Windows.
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

## 6. Add the workflow skill

For a personal Hermes installation, tap this repository with `hermes skills tap add Adr1an04/erga-mcp`, then install `skills/productivity/erga-mcp/SKILL.md` through the chosen skill workflow. The skill contains workflow and safety policy only; it contains no integration code or credentials.

## 7. Verify

```bash
uv run erga status --config ~/.config/erga-mcp/config.toml
uv run ruff check .
uv run python -m unittest discover -v
```

## Deliberately bounded adapters

- **Zoho live access:** Authorization Code + PKCE with read-only scopes and bounded metadata polling. It cannot modify mail.
- **Obsidian:** the importer is read-only and limited to an explicitly configured vault path. Imported candidates still require approval before use.
- **Overleaf:** use a local Git worktree and the reviewable LaTeX patch; remote synchronization stays an explicit, user-initiated operation.

All adapters remain separately configured and authorized.
