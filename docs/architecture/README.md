# Architecture

Erga is a local application with optional interfaces. The deterministic CLI and domain modules are
the product; MCP and Hermes are adapters, not the system of record.

```text
CLI / MCP / Discord / Hermes
            │
            ▼
 application workflows (intake, tailoring, research, tracking)
            │
            ▼
 domain rules (evidence, status, project selection, validation)
            │
            ▼
 adapters (SQLite, Git, HTTP, mail, Obsidian, LaTeX)
```

## Dependency rules

1. Domain and application modules must not import MCP, Discord, Hermes, or CLI modules.
2. Interfaces may translate inputs and register commands/tools, but reusable business decisions
   belong in ordinary Python functions outside the interface.
3. Optional integrations may depend on the core; the core must not require an optional host.
4. Untrusted inputs never grant authority. Network fetches, local writes, execution, and private
   reads remain visibly distinct capabilities.
5. Every résumé claim traces to approved evidence. Git accounting can establish authorship and
   implementation scope, but cannot invent impact, adoption, performance, or coverage.

## Current module map

For file-by-file ownership and search routing, see the [module ownership map](module-map.md).

| Area | Primary modules |
| --- | --- |
| Shared configuration and state | `config.py`, `models.py`, `store.py`, `versioning.py` |
| Applications and research | `applications/` |
| Resume knowledge and output | `resumes/` |
| Project and Git evidence | `portfolio/` |
| Tracking and presentation models | `tracking/` |
| Local lifecycle operations | `operations/` |
| Optional hosts and providers | `integrations/` |
| CLI interface | `cli.py` |
| MCP interface | `mcp/`, with `mcp/server.py` as the composition root |

The package root is intentionally limited to six stable foundation modules. New feature modules go
in the owning package, and `tests/test_architecture.py` enforces that boundary.

## MCP organization

MCP does not prescribe a repository directory tree. Erga follows the official SDK's thin-server
direction and separates protocol concerns by responsibility:

```text
src/erga_mcp/mcp/
├── contracts.py      Pydantic wire contracts
├── profiles.py       capability annotations and tool-profile policy
├── read_tools.py     local read/dashboard tool family
├── sampling.py       MCP sampling translation
├── workspace_tools.py project, Git, evidence, mail, and usage tool family
├── server.py         stdio composition and cross-family intake orchestration
├── transport.py      authenticated Streamable HTTP composition
├── package_manifest.py persisted-package wire translation
└── registry.py       profile-aware registration
```

`applications/identity.py` owns canonical listing/package identity for every interface.
`mcp/server.py` is the composition root, not a home for reusable product rules. New unrelated tools
must go in a cohesive tool-family module and be registered from `build_server`. Tool functions
should validate bounded inputs, declare accurate annotations, and delegate reusable work to the
owning domain package.

Stdio is the default transport. Streamable HTTP is loopback-only, rejects browser origins, validates
Host headers, and requires a per-launch bearer token. It is not a remote or multi-user deployment
mode. MCP 2026-07-28 model-assisted tailoring uses the protocol's multi-round
`InputRequiredResult` flow; the legacy server-initiated sampling backchannel is retained only for
older negotiated protocol versions.

## State and side effects

- SQLite and generated artifacts are local system-of-record data.
- Obsidian is an optional projection.
- Mail adapters read bounded metadata and do not mutate mail.
- Resume changes are reviewable local diffs; remote publication is outside the tool surface.
- Job submission and outbound messages require a separate explicit user action and are not MCP
  tools.

## Decision records

- [ADR-001: local core and evidence ledger](ADR-001-local-core-and-evidence-ledger.md)
- [ADR-002: client-neutral reasoning hosts](ADR-002-client-neutral-reasoning-hosts.md)
- [ADR-003: core/Obsidian onboarding](ADR-003-core-obsidian-onboarding.md)
- [ADR-004: optional MCP host connections](ADR-004-optional-mcp-host-connections.md)
- [ADR-005: optional Discord bridge](ADR-005-optional-discord-bridge.md)
- [ADR-006: optional Keryx discovery](ADR-006-optional-keryx-discovery.md)
