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

## Read-only Zoho collection stage

The CLI includes a read-only Zoho adapter. A deterministic no-agent poll may run `recruiting-pipeline zoho sync` against a configured Inbox using the minimum OAuth scopes, normalize newly observed metadata locally, and exit. Configure the client identifier and Keychain-backed OAuth connection locally; never commit credentials, personal paths, or a filled-in cron command.

Treat cron output as a prompt for review. Never schedule automatic applications, external résumé syncs, emails, or social-media actions.
