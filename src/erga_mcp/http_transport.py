"""Compatibility imports for code written before the MCP package split."""

from .mcp.transport import HttpTransportSettings, protect_http_app

__all__ = ["HttpTransportSettings", "protect_http_app"]
