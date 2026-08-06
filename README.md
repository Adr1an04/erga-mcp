<div align="center">
  <img src="docs/assets/erga-logo.svg" width="720" alt="Erga" />

  <p>
    <a href="https://github.com/Adr1an04/erga-mcp/actions/workflows/ci.yml"><img src="https://github.com/Adr1an04/erga-mcp/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-C8792A.svg" alt="MIT License" /></a>
    <img src="https://img.shields.io/badge/Status-Pre--Alpha-F2A93B.svg" alt="Pre-Alpha" />
  </p>

  <p>
    <a href="#quick-start">Quick start</a> ·
    <a href="#how-erga-works">How it works</a> ·
    <a href="docs/getting-started.md">Documentation</a> ·
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>
</div>

---

Recruiting is hard for students and full-time engineers. Applications pile up, job descriptions
disappear, recruiter updates get buried in your inbox, and every role asks for a slightly different
version of the same résumé.

Erga helps you keep that mess organized. It tracks your applications, saves the job information
and recruiting updates that matter, and prepares a tailored version of your résumé for each role.
It is built around LaTeX résumé workflows and is designed to work cleanly with templates such as
[Jake's Resume](https://www.overleaf.com/latex/templates/jakes-resume/syzfjbzwjncs). Your original
résumé stays untouched; Erga creates a separate `.tex` file, a readable diff, and a PDF for you to
review.

You can use Erga directly from the command line or optionally connect any MCP-capable assistant.
Those connections extend the same local system; none becomes Erga's system of record. Application
records and generated files stay on your computer.

> [!IMPORTANT]
> Erga organizes the process, but it does not submit applications, send messages, invent résumé
> claims, modify your inbox, or overwrite your original résumé.

## What Erga does

- Keep your applications and status history in one local database.
- Save job postings before they disappear.
- Create a separate folder and tailored résumé for each role.
- Rank the approved project catalogue and, with a sampling-capable host, synthesize new
  evidence-cited project bullets from approved claims plus authenticated authored-Git changes.
- Compile the result to a PDF and show exactly what changed.
- Read limited Gmail or Zoho metadata to spot interviews, assessments, offers, and rejections.
- Use the same tools from the CLI or an MCP client.

Erga does not fill out forms or submit applications. Resume tailoring only uses content already in
your template or facts you added yourself.

## Quick start

### Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git

Optional workflows use [Obsidian](https://obsidian.md/), `latexmk`, an existing LaTeX résumé, a
supported operating-system credential store, or an authenticated
[`gws`](https://github.com/googleworkspace/cli) command.

### Install

Erga is not published to a package registry yet, so `uvx erga` will not work. Clone the repository
and run it locally:

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync
```

Run the interactive setup and verify the installation:

```bash
uv run erga setup
uv run erga doctor
```

The arrow-key wizard imports a complete PDF, DOCX, or `.tex` master résumé into private
hash-verified storage, initializes local application tracking, and enables Erga's client-neutral
`career` MCP profile. Obsidian is an optional human-readable workspace and tracker view. Setup does
not require or configure Obsidian, a coding-AI subscription, Discord bot, Hermes installation, or
separate model API key.

Onboarding labels the master résumé as factual knowledge and a second, optional résumé as
style-only. Use that second file only when you are confident it is a useful reference. A PDF page
count prefills the maximum-page setting; section order and density are recorded as descriptive
metadata, while the editable `.tex` template controls rendered layout. Reference wording can never
authorize claims. The wizard also exposes page and bullet-length controls. Users may enter
minimum/target/maximum character counts directly or paste one or two example bullets to calibrate
those numbers. Example wording is discarded immediately and is never stored as evidence. The
configured range is enforced when CLI or MCP workflows author new `\resumeItem{...}` bullets.
Complete source context and derived style-reference metadata remain behind the explicit
`career-private` profile; the recommended `career` profile does not receive either document.

By default Erga's private machine state is independent of any optional vault:

```text
~/.config/erga-mcp/
├── config.toml
├── state/
│   └── erga.sqlite3
└── generated-resumes/
```

The configuration contains paths and feature settings, never credentials. Use
`--config /absolute/path/to/config.toml` to select another location.
`erga init` remains available as a low-level non-interactive initializer for advanced and scripted
installations.

### Git-backed project tailoring

Project inventory entries may declare one or more GitHub repositories without embedding local
paths or credentials:

```json
{
  "id": "api-platform",
  "title": "API Platform",
  "git_repositories": ["example/api-platform"]
}
```

When at least one inventory entry has this mapping and GitHub CLI is already authenticated, job
intake refreshes a private JSON index of owned and direct-collaborator repositories. It ranks the
full approved catalogue before reading source code, then researches a broader role-relevant
shortlist instead of filtering projects by their existing résumé wording. Erga attributes commits
to the connected GitHub identity and inspects all fetched refs. When the MCP client enables
sampling, its connected model receives bounded approved bullets plus authenticated diff evidence
and returns structured, role-specific project bullets with evidence IDs. Server-side validation
rejects unsupported numbers, cross-project citations, raw commit/file/line accounting, duplicate
lead verbs, unsafe LaTeX, and rendered overflow. The deterministic approved-copy path remains the
fallback when sampling, GitHub, or a selected repository is unavailable.

After core setup, optionally connect any number of coding assistants:

```bash
# Arrow-key multi-select; selecting nothing is valid.
uv run erga connect

# Or configure explicit hosts without an interactive picker.
uv run erga connect --host codex --host claude-code --project-dir /path/to/project
```

This writes only project-scoped MCP entries. It does not install a host, require a host login,
select a model, or request an API key. Use `--dry-run` to inspect the exact configuration first.

Discord is another optional interface. Install its isolated runtime extra, then choose exactly
which existing headless coding CLI should power Discord replies:

This bridge is specifically for users of tools such as Codex, Claude Code, or OpenCode who want
Discord access without already running a messaging gateway. If Hermes, OpenClaw, or another
gateway already manages Discord, connect Erga through MCP there instead of operating a second bot.

```bash
uv sync --extra discord
uv run erga discord configure
uv run erga discord start
```

The bridge supports the same presets plus an advanced custom argument array. It accepts current
Discord usernames such as `emperor_sai` or stable numeric user IDs, stores the bot token only in
the operating-system credential store, and never makes the selected backend a requirement for
Erga's local core. See the [Discord bridge guide](docs/discord.md).

Keryx is an optional public job-discovery source. Enabling it downloads the fixed, public US
internship and new-graduate index into Erga's local state; searches never send a query, résumé,
profile, or application data anywhere:

```bash
uv run erga keryx enable
uv run erga keryx search 'software engineer' --program internship --cycle summer-2027
```

Search results remain untrusted public leads. Erga does not create an application or run résumé
intake until the user separately chooses an individual posting URL. Disable the extension at any
time with `uv run erga keryx disable`. The harmless public cache remains available for a later
re-enable and is removed with the rest of Erga-owned state by `erga uninstall`.

### Add evidence and a draft application

```bash
uv run erga evidence add \
  --source-ref 'Career.md#Pipeline project' \
  --text 'Built a Python pipeline that reduced weekly manual review by 30%.' \
  --approved
```

Use the returned evidence ID to create a local draft:

```bash
uv run erga applications add \
  --company 'Example Company' \
  --role 'Software Engineer' \
  --source-url 'https://jobs.example.com/123' \
  --evidence-id 'ev_a1b2...'
```

Nothing is sent to the employer. Check local state with:

```bash
uv run erga status
uv run erga applications list
```

For résumé setup, mail connectors, job-link routing, and scheduled private alerts, continue with
the [complete getting-started guide](docs/getting-started.md).

## Optional MCP hosts and Hermes

The `erga connect` command supports Codex, Claude Code, OpenCode, OpenCode V2, Gemini CLI, Cursor,
GitHub Copilot CLI, and other clients that use standard `.mcp.json`. Manual registration and Hermes
remain supported:

```bash
uv sync

hermes mcp add erga-mcp \
  --command uv \
  --connect-timeout 30 \
  --env ERGA_MCP_CONFIG=/absolute/path/to/config.toml \
  --env ERGA_MCP_TOOL_PROFILE=career \
  --args --directory /absolute/path/to/erga-mcp run erga-mcp
```

`--args` must remain last. If the gateway routes the chat through a named Hermes profile, add the
same profile flag to MCP and plugin commands (for example, `hermes --profile coder mcp add ...`).
See [`integrations/hermes/mcp.example.yaml`](integrations/hermes/mcp.example.yaml) for the equivalent
configuration file.

The recommended `career` MCP profile includes:

| Tool | Behavior |
| --- | --- |
| `pipeline_status` | Read local record counts |
| `list_applications` | Read local application records |
| `update_application_status` | Set an application to draft, applied, OA, assessment, interview, offer, rejected, or withdrawn in the private local database |
| `application_tracker` | Render the optional configured Obsidian tracker as a compact, read-only message card |
| `list_evidence` | Read local evidence records |
| `search_keryx_jobs` | Search an explicitly enabled local cache of public Keryx roles without network access or application creation |
| `intake_job_url` | Research one job and build local review artifacts end to end |
| `prepare_job_workspace` | Create a bounded local job package from a supplied URL |
| `create_tailored_resume` | Create a proposal, diff, and evidence report |
| `validate_tailored_resume` | Run the configured local LaTeX compiler |

Private archive export, full writing-style source context, mail integration, Hermes monitors, Git
scanning, and token recording are excluded from `career`. Select another documented profile only
when the connected host should receive that additional capability.

With the optional `erga-mcp-router` Hermes plugin enabled, `/erga-tracker` renders that same local Obsidian tracker directly in the current chat, and `/erga-mail-sync` runs a bounded configured-mail sync. Both return compact Markdown that remains readable across Discord, Signal, Telegram, Slack, and other Hermes platforms. The tracker does not write to the vault; the mail command stores metadata-only events and does not expose message bodies, previews, or credentials.

The full list of permissions and safety limits is in [`docs/security.md`](docs/security.md).

## Repository map

```text
src/erga_mcp/          deterministic domain layer, CLI, and MCP server
integrations/hermes/  optional Hermes configuration and router plugin
skills/productivity/  optional workflow skill
cron/                 private notification runner documentation
docs/                 architecture, security, setup, and project direction
tests/                synthetic unit and MCP integration tests
```

## Documentation

- [`docs/getting-started.md`](docs/getting-started.md) — full setup.
- [`docs/mcp-clients.md`](docs/mcp-clients.md) — standard stdio and loopback HTTP setup for non-Hermes MCP clients.
- [`docs/discord.md`](docs/discord.md) — optional private Discord bridge.
- [`docs/security.md`](docs/security.md) — permissions and safety details.
- [`docs/FUTURE.md`](docs/FUTURE.md) — ideas for later.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to run checks and contribute.

## Project status

Erga MCP is **pre-alpha**. The evidence ledger, local application store, deterministic mail
classification, job workspace creation, LaTeX proposal artifacts, read-only mail connectors, and
MCP surface are implemented and tested. Breaking changes are expected before 1.0.

Current limitations:

- no graphical interface;
- no automatic matching between mail events and application records;
- imported Obsidian candidates cannot yet be approved through the CLI;
- relevance ranking is lexical rather than semantic;
- résumé workflows currently target LaTeX; and
- no remote résumé synchronization or automatic job submission by design.

## Development

```bash
uv sync --extra dev
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run python -m unittest discover -s tests -v
uv build
git diff --check
```

Tests and examples use synthetic data. Never commit real résumés, applications, email content,
credentials, contact details, exports, or vault contents.

## Contributing

Issues and pull requests are welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md), follow the
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), and use private vulnerability reporting described in
[`SECURITY.md`](SECURITY.md).

## License

Erga MCP is available under the [MIT License](LICENSE).
