from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer


@dataclass(frozen=True)
class ToolRegistry:
    """Apply Erga's capability and strict-input policy at the MCP SDK boundary."""

    server: MCPServer
    enabled_names: frozenset[str]

    def tool(self, name: str, **kwargs: Any) -> Any:
        if name not in self.enabled_names:
            return lambda function: function

        def register(function: Any) -> Any:
            registered = self.server.tool(name=name, **kwargs)(function)
            # MCPServer does not yet expose argument-model configuration publicly. Keep this one
            # intentional SDK coupling isolated here and covered by discovery/call tests so a v2
            # SDK change fails loudly. Pydantic otherwise ignores misspelled tool arguments.
            tool = self.server._tool_manager.get_tool(name)  # noqa: SLF001
            if tool is None:  # pragma: no cover - SDK registration invariant
                raise RuntimeError(f"MCP tool registration failed: {name}")
            argument_model = tool.fn_metadata.arg_model
            argument_model.model_config["extra"] = "forbid"
            argument_model.model_rebuild(force=True)
            tool.parameters = argument_model.model_json_schema(by_alias=True)
            return registered

        return register
