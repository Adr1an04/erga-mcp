# Native Discord

Erga can connect a private Discord bot directly to Codex, Claude Code, OpenCode, Gemini CLI,
Cursor Agent, GitHub Copilot CLI, or an advanced custom headless CLI. The bridge starts one
noninteractive turn in the selected coding CLI for each accepted Discord message, so maintained
presets use the same local login and model access as the coding tool. Hermes, a separate LLM API
key, and another reasoning service are not required.

## One-time Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an
   application.
2. Open **Bot**, create the bot user, and copy its token. Treat this token like a password.
3. Enable **Message Content Intent** on the Bot page.
4. Under **Installation**, enable the `bot` scope and grant only **View Channels**, **Send
   Messages**, **Read Message History**, and **Attach Files**.
5. Install the bot into a private server you control.
6. Note your current unique Discord username, such as `student.dev`. Do not include a leading `@`.

Run the guided setup:

```bash
uv run erga setup
```

Choose **Full Erga**, select the coding tool whose subscription is already signed in, and paste the
bot token and your Discord username when prompted. The hidden numeric account ID is also accepted
for users who prefer a stable identifier, but Developer Mode is not required.

Discord treats bot tokens as credentials and recommends requesting only the permissions an app
needs. Message Content Intent is required for ordinary message content; direct messages and
messages that mention the bot are exceptions, but Erga enables the intent for predictable private
use.

## Use the bot

- Send the bot a direct message; or
- mention it in an authorized server channel: `@Erga show my pipeline status`.

In servers, Erga ignores messages that do not mention it. It ignores every account not included in
the local username-or-ID allowlist. Bot-authored messages are ignored. Accepted turns are queued
one at a time so two Discord messages cannot mutate the résumé workspace concurrently.

Useful lifecycle commands:

```bash
uv run erga discord status
uv run erga discord start
uv run erga discord stop
uv run erga discord run
```

`run` keeps the bridge in the foreground for troubleshooting. `start` runs it in the background
and reports the log path.

## Credentials and billing boundary

- The Discord token is stored through the operating system's credential store, never in
  `config.toml`, `discord-bridge.json`, command arguments, or the repository.
- Erga verifies a maintained coding CLI's existing login with one minimal live readiness turn
  before enabling the bridge.
- Codex child processes do not inherit `OPENAI_API_KEY`.
- Claude Code child processes do not inherit `ANTHROPIC_API_KEY`.
- Erga itself never chooses a model, calls a model API, or receives the coding tool's account
  credential.

OpenCode provider authentication remains managed by OpenCode. Advanced custom adapters cannot
prove which provider or billing route an unknown CLI uses; review that client before enabling the
bridge.

## Security boundary

An authorized Discord account can ask the coding agent to read and modify files that the selected
CLI can access inside the configured workspace. Treat the bot as remote access to that bounded
coding-agent session:

- use a private bot that is not publicly installable;
- allowlist only your own Discord username or stable account ID unless another person is
  intentionally trusted;
- configure the narrowest workspace possible;
- do not run the bot as an administrator or from a directory containing unrelated secrets; and
- stop the bridge when it is not needed.

Erga's agent prompt prohibits job submission, invented résumé claims, and employer messaging.
Those policy instructions reduce mistakes but are not an operating-system sandbox.
