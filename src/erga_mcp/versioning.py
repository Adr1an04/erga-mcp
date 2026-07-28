"""Public, data-minimized compatibility constants for Erga MCP clients."""

from __future__ import annotations

from . import __version__

MCP_CONTRACT_VERSION = "1.0"
MINIMUM_CLIENT_CONTRACT_VERSION = "1.0"
PACKAGE_MANIFEST_SCHEMA_VERSION = 1
SQLITE_SCHEMA_VERSION = 1
SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")


def capabilities(*, tool_profile: str, capability_classes: list[str]) -> dict[str, object]:
    """Return stable, non-sensitive server capabilities for local MCP clients."""
    return {
        "server_version": __version__,
        "mcp_contract_version": MCP_CONTRACT_VERSION,
        "minimum_client_contract_version": MINIMUM_CLIENT_CONTRACT_VERSION,
        "package_manifest_schema_version": PACKAGE_MANIFEST_SCHEMA_VERSION,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "supported_transports": list(SUPPORTED_TRANSPORTS),
        "tool_profile": tool_profile,
        "capability_classes": sorted(capability_classes),
    }
