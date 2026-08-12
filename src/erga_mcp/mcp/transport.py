from __future__ import annotations

import secrets
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

_LOOPBACK_HOST_HEADERS = [
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
    "[::1]",
    "[::1]:*",
]
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class HttpTransportSettings:
    """Safe settings for the opt-in, native-client-only Streamable HTTP transport."""

    host: str
    port: int
    bearer_token: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> HttpTransportSettings:
        host = environment.get("ERGA_MCP_HTTP_HOST", "127.0.0.1").strip().casefold()
        if host not in _LOOPBACK_HOSTS:
            raise ValueError(
                "Streamable HTTP may bind only to a loopback host; use stdio for remote clients."
            )
        raw_port = environment.get("ERGA_MCP_HTTP_PORT", "8765").strip()
        try:
            port = int(raw_port)
        except ValueError as error:
            raise ValueError("ERGA_MCP_HTTP_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise ValueError("ERGA_MCP_HTTP_PORT must be between 1 and 65535")
        bearer_token = environment.get("ERGA_MCP_HTTP_TOKEN", "").strip()
        if len(bearer_token) < 32:
            raise ValueError(
                "ERGA_MCP_HTTP_TOKEN is required for Streamable HTTP and must contain "
                "at least 32 characters"
            )
        return cls(host=host, port=port, bearer_token=bearer_token)


def protect_http_app(app: Any, *, bearer_token: str) -> Any:
    """Require a bearer token and reject browser-originated requests."""
    expected_authorization = f"Bearer {bearer_token}".encode()

    async def protected(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            origin = next(
                (value for key, value in scope["headers"] if key.lower() == b"origin"),
                None,
            )
            if origin is not None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Browser origins are not supported by this local MCP server.",
                    }
                )
                return
            authorization = next(
                (value for key, value in scope["headers"] if key.lower() == b"authorization"),
                b"",
            )
            if not secrets.compare_digest(authorization, expected_authorization):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"text/plain; charset=utf-8"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"A valid local MCP bearer token is required.",
                    }
                )
                return
        await app(scope, receive, send)

    return protected


def build_streamable_http_app(server: MCPServer, settings: HttpTransportSettings) -> Starlette:
    """Build an authenticated, loopback-only Streamable HTTP app."""
    transport_app = server.streamable_http_app(
        host=settings.host,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_LOOPBACK_HOST_HEADERS,
        ),
    )

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with server.session_manager.run():
            yield

    return Starlette(
        routes=[
            Mount(
                "/",
                app=protect_http_app(
                    transport_app,
                    bearer_token=settings.bearer_token,
                ),
            )
        ],
        lifespan=lifespan,
    )


def run_streamable_http(server: MCPServer, settings: HttpTransportSettings) -> None:
    """Run authenticated MCP Streamable HTTP on a loopback interface."""
    uvicorn.run(build_streamable_http_app(server, settings), host=settings.host, port=settings.port)
