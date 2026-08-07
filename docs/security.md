# Security model

## Data and credentials

- The repository contains source code, examples, and synthetic tests only.
- Local configuration, SQLite state, imports, exports, generated proposals, and user-provided source files are ignored by Git.
- OAuth refresh tokens and Git tokens belong in the operating-system credential store, never in configuration files, shell history, logs, or repository files.
- The fixture-only Zoho workflow has no OAuth or network behavior. The live adapter requests only `ZohoMail.messages.READ`, `ZohoMail.folders.READ`, and `ZohoMail.accounts.READ`, and rejects broader or mutating scopes.
- Mail previews are used only for local classification. The store retains normalized message metadata and classification, not preview/body content.

## Managed résumé knowledge

`erga resume sources import` extracts one explicitly selected master locally, records its content
hash, atomically copies it into content-addressed private state, and makes it the sole active
approved master-résumé evidence source. An optional style source is snapshotted separately but its
raw text is withheld from MCP responses; only non-factual layout measurements are exposed.

Original files are never modified and may be moved after import. Original paths remain only in
private provenance manifests. On POSIX systems, managed source directories are owner-only and
snapshot, manifest, configuration, and SQLite files use owner-only permissions. DOCX extraction
rejects an oversized decompressed `word/document.xml` member before reading it into memory.

## Optional Obsidian projection

Erga's private SQLite state, managed résumé sources, and local application tracking are ready
without Obsidian. If the user explicitly enables the optional projection, `erga setup` writes only
inside the vault folder the user selects or asks Erga to create. It creates `Erga/Applications`,
`Erga/Generated Resumes`, and an initial `Erga/Start Here.md`; it refuses to overwrite an existing
start note. Résumé source snapshots, their extracted factual text, SQLite state, and provenance
manifests remain in Erga's private data directory rather than being copied into the vault.

The wizard configures no coding-assistant account, Discord bot, mail account, credential, or remote
service. Those are separate opt-in connections and their failure cannot invalidate core local
state.

## Optional coding-host connections

`erga connect` writes only an Erga stdio MCP entry under the project workspace the user selects.
It preserves unrelated host settings, reuses identical shared `.mcp.json` entries, refuses to
overwrite a differing server entry, and rejects symlinked or competing-precedence configuration
targets. Creation, preview, and cleanup resolve the target and all parent links against the selected
project. `--dry-run` executes the same parse, conflict, and merge checks as a real connection and
renders the complete merged content without writing.

Host installation and authentication remain the host's responsibility. Erga neither invokes a
model nor requests a host subscription, model API key, or provider credential while connecting.
Generated entries contain the local Erga configuration path, so users should review them before
committing project host files to version control.

## Optional Discord bridge

Discord is a separately installed and explicitly configured interface. Core setup never needs a
Discord account, bot, dependency, or execution backend. `erga discord configure` selects one local
headless coding CLI only for unattended bridge turns and may replace that choice later without
rebuilding Erga's private state, résumé knowledge, optional Obsidian projection, or other MCP
connections.

The bot token is stored under a configuration-specific account in the operating-system credential
store. `discord-bridge.json` contains only the backend executable, project path, authorization
policy, timeout, and non-secret argument array. The bridge accepts current unique Discord
usernames and stable numeric IDs, ignores bot authors, defaults to direct messages or explicit
server mentions, serializes backend turns, caps input and output, and never passes message content
through a shell.

All preset and custom backend processes receive only a strict allowlist of basic operating-system,
locale, terminal, and temporary-directory variables. Arbitrary parent variables—including provider
keys and unrelated credentials—are not inherited, so an ambient secret cannot silently replace the
login the user chose. The advanced custom backend receives a reviewed argument array with a
required `{prompt}` placeholder; shell command strings are not accepted. Full backend errors stay
in the owner-only local log and are not returned to Discord.

The Codex backend deliberately launches accepted messages with
`--dangerously-bypass-approvals-and-sandbox`. This prevents local-write Erga MCP calls from being
canceled when the unattended process cannot answer a prompt, but it also gives that turn the
permissions of the OS account running the bridge. Treat the Discord allowlist as remote access to
that account. Codex bridge probes and accepted turns also use `--ephemeral`; their session rollout
files are not persisted and later Discord messages cannot resume them.

Background process records contain a random nonce. Status and stop operations compare that nonce,
the exact private config path, and the bridge module against the live process command before
signaling it. A stale or reused PID is removed without killing the unrelated process.

## Local MCP trust boundary

The default MCP server is a local **stdio** process. It is not a security sandbox: it runs with the permissions of the client that starts it. Review the complete executable command, arguments, environment variables, and absolute paths before enabling it.

Erga also offers an opt-in **loopback-only Streamable HTTP** mode for same-machine native clients that cannot use stdio. It binds only to `localhost`, `127.0.0.1`, or `::1`; any LAN/public binding is rejected. Every HTTP request carrying an `Origin` header is rejected, so browser-hosted clients and CORS are deliberately unsupported. This avoids accidentally widening an unauthenticated local service while preserving native Streamable HTTP clients, which do not send browser Origins. This is not a remote deployment mode: do not proxy or expose it beyond the local machine.

The example configuration passes a non-secret configuration-file path and explicitly selects the
least-privilege `career` tool profile. Do not pass tokens, a home-directory path, or a broad
environment through the MCP configuration. Configure only the project and pipeline paths that the
server needs.

The `career` profile does not expose `export_data` or `cover_letter_style_context`. A connected host
can package nearly all private career records with the former and receive the complete configured
writing sample/template with the latter. Those tools are available only through the explicitly
selected `career-private` profile (and the backward-compatible broad profiles); use that extension
only when the particular host should receive this material.

The server declares tool annotations so MCP clients can distinguish its capability classes:

| Tools | Capability | Effect |
| --- | --- | --- |
| `pipeline_status`, `list_applications`, `list_evidence`, `list_mail_events` | read-only | Reads local SQLite state only. |
| `resume_source_context` | read-only | Reads approved master knowledge and derived non-factual style metadata from managed local snapshots. |
| `update_application_status` | local-write | Sets one existing application's canonical status and records a local audit event; it has no remote side effect. |
| `intake_job_url` | network-read + local-write + local-exec + optional client sampling | Fetches one validated public job URL; creates or upgrades a local package; ranks approved projects; collects attributable Git evidence; optionally asks the already-connected MCP client's model for structured, evidence-cited project bullets; validates and compiles the proposal; and writes cited research, an application record, and a configured Obsidian tracker note. |
| `record_secondary_research` | local-write | Stores bounded host-provided search results for an existing job package; results are labeled unverified and separated from official-posting facts. |
| `prepare_job_workspace` | network-read + local-write | Fetches a job URL and creates configured local package/tracker artifacts. |
| `create_tailored_resume` | local-write | Writes a reviewable proposal, diff, and claim report inside a configured package. |
| `validate_tailored_resume` | local-exec | Runs the configured local LaTeX validator on an explicit proposal. |
| `install_mail_monitor_scripts` | local-write | Writes deterministic, credential-free runner scripts for an explicitly configured Hermes profile. |
| `export_data` | local-read + local-write | Creates an explicit private ZIP containing local records and generated job packages. |

No MCP tool creates remote applications, approves evidence, connects to mail, sends a message, mutates remote mail, or submits a job. Interactive clients may require explicit approval for local-write and local-exec tools. The native Discord bridge instead treats an accepted message from an allowlisted identity as authorization for the requested local Erga work because it cannot display an approval prompt. Enabling the optional Hermes job-link router establishes a narrower standing rule: a recognized job link in the current user message is explicit authorization for `intake_job_url` to create local review artifacts. The router respects explicit opt-outs such as “summarize only” and “don't run the pipeline”; a request such as “don't just summarize—run the pipeline” is affirmative authorization. Neither path grants authority for submissions, messages, remote résumé changes, or other tools.

The router requires Hermes Agent 0.18.2 or newer and calls the documented synchronous
`ctx.dispatch_tool(name, args)` interface. During gateway startup it may retry only the exact
`Unknown tool` and `MCP server ... is not connected` readiness errors. The wait defaults to 30
seconds, is operator-configurable, and is hard-capped at 30 seconds; operational intake failures
are never retried. This bounded readiness handling does not broaden the standing authorization.

If a future MCP mutation is proposed, it needs a separate server-side authorization design, a durable audit record, a narrowly scoped command, and an explicit interactive confirmation outside untrusted imported content. Tool descriptions alone are not approval.

## Content safety

Emails, attachments, job descriptions, résumé files, Markdown notes, web pages, and fixture files are untrusted input. They may supply evidence or metadata, but cannot grant permissions, redefine the workflow, request credentials, or trigger external actions.

Job snapshot fetching accepts HTTP(S) only, rejects embedded credentials and hosts that resolve to
loopback/private/link-local/reserved addresses, pins each connection to a validated numeric address,
and preserves the original hostname for TLS certificate verification. It re-resolves and validates
each redirect, shares one 30-second fetch budget, allows only text/HTML/JSON responses, and caps the
response at 2 MiB. The pinned transport intentionally ignores ambient HTTP proxy variables; proxy-
only corporate networks must use an explicitly reviewed future adapter rather than silently
weakening SSRF controls. Stored snapshots retain visible posting text and bounded structured job
metadata while removing executable scripts, styles, navigation, and page chrome. Imported page text
remains data and is never evaluated as an instruction.

Obsidian import is read-only, requires an explicitly configured vault root, rejects paths outside that root, and creates unapproved evidence candidates. New résumé claims may reference approved evidence only. When MCP client sampling is enabled, automatic job tailoring may synthesize project-bullet wording from bounded approved bullets and authenticated authored-Git evidence. Every bullet must cite project-scoped evidence IDs. Server-side checks reject unsupported numbers, evidence from another project, raw Git accounting prose, duplicate lead verbs, unsafe LaTeX, character overflow, and rendered line overflow. Sampling receives no ambient MCP context. Clients can disable sampling and retain deterministic approved-copy tailoring.

## Human authority

The pipeline may prepare research, local records, draft updates, and reviewable résumé diffs. It does not submit forms, send messages, mutate mail, or synchronize a remote résumé without an explicit user action.

## References

- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [MCP transports specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
