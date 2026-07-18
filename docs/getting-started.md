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

The live connector uses Zoho's **Mobile-based application** OAuth type, Authorization Code + PKCE, a fixed local redirect URI, and macOS Keychain. It requests only `ZohoMail.messages.READ`; messages are not writable.

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

Copy `integrations/hermes/mcp.example.yaml` into the selected Hermes profile's `config.yaml`, replacing the placeholder absolute paths locally. Review the full command before enabling it: a local MCP server runs with Hermes client permissions. Hermes starts this server through stdio and exposes tools prefixed with `mcp_recruiting_pipeline_`.

The initial MCP server exposes only read-only local tools:

- `pipeline_status`
- `list_applications`
- `list_evidence`
- `list_mail_events`

It does not receive a Zoho token and it cannot change external services.

## 6. Add the workflow skill

For a personal Hermes installation, tap this repository with `hermes skills tap add Adr1an04/recruiting-pipeline`, then install `skills/productivity/recruiting-pipeline/SKILL.md` through the chosen skill workflow. The skill contains workflow and safety policy only; it contains no integration code or credentials.

## 6. Verify

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
