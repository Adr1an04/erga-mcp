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

- Local configuration and SQLite state initialization
- Evidence and application-record foundations with an audit trail
- Conservative deterministic acknowledgement/denial classification
- Read-only MCP tools for local status, application records, and evidence
- Generic Hermes skills, MCP, and cron setup examples

Zoho OAuth polling, Obsidian write proposals, and Overleaf Git proposals are deliberately not connected yet. They require their own testable adapters and explicit authorization flows.

## Development

```bash
uv sync --extra mcp --extra dev
uv run ruff check .
uv run python -m unittest discover -v
```

## License

[MIT](LICENSE)
