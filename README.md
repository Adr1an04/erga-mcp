<div align="center">

# Recruiting Pipeline

**A private, local workspace for keeping job-search context organized and preparing truthful, reviewable résumé changes.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](LICENSE)
[![Status: Pre-Alpha](https://img.shields.io/badge/Status-Pre--Alpha-f59e0b.svg)](#project-status)

[Quick start](#quick-start) · [What it does](#what-it-does) · [How it works](#how-it-works) · [Integrations](#optional-integrations) · [Security](#privacy-and-safety)

</div>

## What is this?

Recruiting Pipeline is a **local-first Python CLI** for the repetitive, information-heavy parts of a job search. It keeps a small SQLite database on your computer and helps you:

- save verified facts from your work history as reusable **career evidence**;
- create local draft application records;
- recognize application acknowledgements, assessments, rejections, and recruiting messages from read-only email metadata;
- prepare a folder for each job with the job description, relevant evidence, and a copy of your LaTeX résumé;
- generate a proposed résumé edit, unified diff, and evidence report for you to review; and
- expose the same local workflow to an MCP client such as [Hermes](https://github.com/NousResearch/hermes-agent).

The project is intentionally conservative: it prepares and organizes work, but **you remain the person who reviews the résumé and submits the application**.

> [!IMPORTANT]
> This is currently a pre-alpha developer tool. It has a command-line interface, not a web UI, and some workflows require manual commands or an MCP client.

## What it does — and what it does not do

| It does | It does not |
| --- | --- |
| Store recruiting records in a local SQLite database | Submit job applications |
| Keep an audit trail of local records | Fill out application forms |
| Import evidence candidates from an Obsidian note | Invent experience, metrics, or résumé claims |
| Read bounded Gmail or Zoho inbox metadata | Send, delete, label, or modify email |
| Classify common recruiting messages with simple rules | Automatically change an application's status |
| Create reviewable LaTeX résumé proposals | Modify your original résumé or push to Overleaf |
| Create local job/research folders through MCP | Act as a full applicant-tracking system or job-search UI |

## The core idea: evidence before résumé bullets

Most résumé tools start with generated text. Recruiting Pipeline starts with facts that you have supplied and approved.

```text
Your verified career facts
          │
          ▼
  Local evidence ledger ──────► Draft application record
          │
          ├────► Job-description matching
          │
          ▼
Reviewable LaTeX proposal + diff + claim report
          │
          ▼
      You review it
```

Every résumé proposal must reference at least one approved evidence record. The tool writes the proposal to a separate output folder, leaves the source résumé unchanged, and records the evidence used in `claim-report.json`.

## Quick start

### Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Git

Optional features have additional requirements:

- `latexmk` to compile a proposed LaTeX résumé
- an existing LaTeX résumé (`.tex`) for résumé workflows
- macOS Keychain for the built-in Zoho OAuth flow
- an authenticated [`gws`](https://github.com/googleworkspace/cli) command for Gmail

### 1. Install

```bash
git clone https://github.com/Adr1an04/recruiting-pipeline.git
cd recruiting-pipeline
uv sync --extra mcp --extra dev
```

`--extra mcp` installs the optional MCP server. `--extra dev` installs the test and lint tools. For the CLI alone, `uv sync` is enough.

### 2. Create local configuration

```bash
uv run recruiting-pipeline init
uv run recruiting-pipeline doctor
```

By default this creates:

```text
~/.config/recruiting-pipeline/
├── config.toml
└── state/
    └── pipeline.sqlite3
```

The config contains paths and feature settings—not credentials. Relative paths are resolved from the directory containing `config.toml`.

To keep the config somewhere else, add `--config /absolute/path/to/config.toml` to commands.

### 3. Add a verified career fact

```bash
uv run recruiting-pipeline evidence add \
  --source-ref 'Career.md#Pipeline project' \
  --text 'Built a Python pipeline that reduced weekly manual review by 30%.' \
  --approved
```

The command returns JSON containing an ID such as `ev_a1b2...`. Keep that ID for the next steps.

> [!NOTE]
> `--approved` means you have verified the statement and allow it to support a résumé proposal. Leave it off for unverified notes.

### 4. Create a local application record

```bash
uv run recruiting-pipeline applications add \
  --company 'Example Company' \
  --role 'Software Engineer' \
  --source-url 'https://jobs.example.com/123' \
  --evidence-id 'ev_a1b2...'
```

This creates a local record with status `draft`; it does not contact the employer or submit anything.

### 5. Check your local pipeline

```bash
uv run recruiting-pipeline status
uv run recruiting-pipeline applications list
```

Commands return JSON so they can be inspected directly or used in scripts.

## Résumé workflow

The current résumé workflow supports LaTeX files. It does not generate facts on its own; you supply the proposed LaTeX content and the approved evidence IDs that justify it.

### Simple proposal

```bash
uv run recruiting-pipeline resume propose \
  --resume /absolute/path/to/resume.tex \
  --output-dir /absolute/path/to/proposal \
  --latex-snippet '\item Built a Python pipeline that reduced weekly manual review by 30\%.' \
  --evidence-id 'ev_a1b2...'
```

The output directory contains:

```text
proposal/
├── proposal.tex        # proposed résumé; the original is untouched
├── proposal.diff       # unified diff for review
└── claim-report.json   # approved evidence used by the proposal
```

### Section-aware proposal

First configure your template and the sections the tool is allowed to edit:

```bash
uv run recruiting-pipeline resume settings set \
  --template-path /absolute/path/to/resume.tex \
  --editable-section Experience \
  --editable-section Projects \
  --output-root /absolute/path/to/job-packages
```

Then append proposed content to one allowed `\section{...}`:

```bash
uv run recruiting-pipeline resume tailor \
  --section Experience \
  --latex-content '\item Built a Python pipeline that reduced weekly manual review by 30\%.' \
  --output-dir /absolute/path/to/proposal \
  --evidence-id 'ev_a1b2...'
```

Existing section content is preserved; the new content is appended in the separate proposal file.

### Compile a proposal locally

```bash
uv run recruiting-pipeline resume validate \
  --proposal /absolute/path/to/proposal/proposal.tex
```

This runs `latexmk` only on the selected proposal. It does not copy the result over your master résumé or synchronize with Overleaf.

## Optional integrations

All integrations are opt-in. The standalone CLI and local SQLite store work without them.

### Obsidian

Set `vault_path` in your local config, then import a Markdown note:

```bash
uv run recruiting-pipeline obsidian import --note 'Career/Projects.md'
```

The importer:

- reads only `.md` files inside the configured vault;
- treats each level-two heading (`##`) as one evidence candidate;
- never modifies the source note; and
- imports every candidate as **unapproved**.

At present there is no CLI command to approve an imported record after the fact. Add verified evidence with `evidence add --approved` when you want to use it in an application or résumé proposal.

### Gmail

Set the provider in `config.toml`:

```toml
[mail]
provider = "gmail"
gws_command = "gws"
```

After separately installing and authorizing `gws`, fetch a bounded set of Inbox messages:

```bash
uv run recruiting-pipeline mail sync --limit 20
```

The connector asks Gmail for message IDs, sender and subject headers, timestamps, and snippets. Classification happens locally. The SQLite store does not retain snippets or message bodies.

### Zoho Mail

The built-in Zoho flow uses Authorization Code + PKCE and requests only these read scopes:

- `ZohoMail.messages.READ`
- `ZohoMail.folders.READ`
- `ZohoMail.accounts.READ`

Create a **Mobile-based application** in the Zoho API Console with this exact redirect URI:

```text
http://127.0.0.1:8765/callback
```

Then connect and sync:

```bash
uv run recruiting-pipeline zoho set-client-secret --client-id '<client-id>'
uv run recruiting-pipeline zoho connect --client-id '<client-id>'
uv run recruiting-pipeline zoho sync --client-id '<client-id>' --limit 20
```

The client secret and OAuth tokens are stored in macOS Keychain. The sync reads recent Inbox metadata and cannot mutate messages.

To try classification without OAuth or network access:

```bash
uv run recruiting-pipeline zoho ingest-fixture \
  --fixture tests/fixtures/zoho_messages.json
```

### Hermes / MCP

The optional local stdio MCP server exposes the pipeline to Hermes or another compatible client.

```bash
hermes mcp add recruiting-pipeline \
  --command "uv --directory /absolute/path/to/recruiting-pipeline run recruiting-pipeline-mcp"
```

Set `RECRUITING_PIPELINE_CONFIG` in the MCP server environment to the absolute path of your local `config.toml`. A complete example is available at [`integrations/hermes/mcp.example.yaml`](integrations/hermes/mcp.example.yaml).

The MCP tools fall into two groups:

| Tool | Behavior |
| --- | --- |
| `pipeline_status` | Read local record counts |
| `list_applications` | Read local application records |
| `list_evidence` | Read local evidence records |
| `list_mail_events` | Read normalized local mail events |
| `prepare_job_workspace` | Fetch a supplied job URL and create a local job package |
| `create_tailored_resume` | Create a local proposal, diff, and claim report |
| `validate_tailored_resume` | Run the configured local LaTeX compiler |

`prepare_job_workspace` performs transparent keyword-overlap matching against approved evidence. It is not semantic AI matching. The MCP client supplies any tailored LaTeX content, which is still gated by approved evidence IDs and configured editable sections.

## How data moves through the project

```text
                    ┌──────────────────────────────┐
Obsidian note ─────►│ unapproved evidence          │
manual evidence ───►│ approved/unapproved evidence │
                    │                              │
Gmail / Zoho ──────►│ normalized mail events       │──► local JSON/status
                    │                              │
manual command ────►│ draft applications           │
                    └──────────────┬───────────────┘
                                   │ SQLite + audit log
                                   ▼
job URL + approved evidence + LaTeX template
                                   │
                                   ▼
              local job package / résumé proposal
```

Mail classification currently uses deterministic phrase matching. It recognizes acknowledgements, assessments, denials, likely recruiting messages, and other mail. Ambiguous or consequential messages are marked for review. Mail events are deduplicated by provider message ID, but they are not yet linked automatically to application records.

## Privacy and safety

- Personal state is stored outside the repository in your configured local paths.
- OAuth credentials are not stored in TOML, `.env` files, SQLite, or Git.
- Email previews may be used during local classification but are not persisted.
- Obsidian imports are bounded to the configured vault and are read-only.
- Résumé proposals require existing approved evidence.
- Proposed LaTeX rejects file-inclusion and shell-execution commands such as `\input` and `\write18`.
- The MCP server uses stdio rather than opening a network service. It still runs with the permissions of the client that launches it, so review its command, environment, and configured paths.
- Job descriptions, email text, Markdown, and web pages are treated as untrusted input—not instructions.

See [`docs/security.md`](docs/security.md) for the complete trust boundary and tool capability model.

## Project status

Recruiting Pipeline is **pre-alpha**. The local data model, deterministic mail classification, evidence checks, résumé proposal artifacts, read-only mail connectors, and MCP surface are implemented and tested. Expect rough edges and breaking changes.

Current limitations include:

- no graphical interface;
- application records are local drafts with no status-update command;
- mail events are not automatically matched to application records;
- imported Obsidian candidates cannot yet be approved through the CLI;
- job matching is lexical keyword overlap, not semantic ranking;
- résumé editing supports LaTeX only;
- résumé bullet length and page-limit settings are stored but not currently enforced;
- no Overleaf synchronization; and
- no automatic applications or outbound messages by design.

The architecture direction is documented in [`docs/architecture/ADR-001-local-core-and-evidence-ledger.md`](docs/architecture/ADR-001-local-core-and-evidence-ledger.md). More detailed setup notes are in [`docs/getting-started.md`](docs/getting-started.md).

## Command reference

```text
recruiting-pipeline init
recruiting-pipeline doctor
recruiting-pipeline status

recruiting-pipeline evidence add
recruiting-pipeline applications [list|add]
recruiting-pipeline obsidian import
recruiting-pipeline mail sync

recruiting-pipeline zoho set-client-secret
recruiting-pipeline zoho connect
recruiting-pipeline zoho sync
recruiting-pipeline zoho ingest-fixture

recruiting-pipeline resume settings [show|set]
recruiting-pipeline resume create-package
recruiting-pipeline resume propose
recruiting-pipeline resume tailor
recruiting-pipeline resume validate
```

Run `uv run recruiting-pipeline <command> --help` for all arguments.

## Development

```bash
uv sync --extra mcp --extra dev
uv run ruff check .
uv run mypy src
uv run python -m unittest discover -v
```

The test suite uses synthetic data. Do not commit real résumés, email, job applications, OAuth tokens, contact details, or vault contents.

## License

[MIT](LICENSE)
