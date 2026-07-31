# ADR-005: Keep Discord outside the core and require an explicit bridge backend

## Status

Accepted

## Context

Discord is useful when a user wants to reach Erga away from a terminal, but it introduces three
independent requirements: a Discord bot, a long-running process, and a coding CLI capable of
headless reasoning. Making any of those requirements part of first-run setup would contradict the
provider-neutral local core established in ADR-002 and ADR-003.

At the same time, an unattended Discord message cannot borrow an interactive coding session. The
bridge therefore needs an execution backend even though Erga itself does not.

## Decision

Ship Discord as an optional package extra and a separate `erga discord` command group.

Core setup completes before offering Discord and defaults to skipping it. If selected, the user
chooses one replaceable bridge backend from documented presets or supplies an advanced executable
and argument array. That selection applies only to Discord; it neither restricts other MCP hosts
nor becomes Erga's system of record.

The bridge:

- resolves the optional backend before collecting Discord credentials;
- makes its one-message login readiness probe explicit;
- removes common provider API-key variables for preset subscription-backed processes;
- stores the bot token only in the operating-system credential store;
- accepts current Discord usernames and stable numeric IDs;
- defaults to direct messages or explicit server mentions;
- invokes every backend as an argument array without a shell;
- bounds and serializes message processing; and
- validates a random process nonce before stopping a recorded background process.

## Consequences

Users who do not want Discord install no Discord runtime and provide no Discord or coding-host
credential. Bridge failures cannot invalidate the local database, résumé knowledge, CLI, MCP
server, or any independently configured Obsidian projection.

Users who enable Discord must maintain one authenticated headless coding CLI and explicitly accept
its local execution behavior. The custom adapter provides control for clients outside the preset
list, but places responsibility for the reviewed argument contract on that user.

Discord configuration can evolve, be replaced, or be removed independently. Any future remote
delivery channel should follow the same optional-adapter boundary rather than entering core setup.
