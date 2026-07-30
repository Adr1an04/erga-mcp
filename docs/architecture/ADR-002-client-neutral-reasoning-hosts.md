# ADR-002: Client-Neutral Reasoning Hosts

## Status

Proposed.

## Context

Erga's product core is the local evidence ledger, Obsidian-backed career workspace, application
state, résumé workflow, and MCP server. Those capabilities should remain useful without requiring
one particular AI product or orchestration runtime.

The original automatic job-link path was optimized for Hermes, and the complete job workflow was
available only through the broad legacy `default` tool profile. That made a host integration look
like a product requirement and forced other MCP hosts to accept unrelated mail, monitor,
Git-research, and token-recording tools.

## Decision

Treat the connected MCP client as an optional reasoning host around a complete local Erga system:

- Erga never selects or calls an LLM.
- Erga never asks for or stores a model API credential.
- Model authentication, entitlements, limits, and token accounting belong to whichever MCP host
  the user chooses.
- A least-privilege `career` profile exposes the bounded job-intake and document workflow without
  bulk private-data export, full writing-sample/template context, mail, Hermes monitors, Git
  scanning, or token recording.
- The `career-private` profile is an explicit opt-in extension for hosts that may receive the full
  writing-style source material or package the user's private career archive.
- MCP initialization instructions carry portable job-link routing policy.
- No client brand is part of the core compatibility contract. Host-specific configuration,
  readiness checks, messaging channels, and convenience automation remain optional integrations.

## Consequences

- Users may operate Erga through its CLI alone or connect any compatible MCP host.
- Hermes remains a supported optional integration rather than a required runtime.
- Every host receives the same evidence and local-artifact semantics.
- Deterministic pre-model routing cannot be guaranteed on hosts without a hook equivalent to the
  Hermes router; MCP instructions and tool descriptions provide the portable fallback.
- Optional host adapters may be added without changing Erga's evidence, Obsidian, résumé, or
  application model.
