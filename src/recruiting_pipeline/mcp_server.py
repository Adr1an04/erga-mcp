from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP

from .cli import DEFAULT_CONFIG_PATH
from .config import load_config
from .store import PipelineStore


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def build_server(config_path: Path) -> FastMCP:
    """Build a read-only MCP interface for an existing local pipeline."""
    config = load_config(config_path)
    store = PipelineStore(config.data_dir / "pipeline.sqlite3")
    store.initialize()
    server = FastMCP(
        "Recruiting Pipeline",
        instructions=(
            "Local recruiting context only. These tools are read-only; they never submit "
            "applications, send messages, change mail, or modify resume files."
        ),
    )

    @server.tool()
    def pipeline_status() -> dict[str, int]:
        """Return counts for local-only recruiting records."""
        return {
            "applications": len(store.list_applications()),
            "evidence": len(store.list_evidence()),
            "mail_events": len(store.list_mail_events()),
            "audit_events": len(store.audit_events()),
        }

    @server.tool()
    def list_applications() -> list[dict[str, object]]:
        """List local application records; no external system is queried."""
        return [
            cast(dict[str, object], _json_value(asdict(application)))
            for application in store.list_applications()
        ]

    @server.tool()
    def list_evidence() -> list[dict[str, object]]:
        """List locally stored evidence records used for truthful resume proposals."""
        return [
            cast(dict[str, object], _json_value(asdict(evidence)))
            for evidence in store.list_evidence()
        ]

    @server.tool()
    def list_mail_events() -> list[dict[str, object]]:
        """List normalized local mail events; previews and message bodies are not retained."""
        return [
            cast(dict[str, object], _json_value(asdict(event)))
            for event in store.list_mail_events()
        ]

    return server


def main() -> None:
    raw_path = os.environ.get("RECRUITING_PIPELINE_CONFIG")
    config_path = Path(raw_path).expanduser() if raw_path else DEFAULT_CONFIG_PATH
    build_server(config_path).run()


if __name__ == "__main__":
    main()
