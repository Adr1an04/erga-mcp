# Security model

## Data and credentials

- The repository contains source code, examples, and synthetic tests only.
- Local configuration, SQLite state, imports, exports, generated proposals, and user-provided source files are ignored by Git.
- OAuth refresh tokens and Git tokens belong in the operating-system credential store, never in configuration files, shell history, logs, or repository files.
- The fixture-only Zoho workflow has no OAuth or network behavior. A future live adapter must request `ZohoMail.messages.READ` and may request `ZohoMail.accounts.READ` only when account discovery is necessary. It must reject broader or mutating scopes.
- Mail previews are used only for local classification. The store retains normalized message metadata and classification, not preview/body content.

## Local MCP trust boundary

The MCP server is a local **stdio** process. It is not a security sandbox: it runs with the permissions of the Hermes client that starts it. Review the complete executable command, arguments, environment variables, and absolute paths before enabling it.

The server is intentionally not a localhost HTTP service. Stdio restricts access to the configured client process and avoids exposing a network listener. The server must keep stdout reserved for MCP protocol traffic; diagnostics belong on stderr.

The example configuration passes one non-secret configuration-file path only. Do not pass tokens, a home-directory path, or a broad environment through the MCP configuration. Configure only the project and pipeline paths that the server needs.

The server declares tool annotations so MCP clients can distinguish its capability classes:

| Tools | Capability | Effect |
| --- | --- | --- |
| `pipeline_status`, `list_applications`, `list_evidence`, `list_mail_events` | read-only | Reads local SQLite state only. |
| `intake_job_url` | network-read + local-write | Fetches one validated public job URL and creates or reuses a configured local review package. |
| `prepare_job_workspace` | network-read + local-write | Fetches a job URL and creates configured local package/tracker artifacts. |
| `create_tailored_resume` | local-write | Writes a reviewable proposal, diff, and claim report inside a configured package. |
| `validate_tailored_resume` | local-exec | Runs the configured local LaTeX validator on an explicit proposal. |

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
weakening SSRF controls. Imported page text remains data and is never evaluated as an instruction.

Obsidian import is read-only, requires an explicitly configured vault root, rejects paths outside that root, and creates unapproved evidence candidates. Applications and résumé proposals may reference approved evidence only.

## Human authority

The pipeline may prepare research, local records, draft updates, and reviewable résumé diffs. It does not submit forms, send messages, mutate mail, or synchronize a remote résumé without an explicit user action.

## References

- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [MCP transports specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
