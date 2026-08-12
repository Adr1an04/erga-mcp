from __future__ import annotations

import unittest

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from erga_mcp.http_transport import HttpTransportSettings, protect_http_app

_HTTP_TOKEN = "test-token-with-at-least-thirty-two-characters"


class HttpTransportTests(unittest.TestCase):
    def test_defaults_to_loopback_streamable_http_with_explicit_token(self) -> None:
        settings = HttpTransportSettings.from_environment({"ERGA_MCP_HTTP_TOKEN": _HTTP_TOKEN})

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8765)
        self.assertEqual(settings.bearer_token, _HTTP_TOKEN)

    def test_requires_a_strong_explicit_bearer_token(self) -> None:
        for environment in ({}, {"ERGA_MCP_HTTP_TOKEN": "too-short"}):
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(ValueError, "at least 32 characters"):
                    HttpTransportSettings.from_environment(environment)

    def test_rejects_non_loopback_bindings(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            HttpTransportSettings.from_environment(
                {"ERGA_MCP_HTTP_HOST": "0.0.0.0", "ERGA_MCP_HTTP_TOKEN": _HTTP_TOKEN}
            )

    def test_requires_token_and_rejects_all_browser_origins(self) -> None:
        async def health(_: object) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/mcp", health, methods=["POST"])])
        client = TestClient(protect_http_app(app, bearer_token=_HTTP_TOKEN))

        self.assertEqual(client.post("/mcp").status_code, 401)
        self.assertEqual(
            client.post("/mcp", headers={"Authorization": f"Bearer {_HTTP_TOKEN}"}).status_code,
            200,
        )
        self.assertEqual(
            client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )
        self.assertEqual(
            client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {_HTTP_TOKEN}",
                    "Origin": "http://localhost:3000",
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                "/mcp",
                headers={
                    "Authorization": f"Bearer {_HTTP_TOKEN}",
                    "Origin": "https://evil.example",
                },
            ).status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
