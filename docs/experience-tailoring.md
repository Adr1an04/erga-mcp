# Experience tailoring

Erga can select from multiple approved bullets for each job while keeping the source résumé's
heading, dates, company, location, links, spacing, and LaTeX macros unchanged. Setup creates a
private experience inventory from the master résumé. Tailoring is controlled independently from
project selection.

```toml
[resume]
experience_tailoring = true
experience_inventory_path = "experience-inventory.json"
experience_min_bullets = 2
experience_max_bullets = 4
```

The normal commands do not require editing JSON:

```console
erga resume experience list
erga resume experience add --role "Software Engineer" --company "Example" \
  --bullet "Reduced deployment failures by 40% across 12 services." \
  --tag reliability --tag deployment
erga resume settings set --experience-tailoring
```

Adding a bullet is an explicit user confirmation. Erga stores the claim as approved local evidence,
records every numeric value as `user_confirmed`, and bolds those metrics in the LaTeX bullet. Git
evidence may corroborate implementation/test scope (for example, a test inventory); it cannot be
used to claim adoption, business impact, performance, revenue, latency, or user counts.

During tailoring, Erga matches inventory roles to existing experience headings, ranks distinct
bullets against the job, applies the configured per-role floor/cap, and replaces only `resumeItem`
bodies. If a role cannot meet the floor with distinct approved evidence, its master entry is kept
unchanged.

When `single_line_bullets = true`, validation counts physical TeX paragraph lines as well as visible
PDF text lines. This rejects the invisible extra line that some Jake-style macros create when a
nearly full line is followed by `\vspace` or other template glue.
