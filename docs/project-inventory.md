# Project inventory tailoring

Erga can rank a broad local project catalogue rather than merely reordering the Projects section
already in the master template. With MCP client sampling enabled, it can also synthesize new
role-specific project bullets from approved claims and authenticated authored-Git evidence.

## Enable it

Add these optional settings to local configuration:

```toml
[resume]
project_inventory_path = "projects.json"
project_count = 4
```

`erga setup` infers `project_count` from an explicit style/template résumé. Without one, Erga uses
up to four project slots for its default one-page layout. If the inferred layout has no Projects
section, project selection is disabled rather than adding that section.

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

Erga rejects duplicate IDs, missing/unapproved evidence IDs, non-project LaTeX blocks, and `\\input`, `\\include`, or shell-writing LaTeX commands. When `git_repositories` contains an `owner/repo` mapping, Erga makes an otherwise unlinked project heading clickable using the first repository's canonical `https://github.com/owner/repo` URL. An existing custom `\\href` in the heading takes precedence and is preserved unchanged.

## Selection behavior

- Erga first ranks the full eligible catalogue from project titles, tags, and repository metadata;
  it does not exclude a project because its old résumé wording is weak.
- Terms on explicit required-qualification lines receive a 5× effective weight (base match plus a
  4× requirement bonus), so a required technology outranks incidental responsibility wording.
- When the connected MCP client advertises sampling, Erga researches authenticated authored-Git
  changes for a broader shortlist (up to twice the final project count). The host model then chooses
  the final projects and drafts new bullets from only the bounded project evidence it receives.
- Git scope analysis recognizes implementation and test files while excluding generated output,
  dependencies, lockfiles, snapshots, documentation, data, and media. Language/test signals may
  help rank projects, but raw commit, file, and line totals remain non-publishable supporting facts.
- Every model-authored bullet must cite evidence IDs for that same project. Server-side validation
  rejects invented numbers, cross-project evidence, raw commit/file/line accounting, duplicate
  lead verbs, unsafe LaTeX, hard character overflow, and rendered line wrapping.
- The configured minimum character count is a soft preference. Underflow is recorded for review;
  it never aborts an otherwise complete bullet or intake. The maximum and one-line layout remain
  hard constraints, and Erga retries model copy with progressively tighter caps when necessary.
- Once the model chooses a valid project set, layout and copy retries lock those project IDs. A
  wrapped bullet must be rewritten for the same project instead of silently swapping in a less
  relevant catalogue entry.
- When client sampling is unavailable, the deterministic fallback selects and copies approved
  inventory blocks. If sampling was available but model validation cannot be repaired, Erga keeps
  the master résumé projects rather than lowering quality with an unrelated substitution.
- The package claim report records the inventory mode, candidate count, selected IDs/titles, and a `project_claims` entry for every selected project bullet with its approved evidence IDs.
- If no inventory project matches in deterministic fallback mode, the report uses
  `inventory_no_match` and leaves the template Projects section unchanged—there is no
  stale-template reordering.
- If a new inventory bullet violates configured constraints, the proposal is reverted and the report uses `inventory_constraint_fallback` with no selected project or project-claim records.

The final proposal is measured again before publication. Any wrapped non-project bullet, or a wrapped project bullet that cannot be replaced from approved inventory, stops publication with a precise validation error. The existing LaTeX compile/page-limit validation still applies. A rejected compile does not alter the master résumé.
