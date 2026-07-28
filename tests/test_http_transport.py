from __future__ import annotations

import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from erga_mcp.http_transport import HttpTransportSettings, protect_http_app


class HttpTransportTests(unittest.TestCase):
    def test_defaults_to_loopback_streamable_http(self) -> None:
        settings = HttpTransportSettings.from_environment({})

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8765)

    def test_rejects_non_loopback_bindings(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            HttpTransportSettings.from_environment({"ERGA_MCP_HTTP_HOST": "0.0.0.0"})

    def test_rejects_all_browser_origins_but_allows_native_clients(self) -> None:
        async def health(_: object) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/mcp", health, methods=["POST"])])
        client = TestClient(protect_http_app(app))

        self.assertEqual(client.post("/mcp").status_code, 200)
        self.assertEqual(
            client.post("/mcp", headers={"Origin": "http://localhost:3000"}).status_code, 403
        )
        self.assertEqual(
            client.post("/mcp", headers={"Origin": "https://evil.example"}).status_code, 403
        )


if __name__ == "__main__":
    unittest.main()
