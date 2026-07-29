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
        self.assertEqual(result[1]["server_version"], __version__)
        self.assertEqual(result[1]["mcp_contract_version"], "1.0")
        self.assertEqual(result[1]["minimum_client_contract_version"], "1.0")
        self.assertEqual(result[1]["supported_transports"], ["stdio", "streamable-http"])
        self.assertEqual(result[1]["tool_profile"], "read")
        self.assertFalse(result[1]["model_api_required"])
        self.assertEqual(result[1]["reasoning_host"], "mcp-client")
        self.assertEqual(
            result[1]["supported_clients"],
            ["codex", "claude-code", "opencode", "generic-mcp"],
        )
        self.assertNotIn("/", repr(result[1]))
        self.assertNotIn("config", repr(result[1]).casefold())


if __name__ == "__main__":
    unittest.main()
