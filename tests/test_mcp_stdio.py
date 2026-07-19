from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from recruiting_pipeline.config import DEFAULT_CONFIG


class McpStdioTests(unittest.TestCase):
    def test_fresh_client_discovers_primary_job_url_intake(self) -> None:
        async def discover(config_path: Path) -> None:
            environment = {
                **os.environ,
                "RECRUITING_PIPELINE_CONFIG": str(config_path),
            }
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "recruiting_pipeline.mcp_server"],
                env=environment,
            )
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    self.assertIn("including a bare link", initialized.instructions or "")
                    tools = await session.list_tools()
                    by_name = {tool.name: tool for tool in tools.tools}
                    self.assertIn("intake_job_url", by_name)
                    intake = by_name["intake_job_url"]
                    self.assertEqual(intake.inputSchema["required"], ["job_url"])
                    status = await session.call_tool("pipeline_status", {})
                    self.assertFalse(status.isError)

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            asyncio.run(discover(config_path))


if __name__ == "__main__":
    unittest.main()
