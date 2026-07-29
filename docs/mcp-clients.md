# Connect Erga to Codex, Claude Code, OpenCode, and other MCP clients

Erga's primary integration contract is a local stdio process. The MCP client supplies all AI
reasoning; Erga itself never calls a model API. This lets the same local workflow use the model
access already available in Codex, Claude Code, OpenCode, or another MCP host.

> [!IMPORTANT]
> Review the command and absolute paths before enabling it. The server runs with the same local permissions as the client that launches it. Do not put credentials, OAuth tokens, résumé contents, or vault contents in a client configuration.

## Shared stdio contract

Replace both placeholders with absolute local paths:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/erga-mcp", "run", "erga-mcp"],
  "env": {
    "ERGA_MCP_CONFIG": "/absolute/path/to/erga-mcp-config.toml"
  }
}
```

The `mcp` Python runtime dependency is included with the `erga-mcp` package, along with `uvicorn`
for the opt-in HTTP transport. For a source checkout, run `uv sync` once before connecting a
client.

## Generated project configuration

Use one command to preview the correct native configuration:

```bash
uv run erga client configure codex --config /absolute/path/to/erga-config.toml
uv run erga client configure claude-code --config /absolute/path/to/erga-config.toml
uv run erga client configure opencode --config /absolute/path/to/erga-config.toml
```

Add `--project-dir /absolute/path/to/project` to select another project. Preview is the default.
Add `--write` only after reviewing the returned target path and content. The writer preserves
unrelated settings and refuses to replace an existing `erga-mcp` server entry.

Generated files:

| Client | Project configuration |
| --- | --- |
| Codex | `.codex/config.toml` |
| Claude Code | `.mcp.json` |
| OpenCode | `opencode.json` |

Every generated entry passes only two non-secret environment values:

- `ERGA_MCP_CONFIG`: the absolute path to the local Erga configuration.
- `ERGA_MCP_TOOL_PROFILE=career`: the bounded client-neutral job workflow.

## Least-privilege tool profiles

The default profile remains `default` for backward compatibility and exposes the complete legacy
tool surface. For a narrower client integration, set the non-secret `[mcp].tool_profile` value in
the local Erga configuration, or override it per process with `ERGA_MCP_TOOL_PROFILE`. The
environment value takes precedence; neither setting is a credential.

| Profile | Exposed tools |
| --- | --- |
| `career` | Job intake, application/evidence reads, public-page research, local resume/cover-letter artifacts, validation, and export. Excludes mail, Hermes monitors, Git scanning, and token recording. |
| `read` | Local read-only records, tracker, and token summary. It deliberately excludes writing-sample/template content. |
| `research` | `read` plus bounded public HTTP(S) scraping and CSS-section extraction. |
| `write` | `read` plus local proposal, research-recording, export, token-recording, and validation tools; no network or Hermes-only tools. |
| `hermes` | `read` plus configured mail synchronization and the Hermes monitor-script installer. |
| `default` | All legacy tools, including job intake and advanced workspace setup. |

For example, a generic research client can use:

```json
"env": {
  "ERGA_MCP_CONFIG": "/absolute/path/to/erga-mcp-config.toml",
  "ERGA_MCP_TOOL_PROFILE": "research"
}
```

Use a disposable synthetic configuration to verify the selected surface before connecting personal
recruiting data.

## Claude Desktop and Cursor

Both use an `mcpServers` JSON object. Add this entry to the client-managed MCP configuration file; do not overwrite other servers:

```json
{
  "mcpServers": {
    "erga-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/erga-mcp", "run", "erga-mcp"],
      "env": {
        "ERGA_MCP_CONFIG": "/absolute/path/to/erga-mcp-config.toml"
      }
    }
  }
}
```

See the vendor's MCP configuration UI/documentation for the exact user- versus workspace-scope file. Restart or reload the client, then verify that `pipeline_status` is available before allowing write or execution-capable tools.

## Claude Code

Register the same stdio command with Claude Code's MCP command. The Claude Code MCP documentation supports a command plus arguments and environment variables:

```bash
claude mcp add \
  --env ERGA_MCP_CONFIG=/absolute/path/to/erga-mcp-config.toml \
  --env ERGA_MCP_TOOL_PROFILE=career \
  --scope project \
  --transport stdio \
  erga-mcp \
  -- uv --directory /absolute/path/to/erga-mcp run erga-mcp
```

## Codex

Register a local stdio server with Codex, or add the equivalent user-level TOML:

```toml
[mcp_servers.erga-mcp]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/erga-mcp", "run", "erga-mcp"]

[mcp_servers.erga-mcp.env]
ERGA_MCP_CONFIG = "/absolute/path/to/erga-mcp-config.toml"
ERGA_MCP_TOOL_PROFILE = "career"
```

Codex supports project-scoped `.codex/config.toml` in trusted projects. The generated entry also
sets write-aware MCP approval behavior and longer startup/execution timeouts for job intake and
local compilation.

## OpenCode

OpenCode V2 defines local stdio servers under `mcp.servers`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "erga-mcp": {
        "type": "local",
        "command": ["/absolute/path/to/erga-mcp"],
        "cwd": "/absolute/path/to/resume-workspace",
        "environment": {
          "ERGA_MCP_CONFIG": "/absolute/path/to/erga-mcp-config.toml",
          "ERGA_MCP_TOOL_PROFILE": "career"
        }
      }
    }
  }
}
```

OpenCode provider authentication and model selection are independent of Erga. Use the model access
already configured in OpenCode; never put provider credentials in the Erga MCP entry.

## VS Code

Create or update `.vscode/mcp.json` (or the user `mcp.json`) through **MCP: Add Server**:

```json
{
  "servers": {
    "erga-mcp": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/erga-mcp", "run", "erga-mcp"],
      "env": {
        "ERGA_MCP_CONFIG": "/absolute/path/to/erga-mcp-config.toml"
      }
    }
  }
}
```

Prefer user scope for a personal recruiting workspace; use workspace scope only when the local config contains no personal path information that should be shared.

## OpenClaw

OpenClaw supports the same local stdio contract. Register it with absolute executable and project paths, then use `openclaw mcp probe erga-mcp --json` to verify connection and tool discovery without involving a model. Its Streamable HTTP mode can use the loopback URL below, but stdio remains simpler.

## Local Streamable HTTP (opt-in)

Stdio remains the default and is recommended for desktop/CLI clients. For an MCP client that requires HTTP on the **same machine**, Erga supports opt-in, loopback-only Streamable HTTP:

```bash
ERGA_MCP_TRANSPORT=streamable-http \
ERGA_MCP_HTTP_HOST=127.0.0.1 \
ERGA_MCP_HTTP_PORT=8765 \
ERGA_MCP_CONFIG=/absolute/path/to/erga-mcp-config.toml \
uv --directory /absolute/path/to/erga-mcp run erga-mcp
```

The endpoint is `http://127.0.0.1:8765/mcp`. The server rejects non-loopback bindings and rejects every request carrying an `Origin` header. It is designed for native desktop/CLI MCP clients, which do not send browser Origins. Browser-hosted clients and CORS are deliberately unsupported.

This mode is deliberately **not a remote deployment feature**. Do not bind it to a LAN or public interface. Remote/multi-user deployment requires authenticated identities, tenant-specific storage/configuration, artifact redaction, authorization policies, TLS, and a reviewed process model.

## Compatibility checks

Erga maintains official Python MCP SDK checks for both a spawned stdio server from an installed
wheel and a real ephemeral Streamable HTTP server. Every profile must discover
`erga_capabilities` and `pipeline_status`, then call both without error. The `career` and `default`
profiles expose `intake_job_url`; narrower research/read/write profiles intentionally do not.
`erga_capabilities` reports `model_api_required: false`, `reasoning_host: mcp-client`, and the
known client configuration targets.

## References

- [MCP Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [OpenCode MCP servers](https://opencode.ai/v2/docs/mcp-servers)
- [VS Code MCP servers](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [Cursor MCP](https://docs.cursor.com/context/mcp)
