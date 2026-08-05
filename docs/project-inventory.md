# Project inventory tailoring

Erga can select complete, existing project blocks from a local approved inventory rather than merely reordering the Projects section already in the master template.

## Enable it

Add these optional settings to local configuration:

```toml
[resume]
project_inventory_path = "projects.json"
project_count = 4
```

When `project_inventory_path` is empty or absent, Erga keeps the legacy reorder-only behavior.

## Inventory format

The configured file is a local JSON array. Each project needs a stable lowercase ID, the exact LaTeX block that may be copied into a résumé, role-fit tags, and approved evidence. `bullet_evidence_ids` is required: it contains one non-empty approved-evidence ID list for each `\\resumeItem` in source order. Its union must exactly match `evidence_ids`.

```json
[
  {
    "id": "service-platform",
    "title": "Service Platform",
    "latex": "\\resumeProjectHeading{\\textbf{Service Platform} $|$ \\textit{Python, Kubernetes}}{}\n\\resumeItemListStart\n\\resumeItem{Built Python APIs deployed with Kubernetes for backend services.}\n\\resumeItemListEnd\n",
    "evidence_ids": ["ev_service_platform"],
    "bullet_evidence_ids": [["ev_service_platform"]],
    "tags": ["python", "kubernetes", "backend", "infrastructure"]
  }
]
```

Erga rejects duplicate IDs, missing/unapproved evidence IDs, non-project LaTeX blocks, and `\\input`, `\\include`, or shell-writing LaTeX commands.

## Selection behavior

- Only project blocks whose own text or tags match the job description can be selected.
- Terms on explicit required-qualification lines receive a 5× effective weight (base match plus a 4× requirement bonus), so a required technology outranks incidental responsibility wording.
- Ties are deterministic by project ID.
- Selected blocks are copied verbatim; Erga never invents or rewrites a claim.
- The package claim report records the inventory mode, candidate count, selected IDs/titles, and a `project_claims` entry for every selected project bullet with its approved evidence IDs.
- If no arsenal project matches, the report uses `inventory_no_match` and leaves the template Projects section unchanged—there is no stale-template reordering.
- If a new inventory bullet violates configured constraints, the proposal is reverted and the report uses `inventory_constraint_fallback` with no selected project or project-claim records.

The existing LaTeX compile/page-limit validation still applies. A rejected compile does not alter the master résumé.
