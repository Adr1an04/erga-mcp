# Module ownership map

Choose the owning package before searching broadly. Interfaces compose domain packages and do not
own their rules.

| Package | Owns | Does not own |
| --- | --- | --- |
| `applications/` | job identity, source capture, discovery, intake, research, lookup, workspace preparation | submission or résumé generation |
| `resumes/` | sources, template preservation, tailoring, planning, bullet quality, artifacts, cover letters | repository discovery or protocol interaction |
| `portfolio/` | project catalogue, ranking inputs, Git/GitHub evidence, skill evidence, metric proposals | unsupported impact claims or résumé layout |
| `tracking/` | statuses, contacts, reporting, projections, client-neutral tracker/settings/onboarding/cards | Discord or MCP SDK objects |
| `operations/` | setup, diagnostics, export, private files, TOML editing, uninstall | provider wire protocols or domain rules |
| `integrations/` | Discord, mail, Obsidian, Hermes, Keryx, host, bounded HTTP, search-provider, and web parsing adapters | career decisions or canonical persistence |
| `mcp/` | contracts, profiles, registration, tool families, transport, server composition | reusable career decisions |
| package root | CLI, configuration, shared models/store, versioning | feature-specific modules |

## MCP package

| Module | Responsibility |
| --- | --- |
| `mcp/contracts.py` | Pydantic wire contracts |
| `mcp/profiles.py` | tool capabilities, annotations, and private-data visibility |
| `mcp/registry.py` | strict profile-aware SDK registration |
| `mcp/sampling.py` | MCP sampling translation for client-neutral tailoring requests |
| `mcp/transport.py` | authenticated Streamable HTTP composition |
| `mcp/package_manifest.py` | persisted package-to-wire validation translation |
| `mcp/read_tools.py` | read/dashboard tool family |
| `mcp/workspace_tools.py` | project, Git, evidence, mail, and usage tool family |
| `mcp/server.py` | stdio composition and cross-family intake orchestration |

## Tailoring decision modules

| Module | Responsibility |
| --- | --- |
| `applications/role_profile.py` | source-backed required/preferred requirement model, conservative aliases, and explainable matching |
| `resumes/claims.py` | claim-sized approved-evidence index, exact supported-skill projection, and manual claim/evidence validation |
| `resumes/bullet_graph.py` | typed evidence nodes, connected bullet assembly paths, and source-path alignment |
| `resumes/bullet_editor.py` | deterministic bullet/resume structure parsing, editorial issue codes, and repair guidance |
| `resumes/outcomes.py` | local directional reporting from explicitly used résumé versions and later application statuses |
| `resumes/planning.py` | project/copy choices plus supported, partial, and unsupported requirement review |
| `resumes/tailoring.py` | evidence-backed ranking, cross-role bullet budgeting, layout packing, and proposal decision artifacts |

New MCP capability families get a dedicated `*_tools.py` module. New reusable decisions go into
the owning domain package and are injected into the adapter.

## Enforced constraints

`tests/test_architecture.py` rejects root-level feature modules, undocumented top-level packages,
compatibility re-exports, stale local import targets, core-to-interface imports, local import
cycles, and broken local documentation links. Update the map and its focused tests when ownership
intentionally changes.
