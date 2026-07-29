# ADR-002: Client-Neutral Reasoning Hosts

## Status

Accepted.

## Context

Erga's deterministic core and MCP server are portable, but the original onboarding and automatic
job-link routing were optimized around Hermes. The complete `intake_job_url` workflow was exposed
only by the legacy `default` tool profile, forcing other MCP clients to accept unrelated mail,
monitor, Git-research, and token-recording tools.

Many candidates already pay for or receive subsidized model access through a coding-agent product.
Requiring a second model API key would add cost without improving Erga's deterministic evidence,
tracking, or document-generation responsibilities.

## Decision

Erga treats the MCP client as the reasoning host:

- Erga never selects or calls an LLM.
- Model authentication, subscription entitlements, rate limits, and token accounting belong to
  Codex, Claude Code, OpenCode, Hermes, or another MCP host.
- MCP initialization instructions carry the portable job-link routing policy.
- A least-privilege `career` profile exposes the complete job workflow without mail, Hermes,
  Git-scanning, or token-recording capabilities.
- `erga client configure` generates native project configuration for Codex, Claude Code, and
  OpenCode, previews by default, preserves unrelated settings, and refuses silent replacement.
- `erga onboard` composes private initialization, idempotent project registration, health checks,
  and a concrete first-use verification into one human-readable command.
- Hermes plugins remain optional host-specific enhancements for pre-model routing, messaging
  attachments, and scheduled monitors.

## Consequences

- Users can run Erga with existing coding-agent plans and no additional model API credential.
- The same evidence and local artifact semantics are available across supported clients.
- Client configuration remains explicit and reviewable; Erga does not modify global model or
  provider settings.
- Deterministic pre-model routing cannot be guaranteed on hosts without a hook equivalent to the
  Hermes router. Tool descriptions and MCP server instructions provide the portable fallback.
- Client-specific configuration schemas require compatibility tests and periodic documentation
  review.
