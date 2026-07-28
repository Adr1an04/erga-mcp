from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class HttpTransportSettings:
    """Safe settings for the opt-in, native-client-only Streamable HTTP transport."""

    host: str
    port: int

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
        return cls(host=host, port=port)


def protect_http_app(app: Any) -> Any:
    """Reject browser-originated requests; native local MCP clients omit Origin.

    Browser support requires a deliberately designed CORS and authentication policy. Denying all
    present Origin headers prevents accidental cross-origin browser exposure while preserving the
    native Streamable HTTP protocol used by desktop and CLI MCP clients.
    """

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
        await app(scope, receive, send)

    return protected
