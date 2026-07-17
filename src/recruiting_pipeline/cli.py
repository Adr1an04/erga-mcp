from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .store import PipelineStore

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "recruiting-pipeline" / "config.toml"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="recruiting-pipeline",
        description=(
            "Local-first recruiting workflow tools. No external actions are performed by default."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser(
        "init", help="create a local non-secret configuration and database"
    )
    init.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    status = subcommands.add_parser("status", help="show local pipeline counts")
    status.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)

    applications = subcommands.add_parser("applications", help="list local application records")
    applications.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    return parser


def _initialize(config_path: Path) -> int:
    config_path = config_path.expanduser()
    if config_path.exists():
        print(f"Config already exists: {config_path}")
        return 2
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(config_path)
    PipelineStore(config.data_dir / "pipeline.sqlite3").initialize()
    print(f"Created local configuration: {config.config_path}")
    print(f"Created local data directory: {config.data_dir}")
    return 0


def _store_for(config_path: Path) -> PipelineStore:
    config = load_config(config_path)
    store = PipelineStore(config.data_dir / "pipeline.sqlite3")
    store.initialize()
    return store


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "init":
        return _initialize(args.config)

    store = _store_for(args.config)
    if args.command == "status":
        print(
            json.dumps(
                {
                    "applications": len(store.list_applications()),
                    "evidence": len(store.list_evidence()),
                    "audit_events": len(store.audit_events()),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "applications":
        applications = [application.__dict__ for application in store.list_applications()]
        print(json.dumps(applications, default=str))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
