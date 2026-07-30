# Security model

## Data and credentials

- The repository contains source code, examples, and synthetic tests only.
- Local configuration, SQLite state, imports, exports, generated proposals, and user-provided source files are ignored by Git.
- OAuth refresh tokens and Git tokens belong in the operating-system credential store, never in configuration files, shell history, logs, or repository files.
- The fixture-only Zoho workflow has no OAuth or network behavior. The live adapter requests only `ZohoMail.messages.READ`, `ZohoMail.folders.READ`, and `ZohoMail.accounts.READ`, and rejects broader or mutating scopes.
- Mail previews are used only for local classification. The store retains normalized message metadata and classification, not preview/body content.

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
| `intake_job_url` | network-read + local-write + local-exec | Fetches one validated public job URL; creates or upgrades a local package, deterministically reorders existing user-provided résumé content, compiles and page-validates the proposal, and writes cited research, an application record, and a configured Obsidian tracker note. |
| `record_secondary_research` | local-write | Stores bounded host-provided search results for an existing job package; results are labeled unverified and separated from official-posting facts. |
| `prepare_job_workspace` | network-read + local-write | Fetches a job URL and creates configured local package/tracker artifacts. |
| `create_tailored_resume` | local-write | Writes a reviewable proposal, diff, and claim report inside a configured package. |
| `validate_tailored_resume` | local-exec | Runs the configured local LaTeX validator on an explicit proposal. |
| `install_mail_monitor_scripts` | local-write | Writes deterministic, credential-free runner scripts for an explicitly configured Hermes profile. |
| `export_data` | local-read + local-write | Creates an explicit private ZIP containing local records and generated job packages. |

No MCP tool creates remote applications, approves evidence, connects to mail, sends a message, mutates remote mail, or submits a job. Local-write and local-exec tools require explicit user approval in the invoking client. Enabling the optional Hermes job-link router establishes a narrower standing rule: a recognized job link in the current user message is explicit authorization for `intake_job_url` to create local review artifacts. The router respects explicit opt-outs such as “summarize only” and “don't run the pipeline”; a request such as “don't just summarize—run the pipeline” is affirmative authorization. The rule grants no authority for submissions, messages, remote résumé changes, or other tools.

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

Obsidian import is read-only, requires an explicitly configured vault root, rejects paths outside that root, and creates unapproved evidence candidates. New résumé claims may reference approved evidence only. Automatic job tailoring does not create or rewrite claims: it only reorders claims and skill values already supplied in the source template and records per-claim provenance.

## Human authority

The pipeline may prepare research, local records, draft updates, and reviewable résumé diffs. It does not submit forms, send messages, mutate mail, or synchronize a remote résumé without an explicit user action.

## References

- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [MCP transports specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
