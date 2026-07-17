# Initial Technology Landscape

## Hermes Agent

Hermes supports local plugins for custom tools, hooks, and integrations without modifying its core. A plugin directory contains a manifest and Python registration/handler code. Hermes also supports scheduled jobs, including no-agent script mode and fresh agent sessions.

**Implication:** keep recruiting logic in an independently testable local CLI/library and expose it through a local stdio MCP server. Use versioned skills for workflow/policy, a plugin only for optional Hermes-native ergonomics, and cron for orchestration—not as the data model. MCP's filtered subprocess environment provides a narrower credential boundary than a generic terminal session.

- [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [Scheduled tasks](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)

## Zoho Mail

Zoho Mail's REST API uses OAuth 2.0. The email-content endpoint documents `ZohoMail.messages.READ` as a read-only alternative to `ZohoMail.messages.ALL`; account discovery needs a matching read scope.

**Implication:** avoid IMAP app passwords and SMTP. Use a dedicated OAuth Self Client with the smallest read scopes, a user-selected folder, message-ID deduplication, and explicit adapter-level restrictions against mutation endpoints.

- [OAuth 2.0 guide](https://www.zoho.com/mail/help/api/using-oauth-2.html)
- [Get email content](https://www.zoho.com/mail/help/api/get-email-content.html)
- [Message API index](https://www.zoho.com/mail/help/api/email-api.html)

## Overleaf

Overleaf documents Git integration using an authentication token associated with the project owner or collaborator. Git integration availability may depend on the user's Overleaf plan.

**Implication:** version a local LaTeX worktree and generate reviewable patches locally. Treat an Overleaf push as an explicit human-authorized synchronization action. Do not rely on an undocumented editing API.

- [Git integration](https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/git-integration)
- [Git authentication tokens](https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/git-integration/git-integration-authentication-tokens)

## Career accuracy

Models can summarize, select, and rephrase supplied evidence; they cannot reliably infer undisclosed scope, metrics, or results. The architecture therefore requires source-linked claims, metric provenance, and user approval before a claim appears in a résumé.

## Research to validate before implementation

1. Exact Zoho data-center endpoints and the final minimum scopes in the user's tenant.
2. Overleaf plan availability and whether Git integration is enabled for the target project.
3. Preferred local packaging: Python/uv CLI first is the current recommendation because it aligns with Hermes plugin development, but this needs a small spike.
4. A public-data policy for company/recruiting research, including rate limits, attribution, robots/terms, and a compliant Reddit data source.
