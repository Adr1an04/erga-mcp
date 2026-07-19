# Contributing

Erga is early and small. Issues, bug fixes, and focused improvements are welcome.

## Set up the project

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync --extra mcp --extra dev
```

## Before opening a pull request

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run python -m unittest discover -s tests -v
uv build
git diff --check
```

Please add a test when fixing a bug or adding behavior. Keep pull requests focused and explain what
changed.

Use fake data in tests and examples. Do not commit real résumés, application details, emails,
credentials, tokens, databases, or exports.

Erga should continue to organize applications and prepare résumé files without submitting
applications or sending messages for the user.
