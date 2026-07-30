# ADR-003: Career-First Onboarding and Optional Projections

- **Status:** Proposed
- **Date:** 2026-07-29

## Context

Erga's private SQLite state, evidence ledger, résumé sources, and application workflow are its
durable local system of record. First-run setup previously risked presenting Obsidian, a particular
coding assistant, or a chat bridge as a prerequisite. Those products are projections and interfaces
around the career system, so requiring any one of them reduces user control and lets an unrelated
installation or login failure block Erga itself.

New users also commonly have a PDF or DOCX résumé rather than a LaTeX source file. Asking them to
manually distinguish factual evidence, layout preferences, tracker paths, and MCP configuration
creates avoidable setup friction.

## Decision

`erga setup` configures the provider-independent local core:

1. initialize Erga's private configuration, SQLite state, and local application tracking;
2. import every page of a user-selected PDF, DOCX, or `.tex` master résumé into private,
   content-addressed storage as approved factual evidence;
3. optionally snapshot a confidently selected style résumé while exposing only non-factual layout
   metadata;
4. configure local résumé output and the client-neutral `career` MCP profile;
5. optionally create or adopt an Obsidian vault as a human-readable workspace and tracker view.

The wizard never requires a coding-assistant executable, subscription login, Discord bot, Hermes
installation, or model API key. Coding hosts, communication bridges, and mail providers are
separate optional connections that may be added, combined, changed, or removed without rebuilding
the core.

Setup is repeatable. It preserves unrelated configuration, reuses content-addressed résumé
snapshots and evidence records, and does not overwrite an existing Obsidian start note when that
projection is enabled.

## Rationale

The career workflow should be useful immediately after setup: truthful résumé knowledge, private
application state, and local status transitions do not require a note-taking application. Obsidian
remains valuable as a user-controlled, human-readable projection, but Erga's normalized state must
remain authoritative and complete without it.

Separating core setup from external connections also isolates failure domains: a missing CLI,
expired subscription, unavailable credential store, or invalid Discord token cannot prevent Erga
from preserving résumé knowledge and application state.

## Consequences

- `questionary` is a core CLI dependency so first-run setup can provide arrow-key choices and
  drag-and-drop path input.
- Obsidian is offered as an explicit optional setup choice and defaults to disabled.
- When selected, Obsidian must be opened separately; Erga does not install or launch desktop
  applications.
- The low-level `erga init` and individual configuration commands remain available for scripts and
  advanced users.
- Optional connection commands can evolve independently and support zero, one, or multiple hosts.
