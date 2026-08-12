from __future__ import annotations

import asyncio
import unittest
from typing import Any

from mcp.types import CreateMessageResultWithTools, ToolUseContent

from erga_mcp.mcp.sampling import MCPTailoringDraftClient
from erga_mcp.resumes.ai_tailoring import (
    TailoringDraftMessage,
    TailoringDraftRequest,
    TailoringDraftTool,
)


class _Session:
    def __init__(self) -> None:
        self.args: tuple[object, ...] = ()
        self.kwargs: dict[str, Any] = {}

    async def create_message(self, *args: object, **kwargs: Any) -> object:
        self.args = args
        self.kwargs = kwargs
        return CreateMessageResultWithTools(
            role="assistant",
            content=ToolUseContent(
                name="submit_projects",
                id="call-1",
                input={"projects": []},
            ),
            model="synthetic-model",
            stopReason="toolUse",
        )


class McpSamplingTests(unittest.TestCase):
    def test_translates_client_neutral_tailoring_request_and_result(self) -> None:
        session = _Session()
        request = TailoringDraftRequest(
            messages=(TailoringDraftMessage(role="user", text="bounded prompt"),),
            max_tokens=512,
            system_prompt="bounded system prompt",
            temperature=0.2,
            tools=(
                TailoringDraftTool(
                    name="submit_projects",
                    description="Submit projects.",
                    input_schema={"type": "object"},
                ),
            ),
            related_request_id="request-1",
        )

        response = asyncio.run(MCPTailoringDraftClient(session).draft(request))

        self.assertEqual(response.submission, {"projects": []})
        self.assertEqual(response.model, "synthetic-model")
        self.assertEqual(session.args[0][0].content.text, "bounded prompt")
        self.assertEqual(session.kwargs["tools"][0].name, "submit_projects")
        self.assertEqual(session.kwargs["tool_choice"].mode, "required")
        self.assertEqual(session.kwargs["include_context"], "none")
        self.assertEqual(session.kwargs["related_request_id"], "request-1")

    def test_rejects_missing_structured_tool_submission(self) -> None:
        class MissingSubmissionSession:
            async def create_message(self, *_: object, **__: Any) -> object:
                return object()

        request = TailoringDraftRequest(
            messages=(TailoringDraftMessage(role="user", text="prompt"),),
            max_tokens=32,
            system_prompt="system",
            temperature=0,
            tools=(TailoringDraftTool("submit_projects", "Submit.", {"type": "object"}),),
            related_request_id="request-1",
        )

        with self.assertRaisesRegex(ValueError, "structured project submission"):
            asyncio.run(MCPTailoringDraftClient(MissingSubmissionSession()).draft(request))


if __name__ == "__main__":
    unittest.main()
