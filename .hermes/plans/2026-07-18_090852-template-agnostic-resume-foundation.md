# Template-Agnostic Résumé Engine Foundation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Establish the user-configurable, template-agnostic configuration and per-job output-package foundation for an open-source LaTeX résumé editor.

**Architecture:** Keep resume preferences in the existing local TOML configuration, separate from private career evidence and output artifacts. Add a deterministic `resume settings` CLI surface and a `resume create-package` command that creates an isolated, collision-safe job workspace with a metadata manifest. The actual template-specific editing adapter is explicitly deferred until the engine has a supplied LaTeX template contract and declared editable regions.

**Tech Stack:** Python 3.11, `argparse`, `tomllib`, `unittest`, local filesystem, existing SQLite-backed pipeline store.

---

## Scope for this increment

- Add generic résumé settings: template path, editable regions, bullet min/target/max length, page limit, output root, and LaTeX command.
- Validate portable constraints (positive character lengths, ordered min/target/max, relative output directory safety, nonempty identifiers).
- Provide CLI commands to inspect settings and create a job-specific output package.
- Package layout: `<output_root>/<cycle>/<application_slug>/` with a metadata manifest and stable subdirectories for `source`, `artifacts`, and `research`.
- Do not read, rewrite, or compile a user template in this increment.
- Do not scrape job links, query public sites, connect Zoho, submit applications, or alter MCP permissions.

## Task 1: Extend configuration model with a generic resume profile

**Files:**
- Modify: `src/recruiting_pipeline/config.py`
- Test: `tests/test_config.py`

1. Write failing tests for default resume settings and for validation of bullet character bounds.
2. Run the targeted test and verify it fails because the resume settings model/parser does not exist.
3. Add a `ResumeSettings` dataclass, `[resume]` defaults in `DEFAULT_CONFIG`, and strict parsing/validation in `load_config`.
4. Re-run the targeted test, then the full suite.

## Task 2: Add settings CLI read/write commands

**Files:**
- Modify: `src/recruiting_pipeline/cli.py`
- Create: `src/recruiting_pipeline/resume_settings.py`
- Test: `tests/test_resume_settings_cli.py`

1. Write a failing CLI test for `resume settings show` and `resume settings set` round-tripping safe, non-secret settings.
2. Run that test and verify failure.
3. Implement atomic TOML text replacement for the initialized config’s `[resume]` table and CLI parsing.
4. Re-run focused and full tests.

## Task 3: Add generic job-output package creation

**Files:**
- Modify: `src/recruiting_pipeline/resume.py`
- Modify: `src/recruiting_pipeline/cli.py`
- Test: `tests/test_resume_package.py`
- Test: `tests/test_resume_settings_cli.py`

1. Write a failing domain test that creates `Fall26/Fall26Palantir/` beneath the configured output root and validates a metadata manifest.
2. Run it and verify failure.
3. Implement safe path-component validation, collision refusal, directories (`source`, `artifacts`, `research`), and JSON metadata only.
4. Write and run a failing CLI integration test for `resume create-package`.
5. Implement the CLI command and prove it passes.

## Task 4: Update product documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started.md`

Document the generic settings, the output-package contract, the explicit non-goal of template mutation in this increment, and the next template-adapter input requirements.

## Task 5: Verify and review

Run:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run python -m unittest discover -v
uv build
git diff --check
```

Review the scoped diff for personal data, secrets, unsafe file paths, and unintended changes. Do not commit, push, or open a PR unless explicitly requested.

## Risks and follow-on decisions

- TOML editing remains intentionally narrow: update only a generated config whose `[resume]` table has known scalar/list fields. A richer config editor would need a TOML writer dependency.
- `template_path` is a configuration value but is not accessed by package creation; template filesystem validation belongs to the future adapter.
- A generic engine cannot safely infer LaTeX insertion points. The next increment needs a documented template contract, likely named markers such as `% recruiting-pipeline:begin experience` / `% recruiting-pipeline:end experience`.
- PDF production requires an installed, user-selected LaTeX command and should be introduced with a real synthetic template fixture before a user’s master template is used.
