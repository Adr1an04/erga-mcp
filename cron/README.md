# Hermes Cron Examples

Cron is optional and must remain bounded to local, reversible work. Do not install a job until the local configuration path and private delivery destination have been reviewed.

## Phase 1: deterministic local status digest

`cron/scripts/local_status_digest.sh` runs only:

```text
recruiting-pipeline status --config <configured local path>
```

It performs no network access, mail access, applications, résumé changes, or agent reasoning. Verify it manually first:

```bash
./cron/scripts/local_status_digest.sh /absolute/path/to/recruiting-pipeline-config.toml
```

For Hermes, copy the script to the selected profile's scripts directory, then create a no-agent job. Replace placeholders locally; do not commit a filled-in command or personal path.

```bash
mkdir -p ~/.hermes/profiles/<profile>/scripts
cp cron/scripts/local_status_digest.sh ~/.hermes/profiles/<profile>/scripts/

hermes --profile <profile> cron create '0 9 * * 1-5' \
  --name recruiting-pipeline-status \
  --script ~/.hermes/profiles/<profile>/scripts/local_status_digest.sh \
  --no-agent \
  --workdir /absolute/path/to/recruiting-pipeline \
  --deliver local \
  /absolute/path/to/recruiting-pipeline-config.toml
```

`--no-agent` is deliberate: the script's JSON stdout is delivered verbatim, without an LLM interpreting local records. Change `--deliver local` to a private, reviewed destination only after confirming the output contains no sensitive data.

## Future Zoho collection stage

A future read-only adapter should run as a deterministic no-agent script first. It may poll only the configured folder using the minimum OAuth scope, normalize candidate events locally, and exit. A separate, user-invoked review can summarize those events.

Never schedule automatic applications, external résumé syncs, emails, or social-media actions.
