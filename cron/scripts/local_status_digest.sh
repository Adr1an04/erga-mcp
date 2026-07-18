#!/bin/sh
# Deterministic local-only digest for Hermes --no-agent cron jobs.
# Usage: local_status_digest.sh /absolute/path/to/config.toml
set -eu

if [ "$#" -ne 1 ]; then
    printf '%s\n' 'usage: local_status_digest.sh /absolute/path/to/config.toml' >&2
    exit 64
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

exec uv --directory "$project_root" run recruiting-pipeline status --config "$1"
