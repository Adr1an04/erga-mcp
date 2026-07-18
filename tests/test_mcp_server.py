from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.config import DEFAULT_CONFIG
from recruiting_pipeline.mcp_server import build_server


class McpServerTests(unittest.TestCase):
    def test_exposes_only_read_only_pipeline_tools(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG)

            server = build_server(config_path)
            tools = asyncio.run(server.list_tools())

            self.assertEqual(
                {tool.name for tool in tools},
                {"pipeline_status", "list_applications", "list_evidence", "list_mail_events"},
            )


if __name__ == "__main__":
    unittest.main()
