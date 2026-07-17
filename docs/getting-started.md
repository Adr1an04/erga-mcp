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

## 3. Connect Hermes through MCP

Copy `integrations/hermes/mcp.example.yaml` into the selected Hermes profile's `config.yaml`, replacing the placeholder absolute paths locally. Hermes starts the server through stdio and exposes tools prefixed with `mcp_recruiting_pipeline_`.

The initial MCP server exposes only read-only local tools:

- `pipeline_status`
- `list_applications`
- `list_evidence`

It does not receive a Zoho token and it cannot change external services.

## 4. Add the workflow skill

For a personal Hermes installation, tap this repository with `hermes skills tap add Adr1an04/recruiting-pipeline`, then install `skills/productivity/recruiting-pipeline/SKILL.md` through the chosen skill workflow. The skill contains workflow and safety policy only; it contains no integration code or credentials.

## 5. Verify

```bash
uv run recruiting-pipeline status --config ~/.config/recruiting-pipeline/config.toml
uv run ruff check .
uv run python -m unittest discover -v
```

## Planned adapters

- **Zoho:** OAuth authorization-code/PKCE flow with `ZohoMail.messages.READ`; minimal metadata polling from one configured folder.
- **Obsidian:** explicit vault paths and proposed application-note updates, not arbitrary vault rewrites.
- **Overleaf:** local Git worktree and reviewable LaTeX patch; explicit remote sync only.

All future adapters must remain separately installed, configured, and authorized.
