# Provider adapter instructions

This package contains optional host and provider adapters. Keep SDK, authentication, pagination,
and wire-format details here; classification, application-status, evidence, and résumé decisions
belong in the owning domain package.

- `discord/` owns bridge lifecycle, interaction rendering, settings, and setup.
- `mail/` owns Gmail and Zoho transport, OAuth, provider contracts, and mail settings.
- `obsidian/` owns vault import and tracker projection.
- `http.py` owns pinned, bounded public HTTP transport; `web.py` owns provider search and parsing.
- `hermes.py`, `hosts.py`, and `keryx.py` own their bounded external-system adapters.

- Request the narrowest read-only scope that satisfies the feature.
- Never log credentials, tokens, mail bodies, contacts, or private paths.
- Treat every provider response as untrusted data and validate it before it reaches the core.
- Tests use synthetic transports and temporary state; never contact a live account.
- A remote mutation or outbound message requires a separate explicit user action and must not be
  hidden inside synchronization or discovery.
