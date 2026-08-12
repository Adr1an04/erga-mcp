from __future__ import annotations

from typing import Any

from mcp.types import SamplingMessage, TextContent, Tool, ToolChoice, ToolUseContent

from ..ai_resume_tailoring import (
    TailoringDraftClient,
    TailoringDraftRequest,
    TailoringDraftResponse,
)


class MCPTailoringDraftClient(TailoringDraftClient):
    """Translate client-neutral tailoring requests to the MCP sampling wire contract."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def draft(self, request: TailoringDraftRequest) -> TailoringDraftResponse:
        result = await self._session.create_message(
            [
                SamplingMessage(role=message.role, content=TextContent(text=message.text))
                for message in request.messages
            ],
            max_tokens=request.max_tokens,
            system_prompt=request.system_prompt,
            include_context="none",
            temperature=request.temperature,
            tools=[
                Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
                for tool in request.tools
            ],
            tool_choice=ToolChoice(mode="required"),
            related_request_id=request.related_request_id,
        )
        model = str(getattr(result, "model", "host-model"))
        content = getattr(result, "content", None)
        blocks = content if isinstance(content, list) else [content]
        submissions = [
            block.input
            for block in blocks
            if isinstance(block, ToolUseContent)
            and any(block.name == tool.name for tool in request.tools)
        ]
        if len(submissions) != 1 or not isinstance(submissions[0], dict):
            raise ValueError("the tailoring model did not return one structured project submission")
        return TailoringDraftResponse(submission=submissions[0], model=model)
