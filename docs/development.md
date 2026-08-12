# Development and testing

## Set up

```bash
uv sync --extra dev --extra discord
```

Use synthetic fixtures only. Real resumes, applications, mail, credentials, tokens, contacts,
databases, exports, and vault data never belong in the repository or test logs.

## Find the change

| Change | Start in | Focused tests |
| --- | --- | --- |
| MCP contract, profile, or transport | `src/erga_mcp/mcp/` | `test_mcp_*`, `test_http_transport.py` |
| Resume extraction/layout/tailoring | `src/erga_mcp/resumes/` | `test_resume*`, `test_bullet_quality.py` |
| Project/Git evidence | `src/erga_mcp/portfolio/` | `test_project*`, `test_git*` |
| Application/research workflow | `src/erga_mcp/applications/` | `test_application_lookup.py`, `test_job*`, `test_research_navigator.py` |
| Tracking and contact workflow | `src/erga_mcp/tracking/` | `test_tracker*`, `test_contact*`, `test_mail_status*` |
| CLI behavior | `src/erga_mcp/cli.py` and the owning package | `test_cli*` plus the owning module test |
| Hermes/Discord | `src/erga_mcp/integrations/` | `test_hermes*`, `test_discord*` |
| Setup/diagnostics/export | `src/erga_mcp/operations/` | `test_setup*`, `test_doctor.py`, `test_exporting.py` |
| Packaging/workflows/docs | `pyproject.toml`, `.github/`, `docs/` | `test_packaging.py`, build and metadata checks |

## Canonical full gate

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run coverage run -m unittest discover -s tests
uv run coverage report
uv run python -m unittest tests.test_mcp_stdio tests.test_mcp_interoperability -v
uv export --frozen --no-dev --no-emit-project --format requirements-txt | uv run pip-audit -r /dev/stdin
uv build --no-build-isolation
uv run twine check dist/*
git diff --check
```

The `-s tests` argument is required for repository-root unittest discovery. Coverage is branch-aware
and must remain at or above the configured ratchet. CI additionally runs the suite on Linux, macOS,
and Windows across supported Python versions and installs the built wheel into a clean environment.

## MCP change checklist

- Keep `MCPServer` registration/composition thin; move reusable work into ordinary modules.
- Put wire models in `mcp/contracts.py`, capability/profile rules in `mcp/profiles.py`, and tools in
  a cohesive family module.
- Set annotations to the real side effect: local read, network read, local write, or local exec.
- Add the tool to only the profiles that require it.
- Use bounded, explicit paths/URLs; never discover a home directory implicitly.
- Verify schema discovery and at least one real official-SDK stdio or HTTP call.
- Keep stdio free of non-protocol stdout output.

## Dependency and release policy

- `uv.lock` is authoritative for repository checks and CI.
- Runtime security floors in `pyproject.toml` prevent known-vulnerable compatible installs.
- CI audits the frozen runtime graph; Dependabot proposes grouped Python and Actions updates.
- GitHub Actions use immutable commit SHAs with version comments.
- Release verification/build is read-only. A separate minimal job receives `contents: write` only
  to attach the already-built artifact; PyPI uses trusted publishing.
