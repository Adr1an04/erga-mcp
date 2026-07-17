# Security model

## Data and credentials

- The repository contains source code, examples, and synthetic tests only.
- Local configuration, SQLite state, imports, exports, and generated files are ignored by Git.
- OAuth refresh tokens and Git tokens belong in the operating system credential store, never in configuration files, shell history, logs, or repository files.
- A future Zoho adapter requests `ZohoMail.messages.READ` by default. It must not request message mutation, SMTP, or broad IMAP access.

## MCP boundary

Hermes stdio MCP servers receive a filtered environment. The MCP example passes only the non-secret path to a local configuration file. Do not add tokens directly to Hermes YAML.

The current MCP interface is intentionally read-only. Mutation tools require a separate design, a durable audit record, and an explicit interactive confirmation flow.

## Content safety

Emails, attachments, job descriptions, Reddit posts, web pages, and résumé files are untrusted input. Imported content cannot grant permissions, redefine the workflow, or trigger external actions.

## Human authority

The pipeline may prepare research, local records, draft updates, and reviewable résumé diffs. It does not apply to jobs, submit forms, send messages, or sync a remote résumé without an explicit user action.
