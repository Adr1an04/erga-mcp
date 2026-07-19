# Recruiting Pipeline router installed

This opt-in Hermes plugin treats a recognized job link as an explicit request to create local
Recruiting Pipeline review artifacts. It never submits an application or sends a message.

Hermes Agent 0.18.2 or newer is required for the `pre_llm_call` context hook and stable
`ctx.dispatch_tool(name, args)` interface used by this plugin. Run `hermes --version` to check and
`hermes update` before enabling the plugin if needed.

Register the MCP server with the standard `recruiting-pipeline` or `recruiting_pipeline` name before
enabling this plugin. Both names produce the default Hermes tool name
`mcp__recruiting_pipeline__intake_job_url`.

If you deliberately used another MCP server name, set `RECRUITING_PIPELINE_MCP_TOOL` in the Hermes
process environment to its complete prefixed intake-tool name. Restart the gateway after changing
plugin state or environment variables.

At gateway startup, MCP discovery can finish just after the first user message. The router retries
only Hermes' exact `Unknown tool` and `MCP server ... is not connected` readiness errors for up to
30 seconds. Set `RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS` to a value from 0 through 30 to
change that bounded wait; 0 disables retries. The retry interval defaults to 0.25 seconds and can
be adjusted, up to 5 seconds, with `RECRUITING_PIPELINE_MCP_READY_RETRY_SECONDS`. Operational
intake failures are returned immediately and are never retried.

“Summarize only” and “don't run the pipeline” opt out. A request such as “don't just
summarize—run the pipeline” still runs intake, as requested.
