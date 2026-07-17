# ADR-001: Local Core, Evidence Ledger, and Narrow Hermes Integrations

- **Status:** Proposed
- **Date:** 2026-07-16

## Context

Recruiting automation is useful for capture, organization, and reminders, but poor at inventing accurate career claims. The product must preserve a user's career context over time, use only authorized data sources, keep sensitive data local, and never submit applications automatically.

## Decision

Build the project as a **standalone local-first CLI and domain library with a local MCP server**, then expose narrow optional integrations to Hermes. Do not make a Hermes plugin or an LLM the system of record.

### System of record

Use a local SQLite database for normalized pipeline state and a user-selected Obsidian vault for durable, human-readable career material.

- Obsidian remains the editable narrative source for experience, projects, accomplishments, and application notes.
- SQLite holds machine-safe state: source IDs, sync cursors, job/application records, evidence references, classification decisions, approval state, and an append-only audit log.
- The pipeline may write only to explicitly configured Obsidian files/folders. Initial support should generate proposed updates or update narrowly defined application records, not rewrite arbitrary notes.

### Evidence ledger for résumé integrity

A résumé bullet must be a derived artifact, never a free-form model output.

Each career claim records:

- source note/file and stable heading or block reference;
- direct evidence text;
- scope, role, organization, and date range;
- metric value, unit, measurement basis, and confidence when known;
- visibility/confidentiality classification; and
- approval status.

The résumé proposer can only use approved evidence. It must return citations to evidence IDs and flag missing metrics as questions. It may suggest how to quantify an achievement, but must never fabricate numbers, tools, ownership, outcomes, or dates.

### Automation boundary

1. **Zoho intake:** a read-only Zoho Mail OAuth adapter polls a user-designated folder. It stores source message IDs and minimal normalized metadata, then classifies messages into candidate events. Request `ZohoMail.messages.READ`; add `ZohoMail.accounts.READ` only if account discovery cannot be configured locally without it.
2. **Application state:** deterministic rules handle known acknowledgement/denial phrases. High-confidence acknowledgement events may update the *local* tracker automatically with the source message, confidence, notification, and undo/audit record; ambiguous messages are queued for review.
3. **Job enrichment:** a job description URL/manual capture is the primary source. Optional web research produces cited notes, with source URLs and capture time. Reddit is optional enrichment only; use authorized APIs or user-provided links, preserve attribution, and never treat anecdotal content as fact.
4. **Résumé tailoring:** matching selects approved evidence against a saved job description. The result is a proposed LaTex patch plus a claim report and a PDF diff/compile result.
5. **Overleaf:** use an Overleaf Git working copy as the initial integration. Tokens live in the OS credential store. The pipeline creates a local branch/patch; the user reviews the diff and explicitly initiates any push/sync.
6. **Submission:** always outside the automation boundary. The product can prepare a checklist, opened links, tailored documents, and tracking data, but never completes an external application form or submits it.

### Hermes integration

- Ship a local stdio **MCP server** from this repository as the primary Hermes boundary. Expose focused tools such as `list_candidate_emails`, `search_career_context`, `list_applications`, `apply_local_update`, and `propose_resume_edit`.
- Configure Hermes to pass only the required secret names to the MCP subprocess; its filtered stdio environment must not inherit the entire user shell.
- Use versioned skills for classification, evidence standards, and human-approval policy—not for credential-handling or data access code.
- Use a dedicated `recruiting` Hermes profile so its cron jobs, MCP configuration, logs, skills, and credentials are isolated from the everyday assistant profile. Profiles do not isolate the filesystem; stronger isolation later requires a container/backend with explicit mounts.
- Use Hermes cron in two stages: a no-agent collection script fetches and normalizes read-only email data; a separate bounded agent job classifies only the new structured records and produces reviewable changes.
- A native Hermes plugin is optional for ergonomic slash commands/hooks later. Do not use project-local plugins as the primary distribution mechanism: they are privileged code and require explicit enablement.

## Rationale

This separates deterministic, testable data handling from model reasoning; gives the user an auditable evidence chain for every résumé claim; and keeps the product useful without requiring a particular agent runtime.

## Consequences

- The first usable release is intentionally narrow: email-to-application tracking, Obsidian-backed evidence capture, and reviewable résumé patch proposals.
- Full real-time inbox/webhook support, automatic data enrichment, and autonomous application workflows are deferred.
- Overleaf editing depends on the user's available Git integration. A local LaTeX repository must remain a supported fallback.

## Sources

- [Hermes plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [Hermes scheduled tasks](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)
- [Zoho Mail OAuth 2.0 guide](https://www.zoho.com/mail/help/api/using-oauth-2.html)
- [Zoho Mail message-content API](https://www.zoho.com/mail/help/api/get-email-content.html)
- [Overleaf Git authentication tokens](https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/git-integration/git-integration-authentication-tokens)
- [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms)
- [Greenhouse Job Board API](https://developers.greenhouse.io/job-board.html)
