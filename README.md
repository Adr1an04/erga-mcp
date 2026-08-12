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
count prefills the maximum-page setting; section presence, order, repeatable content pools, and
project slots become a private layout profile that drives the standalone `.tex` template and every
later tailoring run. Erga generates that template from the master's approved text. Users may
provide PDF or DOCX input without creating LaTeX. Reference wording can never authorize claims.
The wizard also exposes page and bullet-length controls. Users may enter
minimum/target/maximum character counts directly or paste one or two example bullets to calibrate
those numbers. Example wording is discarded immediately and is never stored as evidence. The
configured range is enforced when CLI or MCP workflows author new `\resumeItem{...}` bullets.
Complete source context and derived style-reference metadata remain behind the explicit
`career-private` profile; the recommended `career` profile does not receive either document.

### Change or reset the résumé template

Add or replace the factual master with one direct command. The current visual-template choice is
preserved and regenerated against the new source of truth:

```bash
uv run erga resume master set "/absolute/path/to/master-resume.pdf"
```

Add or replace the optional visual template independently:

```bash
uv run erga resume template set "/absolute/path/to/preferred-template.pdf"
```

Both commands accept PDF, DOCX, and `.tex` files. Erga copies the selected file into private local
state before using it. The master is facts only: its page count, margins, typography, spacing,
section order, and visual quality never affect the generated layout. The template command preserves
the master and reads only section presence,
order, density, project slots, and the exact bullets-per-entry pattern from the selected file. For
PDF templates it also measures each page margin, body/header/section typography, small-caps
treatment, section-rule presence and weight, line height, entry inset, bullet-glyph size, label gap,
bullet-text indentation, section gaps, entry gaps, and item spacing. These visual constraints survive
the one-page packing pass, and template wording can never introduce résumé claims. Erga first uses
all approved, layout-safe content that fits, including a final project entry with the supported bullets
that remain when a full repeated pattern will not fit. It never stretches a supplied template's line,
item, entry, or section gaps to manufacture density. A template with one bullet per project therefore
produces one bullet per selected
project; a template with a two/three/two pattern preserves and repeats that pattern across selected
approved entries. Observed section totals guide density rather than acting as hard quotas, so Erga
can use additional approved projects when the master has fewer experiences than the style example.
Generated templates also enforce fit rules that do not depend on an AI host: contact details remain
one centered row (scaling only when necessary), project dates reserve a right-hand column while a
long title wraps on the left, project technology stacks occupy their own single measured line (scaling
only when necessary), and technical-skill labels reserve their own width
so skill values wrap cleanly instead of crossing the right margin. Existing generated templates are
refreshed automatically when this layout schema changes.
To remove the style/custom template and return to Erga's default Jake-style layout, run:

```bash
uv run erga resume template reset
```

Reset preserves the approved master résumé, evidence, and application history. It clears only the
configured style/template pointers, generates a new private template from the master, and keeps
the previous content-addressed template files recoverable. Use `--config /path/to/config.toml` with
any command when not using the default configuration. The lower-level
`erga resume sources import --master ... --style ...` command remains available when a script needs
to replace both sources together.

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

### Revisit onboarding from any client

The setup wizard can collect a comma-separated skill inventory and zero or more explicit existing
project roots. The same state remains manageable without Discord or an MCP host:

```bash
uv run erga onboarding status
uv run erga onboarding status --json
uv run erga onboarding skills set --csv "Python, FastAPI, React, PostgreSQL"
uv run erga onboarding skills list
uv run erga onboarding skills check FastAPI
uv run erga onboarding skills uncheck React
uv run erga onboarding skills remove React
uv run erga onboarding roots add /absolute/path/to/projects
uv run erga onboarding roots list
uv run erga settings --json

# Scan only the roots the user explicitly saved; no home-directory crawl occurs.
uv run erga git scan --configured-roots
uv run erga git skills show --json
uv run erga git skills show --filter seeded_and_confirmed
uv run erga git skills approve fastapi
uv run erga git skills skip react
uv run erga git projects
uv run erga git projects --query python --page 2
# Explicitly refresh the private GitHub metadata cache, then browse it.
uv run erga git projects --refresh
```

Skill inventory entries are self-reported discovery hints. They improve audited alias matching,
ordering, and review status, but never become approved evidence or résumé claims by themselves.
`--seed-csv` can supply one-shot review hints to `erga git scan` without persisting them. A skill can
enter approved evidence only when a Git candidate corroborates it and the user explicitly approves
that group. Missing metrics remain missing.

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
lead verbs, semantically interchangeable bullets that merely change the opening verb, unsafe LaTeX,
one- or two-word final bullet lines, and one-page PDFs whose text occupies
less than the configured page-height ratio (82% by default). Lead-verb uniqueness is required even for older
configs that contain the former `false` default. Project bullet count is automatic rather than a
setup choice: the model produces one bounded pool of up to four evidence-backed bullets per project,
then local PDF trials begin with one bullet per project and add every supported bullet that still
fits cleanly without creating a second page. Balanced multi-line bullets are allowed; candidates
that strand only one or two words on the final line are replaced from approved evidence before
publication.

Project ranking is contrastive rather than raw keyword overlap. Each eligible project gets an
identity profile from approved copy: technologies, engineering narratives, evidence tier, supported
metric categories, and bullet-quality score. Selection combines role relevance (40%), approved-copy
quality (30%), differentiation from projects already selected (20%), and provenance strength (10%),
with a small bonus for uncovered role signals. Metric categories include adoption, performance,
scale, reliability, organizational scope, delivery, competition, and functional scope. Git commit,
file, language, and line counts remain Tier C activity accounting: useful research context, never a
résumé outcome. The claim report records the profiles, pairwise differentiation, repeated metric
stories, and exact selection rationale so a reviewer can see why every project won.

Project insertion is also template-contract driven. Before replacing any project, Erga inspects
real project entries in the supplied template and deterministically classifies their heading shape:
inline `Project | Technologies`, structured three-argument, intentional right-column technologies,
or title-only. Every selected inventory block is rewritten to that observed argument contract, and
generation stops before compilation if the resulting headings deviate. This check is independent of
the tailoring model and avoids imposing one user's LaTeX macro semantics on another user's résumé.

PDF/DOCX-derived templates use the same agent-independent render search across semantic experience,
project, open-source, and skills groups. Layout-preserving PDF extraction keeps wrapped source
bullets attached to their headings, and a binary render search retains the fullest valid one-page
content budget. Only remaining whitespace
receives layout spacing, so the density pass spends no extra model tokens, rewrites no claims, and
adds no filler text. The
deterministic approved-copy path remains the fallback when sampling, GitHub, or a selected repository
is unavailable.

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
uv run erga discord connect
```

The bridge supports the same presets plus an advanced custom argument array. It accepts current
Discord usernames such as `emperor_sai` or stable numeric user IDs, stores the bot token only in
the operating-system credential store, and never makes the selected backend a requirement for
Erga's local core. Codex-backed Discord turns run noninteractively with Codex approvals and
sandboxing bypassed so write-capable Erga tools cannot be canceled while no terminal is present.
They use ephemeral `gpt-5.6-terra` sessions, so turns do not share conversation memory.
Accepted requests receive one live, in-place Erga card with elapsed time and honest workflow
status; résumé cards become color-coded ready, review-required, or safely-stopped results when
semantic-accent system.
Keep the bot private and the Discord allowlist minimal; the bridge can access everything available
to its OS account. See the [Discord bridge guide](docs/discord.md).

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
| `research_navigator` | Open a stage-aware, read-only OA/interview/offer view with saved research and public links |
| `discover_job_research` | Review a multi-query official/program/engineering/process/OA/community/technical source plan, retain role-relevant results with quality and recency evidence, save a cited note plus JSON audit index, and discover public recruiter leads; never sends outreach |
| `create_research_brief` | Create or refresh a concise local OA, interview, or offer preparation checklist |
| `onboarding_status` | Render the shared onboarding completion card without changing state |
| `erga_settings_card` | Render a redacted local settings dashboard without credentials or secret paths |
| `git_skill_review_card` | Paginate self-reported, Git-confirmed, and Git-discovered skill groups without approving them |
| `project_catalogue` | Search and paginate approved-inventory and cached GitHub projects with repository links, technologies/tags, local Git activity, evidence coverage, and truthful résumé eligibility |
| `refresh_project_catalogue` | Refresh the private GitHub metadata cache through the already-authorized GitHub CLI, then return the same catalogue; never approves evidence or changes a résumé |
| `create_tailoring_plan` | Fetch one official posting and persist review-only project/copy choices without creating an application, résumé, package, or tracker entry |
| `update_tailoring_plan` | Show, answer, revise, approve, or cancel a persisted tailoring plan without generation |
| `execute_tailoring_plan` | Run validated intake only after explicit plan approval, using the saved posting and locked project/copy decisions |
| `update_skill_inventory` | Explicitly set/add/check/uncheck/remove self-reported discovery hints; never creates evidence |
| `manage_portfolio_roots` | Explicitly set/add/list/remove existing local roots; never performs an implicit home crawl |
| `review_git_skill_group` | Inspect, skip, restore, or explicitly approve a Git-corroborated group |
| `list_evidence` | Read local evidence records |
| `search_keryx_jobs` | Search an explicitly enabled local cache of public Keryx roles without network access or application creation |
| `intake_job_url` | Research one job and build local review artifacts end to end |
| `prepare_job_workspace` | Create a bounded local job package from a supplied URL |
| `create_tailored_resume` | Create a proposal, diff, and evidence report |
| `validate_tailored_resume` | Run the configured local compiler and enforce page-count and fill guarantees |
| `propose_project_metrics` | Analyze one explicit local Git worktree for author-attributed engineering context and deterministic test-case, HTTP-route, and CLI-command scope; excludes generated assets, dependencies, locks, snapshots, docs, and data, and never promotes commit, file, language, or line counts into résumé claims |

Private archive export, full writing-style source context, mail integration, Hermes monitors, and
persistent Git scanning are excluded from `career`. `propose_project_metrics` is deliberately
included because it reads only the explicitly supplied worktree, requires an author email, and never
writes evidence or a résumé. Results remain `engineering_context_only` unless deterministic
functional scope is available; every proposal requires review. During job intake, Git verifies
attributable implementation details and can provide scope from authored test cases, HTTP routes,
and CLI commands. Activity
counts cannot satisfy résumé-quality quantitative coverage. Outcome, adoption, performance,
reliability, shipped-feature, and organizational-scope metrics must still be supported by approved
project evidence. If the combined evidence cannot meet the master résumé's quality bar, Erga keeps
stronger master project copy instead. Select another documented profile only when the connected
host should receive additional capability.

With the optional `erga-mcp-router` Hermes plugin enabled, `/erga-onboard`, `/erga-settings`,
`/erga-git`, and `/erga-tracker` render the same shared local cards directly in the current chat.
Discord receives compact, allowlist-protected controls with opaque single-use state and a 15-minute
expiry; text-only clients receive the same action instructions instead of false button success.
In Discord, `/intake-job <job-posting-url>` starts a persisted résumé plan instead of immediately
generating. One emoji-button question compares up to three evidence-backed project portfolios; a
second chooses evidence-backed synthesis or preservation of approved master copy. The review screen
locks those choices, and only **🚀 Generate résumé** creates the local application/package and runs
PDF validation. Planning is deterministic and model-free, reuses its saved official posting during
generation, and creates no application, résumé, package, or tracker entry by itself. Generation runs
in the background and posts the validated PDF to the Discord channel where the user approved it.
The completion path requires the validated PDF to exist inside the package, forces native-document
delivery, and retries the whole message-plus-attachment operation three times; it never emits a
green completion for a missing attachment.
Sending a plain job URL outside `/intake-job` preserves the existing direct-intake behavior.
`/erga-settings` marks incomplete stages as **Needs setup** and provides mobile-friendly actions:
explicit audited skills can be imported from approved evidence in one tap, a conventional projects
folder can be added in one tap when safely detected without crawling the home directory, and manual
setup buttons return short copyable commands. When delivered through Hermes, the card reports the
Hermes messaging connection instead of incorrectly requiring Erga's optional standalone Discord
bridge.
Starting a Git scan without a configured root returns this guided setup card instead of a backend
error. Choosing the detected-folder action from the Git view configures that explicit root and
continues the original scan immediately.
`/erga-tracker all page 2` and searches such as
`/erga-tracker applied page 2` work on text-only platforms too. Each available company links to its
saved posting. Once a role reaches OA, interview, or offer, Discord adds a **Research · Company**
control. It opens the official posting, deduplicated public links, saved-research inventory,
résumé/package context, next action, and stage-specific preparation guidance. Community and
secondary sources stay visibly unverified; explicit controls can refresh bounded public research
or create the concise local stage brief. `/erga-git` is the only Discord Git entry point: its
**Scan Git projects** control,
or `/erga-git scan`, runs the candidate and diff-research pipeline over roots saved during
onboarding and refreshes the same review UI. Explicit root overrides use
`/erga-git scan /path/to/projects`; `/erga-git projects` browses the entire cached catalogue,
`/erga-git projects python page 2` searches it, and its refresh control updates only the private
GitHub metadata cache. A project is résumé-eligible only when its bullets trace to approved
evidence; discovering a repository never auto-approves it. `/erga-git review confirmed page 2`
filters the resulting skill queue. Changes that require user input stay explicit commands such as
`/erga-onboard skills set Python, FastAPI`.
`/erga-mail-sync` runs a bounded configured-mail sync. These commands return compact
Markdown that remains readable across Discord, Signal, Telegram, Slack, and other Hermes platforms.
The tracker does not write to the vault; the mail command stores metadata-only events and does not
expose message bodies, previews, or credentials.

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
