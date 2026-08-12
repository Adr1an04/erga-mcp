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
| Configuration and private state | `config.py`, `private_files.py`, `store.py` |
| Applications and research | `job_identity.py`, `job_intake.py`, `job_research.py`, `job_discovery.py`, `job_workspace.py` |
| Resume knowledge and output | `resume_sources.py`, `resume_template.py`, `resume_tailoring.py`, `resume.py` |
| Project/Git evidence | `project_inventory.py`, `project_metrics.py`, `git_evidence.py`, `git_project_enrichment.py` |
| Optional adapters | `integrations/`, `discord_*.py`, `keryx.py`, `web_scraping.py` |
| CLI interface | `cli.py` |
| MCP interface | `mcp/`, with `mcp_server.py` as the compatibility/composition facade |

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
├── transport.py      authenticated Streamable HTTP composition
├── package_manifest.py persisted-package wire translation
└── registry.py       profile-aware registration
```

`job_identity.py` owns canonical listing/package identity for every interface. `mcp_server.py`
remains a compatibility facade while the intake orchestration is split gradually.
New unrelated tools must not be added directly to that facade: add or extend a cohesive tool-family
module and register it from `build_server`. Tool functions should validate bounded inputs, declare
accurate annotations, and delegate reusable work to the core.

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
