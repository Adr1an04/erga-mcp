# Reliable MCP routing for pasted job URLs

## Finding

MCP tool metadata can improve model selection, but an MCP server cannot guarantee that its tool is
called. The protocol defines discovery and invocation; the client/host exposes tools and the model
or host chooses whether to call one. A hard rule such as “every pasted job link runs intake before
the assistant answers” therefore belongs in the host routing layer.

This project uses two complementary layers:

1. **Portable MCP fallback:** one purpose-specific `intake_job_url` tool with trigger-first wording,
   a documented URL-only input, accurate annotations, structured output, explicit exclusions, and
   an advanced tool whose description clearly disambiguates it.
2. **Deterministic Hermes route:** an opt-in `pre_llm_call` plugin recognizes job links in the current
   user message and dispatches the MCP intake before the model turn. The plugin reports the result
   into that turn and respects explicit summary-only/skip-intake requests.

## Patterns adopted from established servers

- Put “when to use” triggers, literal user-message shapes, exclusions, and exact argument handling
  in the tool description. Sentry's URL tools use prominent use/do-not-use guidance, whole-URL
  handling, centralized parsing, and model-selection evaluations:
  [tool description](https://github.com/getsentry/sentry-mcp/blob/41a25268c6e32f483e6aad4b40ff63007de2f441/packages/mcp-core/src/tools/catalog/get-sentry-resource.ts#L507-L607),
  [URL parser](https://github.com/getsentry/sentry-mcp/blob/41a25268c6e32f483e6aad4b40ff63007de2f441/packages/mcp-core/src/internal/url-helpers.ts#L69-L132),
  [selection evals](https://github.com/getsentry/sentry-mcp/blob/41a25268c6e32f483e6aad4b40ff63007de2f441/packages/mcp-server-evals/src/evals/get-sentry-resource.eval.ts#L4-L60).
- Keep the default tool surface focused. GitHub's MCP server uses toolsets and allow-lists to reduce
  context and selection ambiguity:
  [GitHub MCP toolsets](https://github.com/github/github-mcp-server/blob/1338dbed4a044ee26422d4212bac3a8037fdb7ff/README.md#L433-L490).
- Provide parameter descriptions and structured output schemas, not open-ended strings/dicts. The
  MCP tools specification describes the fields clients and models receive and the role of output
  schemas:
  [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
- Duplicate cross-tool routing guidance in server instructions for clients that consume it, but
  keep critical triggers in each tool description. Current Hermes converts MCP name, description,
  and input schema into its model-visible tool definition; its source does not currently forward
  server instructions, title, annotations, or output schema for selection:
  [Hermes MCP adapter](https://github.com/NousResearch/hermes-agent/blob/main/tools/mcp_tool.py).

## Lifecycle and readiness

Cold-start requests should not race tool discovery. A host should finish `initialize` and
`tools/list`, verify the required intake tool, and atomically publish the new inventory before
accepting a tool-dependent turn. On config/code changes, reconnect and list again. Dynamic servers
may send `notifications/tools/list_changed`, but that notification does not hot-reload the code of
an already-running stdio subprocess:
[MCP list-changed notification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools#list-changed-notification),
[Hermes discovery and reload](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp#runtime-behavior).

Hermes can complete MCP discovery just after a fresh gateway accepts its first turn. The optional
router therefore retries only the host's exact unknown-tool/not-connected startup responses inside
a 30-second bound. It does not retry URL, configuration, compilation, or other operational errors.

## Safety constraints

The deterministic router is opt-in because intake writes local files. Its standing authorization is
limited to the current user's recognized job link and to the one local intake tool. Imported page
content never triggers routing. Intake must remain accurately marked as network-read/local-write,
must not gain submission or messaging behavior, and must validate public destinations, redirects,
content type, and response size. The fetcher connects to the validated numeric endpoint while
retaining the original hostname for TLS verification, uses one bounded fetch budget, and does not
honor ambient proxy variables because a proxy would invalidate that endpoint guarantee.

Intake builds in a same-filesystem staging directory and publishes only a complete package with an
atomic rename. Canonical listing identities ignore common tracking parameters, while stable hash
suffixes prevent same-company posting collisions. Validation status and the configured PDF name are
persisted in the package manifest so retries report the original result rather than guessing.

## Regression strategy

- Schema contracts assert trigger phrases, only `job_url` required, typed URL guidance, structured
  output, accurate annotations, and clear separation from the advanced workspace tool.
- Direct MCP tests call the tool with a synthetic URL and template, verify all local artifacts,
  prove same-listing repeats are reuse-only, and cover failed/concurrent atomic publication.
- Deterministic router tests cover bare links, Discord/Markdown wrappers and previews, competing
  media links, explicit/negated opt-outs, cold-start readiness, non-job links, exact URL
  preservation, and one dispatch per turn.
- Optional provider evaluations should track model-selected tool precision/recall, but they are a
  portability signal rather than the guarantee. The host router's deterministic tests must remain
  100%.
