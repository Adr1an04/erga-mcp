# Zoho OAuth Connection Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a local browser-based Zoho OAuth connection command that uses a loopback callback, PKCE, minimal read-only scopes, and macOS Keychain token storage.

**Architecture:** The repository stores only non-secret OAuth client metadata in local TOML. Refresh/access tokens and the user-provided client secret remain in macOS Keychain. `zoho connect` validates settings, opens the Zoho consent URL, validates state on a localhost callback, exchanges the code, and persists only the token response in Keychain.

**Tech Stack:** Python stdlib (`http.server`, `urllib`, `secrets`, `hashlib`, `webbrowser`, `subprocess`), macOS `security`, `unittest`.

---

1. Extend local config with a generic `[zoho]` non-secret client profile (client ID, accounts domain, configured folder); validate HTTPS domain and narrow scopes.
2. Add failing tests for PKCE authorization URL construction and state validation.
3. Implement `zoho_oauth.py`: PKCE verifier/challenge, authorization URL, loopback callback receiver, token exchange with timeouts, and Keychain storage adapter.
4. Add `zoho configure` and `zoho connect` CLI commands. `connect` must not accept tokens or secrets as CLI flags.
5. Add docs explaining the one-time Zoho API Console web-app registration, localhost redirect URL, and manual Keychain client-secret entry; do not instruct users to send credentials in chat.
6. Verify using mocked HTTP/browser/keychain tests, full lint/type/test/build checks, and an independent security review.
