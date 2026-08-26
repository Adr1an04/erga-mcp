<div align="center">
  <img src="docs/assets/erga-logo.svg" width="720" alt="Erga" />

  <p>
    <a href="https://github.com/Adr1an04/erga-mcp/actions/workflows/ci.yml"><img src="https://github.com/Adr1an04/erga-mcp/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-C8792A.svg" alt="MIT License" /></a>
    <img src="https://img.shields.io/badge/Status-Pre--Alpha-F2A93B.svg" alt="Pre-Alpha" />
  </p>
</div>

Erga is a local-first recruiting assistant. It keeps job applications, approved career evidence,
research, and reviewable résumé packages on the user's machine. The CLI is the product core; MCP,
Discord, Hermes, Obsidian, GitHub, and mail are optional adapters around that core.

Erga does not submit applications, send messages, invent claims, mutate remote mail, or overwrite a
master résumé. Imported résumés, mail, pages, descriptions, and attachments are untrusted data.

## What it does

- Tracks applications and status history in private SQLite state.
- Captures public job postings before they disappear.
- Ranks an approved project catalogue against a role.
- Enriches projects with attributable local Git evidence.
- Produces evidence-cited résumé proposals, diffs, claim reports, and validated PDFs.
- Deterministically parses and grades generated bullets for action, implementation, scope, proof,
  outcome, cohesion, and scanability before accepting model-authored copy.
- Builds project-scoped evidence graphs and accepts only bullets assembled from one connected claim
  path, preventing plausible-sounding cross-component fact fusion.
- Compares every tailored proposal with its own master, records the project decision and exact
  validated version, and distinguishes generation from explicit application use.
- Preserves the configured master/template structure and enforces page, density, line-wrap, and
  lead-verb constraints.
- Projects selected records into Obsidian and classifies bounded Gmail or Zoho metadata when those
  integrations are explicitly enabled.
- Reconciles recruiting mail with applications using privacy-safe signals and queues ambiguous
  matches for explicit review.
- Exposes capability-scoped tools to any MCP client over stdio, with authenticated loopback HTTP as
  an opt-in compatibility transport.

## Quick start

Requirements: Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and Git.

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync
uv run erga
uv run erga setup
```

Setup imports a PDF, DOCX, or LaTeX master into private, hash-verified storage and creates a
standalone editable template. A complete LaTeX master remains the exact visual template; an
optional second résumé can explicitly override layout measurements only, and its
wording never becomes evidence. The generated configuration uses the least-privilege `career` MCP
profile.

After setup, normal use is one local command:

```bash
uv run erga tailor https://jobs.example.com/role
```

If the posting cannot be fetched, save or paste its description instead:

```bash
uv run erga tailor --job-file job.txt --company Acme --role "Software Engineering Intern"
```

Erga shows progress, chooses the strongest approved experience/project evidence, preserves the
master's LaTeX layout, and rejects drafts with extra pages, low page fill, wrapping, short tails, or
TeX overflow. Use `--preset concise|balanced|technical` plus per-run project, page, and bullet
limits when needed. `uv run erga review` gives a plain-language readiness summary without exposing
internal IDs or JSON files. Discord and every other integration remain separately optional.

Use `uv run erga --help` and `uv run erga <command> --help` for the complete CLI surface.

## MCP

Stdio is the recommended MCP transport:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/erga-mcp", "run", "erga-mcp"],
  "env": {
    "ERGA_MCP_CONFIG": "/absolute/path/to/erga-config.toml",
    "ERGA_MCP_TOOL_PROFILE": "career"
  }
}
```

The `mcp` Python runtime dependency is installed with Erga. Tool profiles are capability boundaries:
`career` is the safe default, `career-private` adds raw private-source context and export, and
`read`, `research`, `write`, `hermes`, and the explicit broad legacy `default` profile support
narrow integration needs. See [`docs/mcp-clients.md`](docs/mcp-clients.md) for client examples and
authenticated Streamable HTTP configuration.

## Data model and safety

By default, machine state lives outside the repository:

```text
~/.config/erga-mcp/
├── config.toml
├── state/
│   └── erga.sqlite3
└── generated-resumes/
```

Credentials belong in the operating-system credential store. Configuration contains paths and
feature settings, never secrets. Resume changes are local proposals until a user reviews them.
Every generated claim must cite approved evidence; unknown impact and metrics remain unknown.

Read [`docs/security.md`](docs/security.md) before enabling network, mail, Discord, or broad MCP
profiles.

## Repository map

```text
src/erga_mcp/             domain/application modules plus the CLI facade
src/erga_mcp/mcp/         MCP contracts, capability policy, registry, and tool families
src/erga_mcp/integrations/optional mail and Obsidian adapters
integrations/hermes/      optional Hermes router/plugin
tests/                    synthetic unit, workflow, protocol, and packaging tests
docs/                     indexed user, operator, security, and architecture docs
```

The MCP specification defines protocol behavior, not a mandatory Python directory tree. Erga keeps
the SDK server as an adapter: tool families call ordinary application/domain functions and SQLite
remains usable through the standalone CLI. The current boundaries and dependency rules are in
[`docs/architecture/README.md`](docs/architecture/README.md).

## Documentation

Start at [`docs/README.md`](docs/README.md):

- [`docs/getting-started.md`](docs/getting-started.md) — install and configure Erga.
- [`docs/mcp-clients.md`](docs/mcp-clients.md) — connect MCP clients over stdio or authenticated
  loopback HTTP.
- [`docs/project-inventory.md`](docs/project-inventory.md) — evidence-backed project selection.
- [`docs/discord.md`](docs/discord.md) — optional Discord bridge.
- [`docs/security.md`](docs/security.md) — trust boundaries and private-data rules.
- [`docs/development.md`](docs/development.md) — code navigation and canonical verification.

## Development

The canonical setup, change-to-test map, and complete verification gate live in
[`docs/development.md`](docs/development.md). Tests and examples use synthetic data. Never commit
real résumés, applications, email content, credentials, contact details, tokens, databases,
exports, or vault content.

Erga is pre-alpha; interfaces can change before 1.0. See [`CONTRIBUTING.md`](CONTRIBUTING.md),
[`SECURITY.md`](SECURITY.md), and the [MIT license](LICENSE).
