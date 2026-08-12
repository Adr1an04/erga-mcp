# ADR-006: Optional Keryx Public Job Discovery

## Status

Proposed

## Context

Erga can intake and track a job once a user supplies a posting URL, but discovering newly published
internships is a separate concern. Keryx is a standalone, self-updating GitHub repository that
aggregates public United States internship and new-graduate listings. Integrating it can make Erga's
career workflow more useful, but treating Keryx as core infrastructure, an automatic application
source, or a channel for private profile data would violate Erga's local-first and explicit-action
boundaries.

Agent-triggered remote searches would also disclose user queries and make an optional public source
look like an always-on capability. Automatically converting listings into application records would
blur the distinction between discovery and a user-approved workflow action.

## Decision

Add Keryx as a default-off optional integration after Erga's private core is complete:

- `erga keryx enable` explicitly downloads and validates one fixed public index URL, caches the
  normalized public records locally, and only then records the opt-in;
- `erga keryx sync` is the only refresh operation and remains an explicit CLI action;
- CLI and MCP searches read only the local cache, so queries and private Erga state never leave the
  machine;
- the read-only MCP search tool appears in the existing local-read and career profiles, but reports
  that setup is required while the integration is disabled;
- search output preserves Keryx's public provenance and academic-eligibility metadata while treating
  all listing content as untrusted data, including the separate `required`, `preferred`, and
  `stated` qualification modalities rather than converting preferences into eligibility gates;
- no result becomes an application, tracker row, evidence record, résumé change, message, or remote
  action; normal `intake_job_url` remains a separate explicit step for one selected URL; and
- disabling the integration blocks use without destructively deleting the public cache.

The adapter accepts the current Keryx schema and the immediately preceding public schema so Erga is
not coupled to a same-minute deployment. It independently validates links and bounded fields rather
than trusting another repository's implementation details.

## Consequences

Erga gains useful public job discovery without adopting Keryx as its system of record or requiring
it during onboarding. A cache refresh is manual in this first integration; scheduled alerts or
notifications require a later, separately reviewed adapter with explicit delivery authorization.

The fixed source URL is a deliberate trust anchor. Supporting mirrors or user-supplied feeds later
requires a separate source-approval and network-security design rather than a generic URL setting.
