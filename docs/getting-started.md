# Getting started

## 1. Install locally

```bash
git clone https://github.com/Adr1an04/recruiting-pipeline.git
cd recruiting-pipeline
uv sync --extra mcp --extra dev
uv run recruiting-pipeline init --config ~/.config/recruiting-pipeline/config.toml
```

The generated configuration and SQLite data directory live outside the repository. Do not place a personal vault path, tokens, or imports in Git.

## 2. Choose local paths

Edit the generated configuration only on the local machine. `data_dir` and `vault_path` may be relative to that configuration file. Start with the vault path empty until an Obsidian adapter is installed.

Job-link intake needs a local LaTeX résumé template and an output directory. Configure them before
connecting an agent; neither path is committed to the repository:

```bash
uv run recruiting-pipeline resume settings set \
  --config ~/.config/recruiting-pipeline/config.toml \
  --template-path /absolute/path/to/resume.tex \
  --output-root /absolute/path/to/recruiting-applications \
  --output-pdf-name Candidate_Resume.pdf
```

When intake cannot infer a recruiting season from its URL-only input, it files the package under
the neutral `unsorted` cycle rather than guessing from the current date. Callers that know the
cycle can pass it explicitly. A successful LaTeX build is stored under the configured PDF filename.

## 3. Use the local workflow

All state remains in the configured local SQLite database. Commands produce JSON suitable for review or scripting.

```bash
# Capture a claim. Imported Obsidian candidates are unapproved by default.
uv run recruiting-pipeline evidence add \
  --config ~/.config/recruiting-pipeline/config.toml \
  --source-ref 'Career.md#Project' \
  --text 'User-provided, verified outcome.' \
  --approved

# Build a draft application using approved evidence only.
uv run recruiting-pipeline applications add \
  --config ~/.config/recruiting-pipeline/config.toml \
  --company 'Example Company' \
  --role 'Example Role' \
  --source-url 'https://jobs.example.test/123' \
  --evidence-id ev_<approved-evidence-id>

# Create review artifacts only; the resume source and remote are unchanged.
uv run recruiting-pipeline resume propose \
  --config ~/.config/recruiting-pipeline/config.toml \
  --resume /absolute/path/to/resume.tex \
  --output-dir /absolute/path/to/local-proposals \
  --latex-snippet '\\item User-approved claim.' \
  --evidence-id ev_<approved-evidence-id>

# Optional, explicit local compilation of the generated proposal only.
# This does not write the original source or synchronize a remote.
uv run recruiting-pipeline resume validate \
  --config ~/.config/recruiting-pipeline/config.toml \
  --proposal /absolute/path/to/local-proposals/proposal.tex \
  --latexmk /absolute/path/to/latexmk
```

The Zoho command accepts local fixtures only. It does not use OAuth, network access, or a mailbox:

```bash
uv run recruiting-pipeline zoho ingest-fixture \
  --config ~/.config/recruiting-pipeline/config.toml \
  --fixture tests/fixtures/zoho_messages.json
```

## 4. Connect Zoho Mail (read-only)

The live connector uses Zoho's **Mobile-based application** OAuth type, Authorization Code + PKCE, a fixed local redirect URI, and macOS Keychain. It requests only the read-only `ZohoMail.messages.READ`, `ZohoMail.folders.READ`, and `ZohoMail.accounts.READ` scopes; messages are not writable.

1. In Zoho API Console, create a Mobile-based application and register exactly `http://127.0.0.1:8765/callback` as its redirect URI.
2. Copy the client ID (not a secret). Store the client secret locally without displaying it using:

   ```bash
   uv run recruiting-pipeline zoho set-client-secret --client-id '<client-id>'
   ```

   The command prompts without echo and writes the secret only to macOS Keychain.
3. Start the consent flow:

   ```bash
   uv run recruiting-pipeline zoho connect --client-id '<client-id>'
   ```

   Your browser opens Zoho's official consent page. On approval, the local loopback endpoint receives the code and the token response is stored in macOS Keychain. No token or secret is written to configuration, Git, chat, `.env`, or Obsidian.

## 5. Connect Hermes through MCP

### Plug-and-play registration

After initializing the local config, add the server with Hermes:

```bash
hermes mcp add recruiting-pipeline \
  --command "uv --directory /absolute/path/to/recruiting-pipeline run recruiting-pipeline-mcp"
```

Set `RECRUITING_PIPELINE_CONFIG` in the MCP server environment to the non-secret local config path. Alternatively, copy `integrations/hermes/mcp.example.yaml` into the selected Hermes profile configuration and replace its local path placeholders. Never put OAuth tokens, client secrets, résumé files, or vault contents in that config.

Verify cold-start discovery before relying on a gateway session:

```bash
hermes mcp test recruiting-pipeline
```

Hermes exposes tools prefixed with `mcp__recruiting_pipeline__`:

**Read-only context**

- `pipeline_status`
- `list_applications`
- `list_evidence`
- `list_mail_events`

**Explicit local artifact actions**

- `intake_job_url` — the primary first-turn action for a bare job URL, Markdown/chat link, or URL followed by preview text. It accepts the URL alone, atomically publishes the complete local review package, and reuses repeats of the same listing (including tracking-only URL variants).
- `prepare_job_workspace` — an advanced second-stage variant for callers that already have company, role, cycle, and slug metadata and explicitly need tracker integration. It is not the entry point for pasted links.
- `create_tailored_resume` — writes only a reviewable tailored `.tex`, diff, and claim report inside that package, gated by supplied approved evidence IDs and configured editable sections.
- `validate_tailored_resume` — explicitly compiles the selected proposal locally; it never publishes or submits it.

The MCP server has no outbound application or message tool. Zoho credentials remain in macOS Keychain and are never sent to Hermes.

### Deterministic pasted-link routing for Hermes

MCP descriptions make the right tool easier for models to select, but the MCP protocol does not
guarantee that a model will choose a tool over a competing browser. For the standing behavior
“pasting a job link means run local intake,” install the optional Hermes router. It requires Hermes
Agent 0.18.2 or newer because it uses the stable `pre_llm_call` context hook and
`ctx.dispatch_tool(name, args)` interface:

```bash
hermes --version
hermes plugins install \
  Adr1an04/recruiting-pipeline/integrations/hermes/plugins/recruiting-pipeline-router \
  --enable
hermes gateway restart
```

The opt-in plugin detects recognized ATS/company-careers links in the current user message and
dispatches `mcp__recruiting_pipeline__intake_job_url` before the model turn. It respects explicit
requests such as “summarize only” or “don't intake,” while correctly treating “don't just
summarize—run the pipeline” as an intake request. It ignores imported page content, reports the tool
result back into the turn, and does not submit applications or send messages. `/intake-job <url>`
is available as an explicit fallback.

MCP discovery can still be finishing when the gateway receives its first message. For that startup
window, the router retries only Hermes' exact `Unknown tool` and `MCP server ... is not connected`
errors. The default wait is 30 seconds and is hard-capped at 30 seconds. Set
`RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS=0` in the Hermes process environment to disable the
wait, or use another value from 0 through 30. All operational intake errors return immediately
without retrying.

After upgrading the server code or changing its configuration, run `/reload-mcp` in the active
Hermes session or restart the gateway so the long-running stdio process and tool inventory refresh.

## 6. Add the workflow skill

For a personal Hermes installation, tap this repository with `hermes skills tap add Adr1an04/recruiting-pipeline`, then install `skills/productivity/recruiting-pipeline/SKILL.md` through the chosen skill workflow. The skill contains workflow and safety policy only; it contains no integration code or credentials.

## 7. Verify

```bash
uv run recruiting-pipeline status --config ~/.config/recruiting-pipeline/config.toml
uv run ruff check .
uv run python -m unittest discover -v
```

## Deliberately unconnected adapters

- **Zoho live access:** a future OAuth authorization-code/PKCE flow with `ZohoMail.messages.READ`; minimal metadata polling from one configured folder. The current implementation accepts fixtures only.
- **Obsidian:** the importer is read-only and limited to an explicitly configured vault path. Imported candidates still require approval before use.
- **Overleaf:** use a local Git worktree and the reviewable LaTeX patch; remote synchronization stays an explicit, user-initiated operation.

All adapters remain separately configured and authorized.
