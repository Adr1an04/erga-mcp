# Recruiting Pipeline

A local-first toolkit for organizing career context, tracking recruiting activity, and producing reviewable preparation materials.

It is designed to work on its own or as a narrow, opt-in MCP integration for [Hermes](https://github.com/NousResearch/hermes-agent). It does not submit applications, send mail, or make remote résumé edits.

## Principles

- Local-first data ownership
- Explicit permissions and least-privilege integrations
- Evidence-backed résumé claims; no invented metrics
- Human approval for external or consequential actions
- Reusable configuration with no personal data committed

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Adr1an04/recruiting-pipeline.git
cd recruiting-pipeline
uv sync --extra mcp --extra dev
uv run recruiting-pipeline init --config ~/.config/recruiting-pipeline/config.toml
uv run recruiting-pipeline status --config ~/.config/recruiting-pipeline/config.toml
```

See [Getting started](docs/getting-started.md) for Hermes MCP setup and local-data boundaries.

## Current capabilities

- Local configuration and SQLite state with an audit trail
- Approved evidence capture and draft application records with evidence provenance
- Read-only, vault-bounded Obsidian Markdown import that creates unapproved candidates
- Fixture-first Zoho metadata classification with message-ID deduplication and read-only scope validation
- Reviewable local LaTeX résumé proposals: proposed source, diff, and claim/evidence report without source mutation or remote sync
- Read-only MCP tools for local status, applications, evidence, and normalized mail events
- Generic Hermes skill, stdio MCP, and deterministic no-agent cron examples

Live Zoho OAuth, Obsidian writes, LaTeX/Overleaf synchronization, application submission, and outbound messaging are deliberately unconnected. They require separately configured adapters, user-side authorization, and explicit approval.

## Development

```bash
uv sync --extra mcp --extra dev
uv run ruff check .
uv run python -m unittest discover -v
```

## License

[MIT](LICENSE)
