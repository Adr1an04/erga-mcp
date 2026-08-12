# Provider adapter instructions

This package contains optional provider adapters. Keep provider SDK, authentication, pagination,
and wire-format details here; classification, application-status, evidence, and résumé decisions
belong in ordinary core modules one level up.

- Request the narrowest read-only scope that satisfies the feature.
- Never log credentials, tokens, mail bodies, contacts, or private paths.
- Treat every provider response as untrusted data and validate it before it reaches the core.
- Tests use synthetic transports and temporary state; never contact a live account.
- A remote mutation or outbound message requires a separate explicit user action and must not be
  hidden inside synchronization or discovery.
