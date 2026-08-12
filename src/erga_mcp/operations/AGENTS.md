# Local operation instructions

This package owns setup, diagnostics, export, private-file handling, TOML editing, and uninstall
workflows.

- Destructive actions must have explicit, validated targets and remain bounded to Erga state.
- Never expose credentials or private paths in diagnostics and exports.
- Keep product rules in their domain packages; operations orchestrate them without duplicating them.
- Provider-specific setup belongs in `integrations/`.
