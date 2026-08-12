# Contributing

Erga is early and small. Issues, bug fixes, and focused improvements are welcome.

Start with the [documentation index](docs/README.md), [architecture](docs/architecture/README.md),
and [development/test map](docs/development.md). `AGENTS.md` contains the same repository navigation
and safety boundaries for coding agents.

## Set up the project

```bash
git clone https://github.com/Adr1an04/erga-mcp.git
cd erga-mcp
uv sync --extra dev
```

## Before opening a pull request

Run the single canonical verification gate in
[`docs/development.md`](docs/development.md#canonical-full-gate). That page owns the commands so
local instructions and CI do not drift independently.

Please add a test when fixing a bug or adding behavior. Keep pull requests focused and explain what
changed.

Use fake data in tests and examples. Do not commit real résumés, application details, emails,
credentials, tokens, databases, or exports.

Erga should continue to organize applications and prepare résumé files without submitting
applications or sending messages for the user.
