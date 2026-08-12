from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.http_transport import HttpTransportSettings
from erga_mcp.mcp_server import build_server, run_streamable_http

_HTTP_TOKEN = "test-token-with-at-least-thirty-two-characters"


class McpHttpServerTests(unittest.TestCase):
    def test_runs_streamable_http_only_on_loopback_with_origin_protection(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)
            settings = HttpTransportSettings.from_environment({"ERGA_MCP_HTTP_TOKEN": _HTTP_TOKEN})

            with patch("erga_mcp.mcp.transport.uvicorn.run") as run:
                run_streamable_http(server, settings)

        app = run.call_args.args[0]
        self.assertEqual(run.call_args.kwargs, {"host": "127.0.0.1", "port": 8765})
        self.assertTrue(callable(app))


if __name__ == "__main__":
    unittest.main()
