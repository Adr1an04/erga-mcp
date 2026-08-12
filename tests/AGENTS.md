# Test instructions

Use synthetic data and temporary directories only. Never read a developer's default Erga config,
resume, home-directory portfolio, mail account, vault, keyring, or live application packages.

Tests that need network behavior must use local deterministic fixtures or mocked transports. Real
MCP interoperability tests may spawn the local server, but must use a synthetic config, authenticated
ephemeral loopback endpoint, bounded timeout, and closed process pipes. Keep cross-platform behavior
valid for Linux, macOS, Windows, and Python 3.11–3.14.
