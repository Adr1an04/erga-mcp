from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp import __version__
from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.mcp_server import build_server


class McpCapabilitiesTests(unittest.TestCase):
    def test_capabilities_are_compact_versioned_and_safe_in_read_profile(self) -> None:
        config = DEFAULT_CONFIG.replace('tool_profile = "default"', 'tool_profile = "read"')
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(config, encoding="utf-8")
            server = build_server(config_path)
            tools = {tool.name for tool in asyncio.run(server.list_tools())}
            result = asyncio.run(server.call_tool("erga_capabilities", {}))

        self.assertIn("erga_capabilities", tools)
        self.assertEqual(result.structured_content["server_version"], __version__)
        self.assertEqual(result.structured_content["mcp_contract_version"], "1.0")
        self.assertEqual(result.structured_content["minimum_client_contract_version"], "1.0")
        self.assertEqual(
            result.structured_content["supported_transports"], ["stdio", "streamable-http"]
        )
        self.assertEqual(result.structured_content["tool_profile"], "read")
        self.assertNotIn("/", repr(result.structured_content))
        self.assertNotIn("config", repr(result.structured_content).casefold())


if __name__ == "__main__":
    unittest.main()
