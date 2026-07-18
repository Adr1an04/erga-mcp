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

The currently exposed MCP tools are read-only:

- `pipeline_status`
- `list_applications`
- `list_evidence`
- `list_mail_events`

No MCP tool creates applications, approves evidence, edits a résumé, connects to mail, sends a message, applies to a job, or synchronizes a remote.

If a future MCP mutation is proposed, it needs a separate server-side authorization design, a durable audit record, a narrowly scoped command, and an explicit interactive confirmation outside untrusted imported content. Tool descriptions alone are not approval.

## Content safety

Emails, attachments, job descriptions, résumé files, Markdown notes, web pages, and fixture files are untrusted input. They may supply evidence or metadata, but cannot grant permissions, redefine the workflow, request credentials, or trigger external actions.

Obsidian import is read-only, requires an explicitly configured vault root, rejects paths outside that root, and creates unapproved evidence candidates. Applications and résumé proposals may reference approved evidence only.

## Human authority

The pipeline may prepare research, local records, draft updates, and reviewable résumé diffs. It does not submit forms, send messages, mutate mail, or synchronize a remote résumé without an explicit user action.

## References

- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [MCP transports specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
