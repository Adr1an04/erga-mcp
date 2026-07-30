# ADR-003: Native Discord Subscription Bridge

## Status

Accepted.

## Context

Many computer-science students already have Discord and a subsidized or paid coding-agent
subscription. Requiring Hermes, a second AI runtime, or a metered model API key makes Erga harder
to adopt and duplicates capabilities the user's coding tool already provides. A fixed list of
three coding clients creates the same adoption problem at a smaller scale.

Discord still needs a long-running Gateway client, while the maintained coding clients expose
noninteractive CLI modes that can reuse their existing local authentication.

## Decision

Erga owns a small native Discord Gateway bridge:

- `erga setup` uses an arrow-key wizard to configure the coding client, résumé workflow, and
  optional Discord connection.
- A declarative adapter registry defines executable discovery, native project MCP format, login
  checks, headless readiness probes, Discord invocation, output capture, and API-key isolation.
- Maintained adapters cover Codex, Claude Code, OpenCode, Gemini CLI, Cursor Agent, and GitHub
  Copilot CLI.
- An advanced generic adapter accepts a shell-free argument array and writes portable `.mcp.json`,
  while explicitly declining to guarantee unknown-client discovery or billing behavior.
- The bridge invokes the selected coding CLI directly for each accepted message.
- The bot token is stored in the operating-system credential store.
- Non-secret settings contain an explicit project directory and Discord identity allowlist.
- Server messages require a bot mention by default.
- The allowlist accepts current unique Discord usernames or stable numeric account IDs; retired
  `name#1234` discriminators are rejected.
- Known ambient model API keys are removed from the corresponding maintained client process.
- The existing Hermes integration remains compatibility code for existing Hermes users; it is not
  part of the default onboarding path.

## Consequences

- A student can use Erga from Discord without purchasing another model API or installing another
  reasoning agent.
- Discord setup still requires creating a bot because Discord does not allow self-bots.
- The long-running bridge must remain active on the user's machine.
- An allowlisted Discord identity can drive a write-capable coding-agent session inside the
  configured workspace, so the bot must be private and the workspace narrowly scoped.
- CLI invocation contracts and subscription-authentication checks require compatibility tests as
  coding tools evolve.
