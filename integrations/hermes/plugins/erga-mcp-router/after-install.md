# Erga MCP router installed

This opt-in Hermes plugin treats a recognized job link as an explicit request to create local
Erga MCP review artifacts. It never submits an application or contacts an employer.
The separate user-invoked monitor command may schedule private alerts back to its origin chat.

Hermes Agent 0.18.2 or newer is required for the `pre_llm_call` context hook and stable
`ctx.dispatch_tool(name, args)` interface used by this plugin. Run `hermes --version` to check and
`hermes update` before enabling the plugin if needed.

Register the MCP server with the standard `erga-mcp` or `erga_mcp` name before
enabling this plugin. Both names produce the default Hermes tool name
`mcp__erga_mcp__intake_job_url`.

If you deliberately used another MCP server name, set `ERGA_MCP_TOOL` in the Hermes
process environment to its complete prefixed intake-tool name. Restart the gateway after changing
plugin state or environment variables.

In Discord, `/intake-job <job-posting-url>` opens a persisted, mobile-friendly planning flow before
generation. Emoji-labeled buttons collect one project-portfolio decision and one copy-strategy
decision, then show a review screen. Planning fetches and saves the official posting but creates no
application, résumé, package, or tracker entry. Only the explicit **🚀 Generate résumé** button runs
the validated intake. The completed message replaces the review with the native validated PDF and
asks **Did you submit this application?** The owner-bound ✅ **Yes, applied** and ❌ **Still
drafting** controls update the exact local application ID and synchronize its configured Obsidian
tracker row; they never submit anything remotely. Button state is opaque, single-use, owner-bound
to the requesting Discord user, and expires after 24 hours. A plain job URL sent outside the slash
command keeps the direct-intake route and receives the same confirmation controls when its validated
PDF is delivered.

Custom MCP server names may also override the planning tools with
`ERGA_MCP_TAILORING_PLAN_CREATE_TOOL`, `ERGA_MCP_TAILORING_PLAN_UPDATE_TOOL`, and
`ERGA_MCP_TAILORING_PLAN_EXECUTE_TOOL` using their complete Hermes-prefixed names. Override the
status tool with `ERGA_MCP_APPLICATION_STATUS_TOOL` only when the MCP server uses a nonstandard
name.

`/erga-tracker` adds a **Research · Company** control for roles at OA, interview, or offer. The
private navigator opens the official posting and deduplicated saved web links, inventories local
research notes, shows résumé availability and the tracked next action, and gives stage-specific
preparation guidance. Community and secondary sources are always labeled unverified. **Refresh
sources** explicitly reruns bounded public research; **Create OA/interview/offer brief** writes the
concise local stage checklist. Neither action submits an application or contacts anyone.

`/erga-orbit [recruiting cycle]` renders the same deterministic aggregate application funnel used
by Erga's local core and attaches the PNG without exposing its host path. `/erga-tracker` also
includes an **Orbit** control. Generated PNGs are temporary by default and are removed from the
Erga host after Discord confirms the upload. Use the ✅/❌ controls on the preview or **Orbit
images** in `/erga-settings` to change the preset for future renders. The snapshot uses no model
tokens, includes no employer names, begins at **Applied**, and shows only OA, recorded interview
rounds, offer, and recruiting outcomes. Pre-application setup states are excluded, and tracker-only
later stages do not receive invented intermediate rounds.

At gateway startup, MCP discovery can finish just after the first user message. The router retries
only Hermes' exact `Unknown tool` and `MCP server ... is not connected` readiness errors for up to
30 seconds. Set `ERGA_MCP_READY_TIMEOUT_SECONDS` to a value from 0 through 30 to
change that bounded wait; 0 disables retries. The retry interval defaults to 0.25 seconds and can
be adjusted, up to 5 seconds, with `ERGA_MCP_READY_RETRY_SECONDS`. Operational
intake failures are returned immediately and are never retried.

“Summarize only” and “don't run the pipeline” opt out. A request such as “don't just
summarize—run the pipeline” still runs intake, as requested.

When intake returns a successfully validated PDF, gateway-delivered replies (Discord, Signal,
Telegram, and similar message platforms) include it as a native document attachment. The plugin
unwraps Hermes' MCP result envelope, validates that the file is a real PDF inside the returned
package's `artifacts` directory, and adds Hermes' outbound document-upload directive. A server-local
path is never presented as a substitute for the upload. Local CLI responses remain text-only.

After official-posting intake, the router runs Erga's unified `discover_job_research` pipeline.
It reviews bounded official-role, early-career, engineering, interview-process, assessment,
community, technical-preparation, and public-recruiter searches; ranks candidates by employer and
role relevance, source quality, and available recency signals; and saves both a cited note and a
structured quality index. Community material remains explicitly unverified. If discovery is
unavailable, primary intake and the PDF attachment still proceed.

Repository, documentation, community-thread, profile, and scheduling URLs are not treated as job
postings even when nearby text contains words such as “job” or “internship.” The MCP server also
verifies fetched source evidence before creating any application or résumé package.

To enable the monitoring half, first configure `mail` in the local pipeline config, then run
`/setup-erga-monitor` in the private connected conversation that should receive alerts. The
plugin prepares two no-agent scripts and creates an every-15-minute event monitor plus a daily
history digest. Hermes captures the current chat/thread as the delivery origin. The event monitor
stays silent when no new relevant messages are found. The command works even when the connected
platform does not expose the general `cronjob` toolset, and installs runners in the active Hermes
profile. `/setup-erga-monitor 14` uses a 14-day window for the daily digest.

Run `/export-erga` to create a private ZIP containing application records, recruiting-event
and audit history, evidence, and generated job packages. The plugin validates that the ZIP is
inside the configured export directory and sends it as a native document attachment rather than a
server-local path.
