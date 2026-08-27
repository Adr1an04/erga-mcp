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
bullet_max_lines = 2
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

`bullet_max_lines` controls the rendered PDF, not an approximate character count. Set it to `1`
for strict one-line bullets, `2` to allow up to two lines, or `0` for no hard line limit. The legacy
`single_line_bullets = true` setting remains supported and maps to a one-line maximum. In strict
one-line mode, validation also counts physical TeX paragraph lines, catching invisible extra lines
that some Jake-style macros create after nearly full text.
