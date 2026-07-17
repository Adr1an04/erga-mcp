# Hermes Cron Examples

Cron is optional and must remain bounded to local, reversible work.

## Phase 1: local status digest

After the MCP/local core is installed, a cron job may summarize local records. It must not submit applications or contact any service.

```text
Schedule: every weekday at 09:00
Prompt: Summarize local Recruiting Pipeline records and list only items requiring review. Do not apply, send, or modify external systems.
Workdir: /absolute/path/to/recruiting-pipeline
Skill: recruiting-pipeline
Delivery: local or a private approved channel
```

## Future Zoho collection stage

A future read-only adapter should run as a deterministic no-agent script first. It may poll only the configured folder using the minimum OAuth scope, normalize candidate events locally, and exit. A separate agent job can summarize those events.

Never schedule automatic applications, external résumé syncs, emails, or social-media actions.
