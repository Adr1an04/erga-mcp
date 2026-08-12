# Module ownership map

Use this map to choose the owning module before searching broadly. Filenames sharing a prefix are
one feature family; interfaces compose those families and do not own their rules.

| Family | Owns | Does not own |
| --- | --- | --- |
| `resume_sources`, `resume_template` | source import, template structure, source provenance | role ranking or final proposal policy |
| `resume_tailoring`, `ai_resume_tailoring`, `bullet_quality` | deterministic tailoring, evidence-backed drafting, bullet validation | MCP or Discord interaction |
| `resume`, `cover_letter` | private package artifacts, reviewable diffs, compilation | remote publication |
| `project_inventory`, `project_catalogue`, `project_metrics` | project records, selection inputs, supported metric proposals | Git transport or résumé layout |
| `git_evidence`, `git_project_enrichment`, `git_skills`, `github_projects` | attributable repository evidence and discovery | unsupported impact claims |
| `job_identity`, `job_source`, `job_intake`, `job_research`, `job_discovery`, `job_workspace` | canonical listing identity, job capture, official/secondary research, workspace preparation | application submission |
| `application_lookup` | exact local application selection shared by interfaces | rendering or persistence |
| `store`, `models`, `versioning` | local persistence contracts and records | interface rendering |
| `*_view`, `card_view` | client-neutral presentation models | Discord SDK objects |
| `integrations/` | provider authentication, pagination, and wire translation | classification or status policy |
| `discord_bridge`, `discord_cards`, `discord_setup` | Discord interface parsing, rendering, and process composition | reusable career decisions |
| `discord_settings` | neutral path derivation for optional Discord settings | tokens or bridge lifecycle |
| `cli`, `mcp/`, `mcp_server` | interface parsing, protocol contracts, and composition | reusable career decisions |

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
| `mcp_server.py` | compatibility facade, stdio composition, and cross-family intake orchestration |

New MCP capability families get a dedicated `*_tools.py` module. New reusable decisions go into an
ordinary core module and are injected into the adapter. `http_transport.py` exists only as a public
compatibility import; transport implementation belongs in `mcp/transport.py`.

## Enforced constraints

`tests/test_architecture.py` rejects core-to-interface imports, local import cycles, and broken local
documentation links. Update the map and its focused tests when ownership intentionally changes.
Large legacy modules should be split at stable behavior boundaries, not moved wholesale into a
different directory or wrapped in duplicate abstractions.
