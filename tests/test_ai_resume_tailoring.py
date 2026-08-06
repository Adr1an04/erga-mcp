from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mcp.types import CreateMessageResultWithTools, ToolUseContent

from erga_mcp.ai_resume_tailoring import (
    _FORBIDDEN_GIT_PROSE,
    _latex_text,
    _normalized_number,
    draft_evidence_backed_projects,
)
from erga_mcp.models import Evidence
from erga_mcp.project_inventory import ProjectCandidate
from erga_mcp.resume import resume_item_texts


class _SamplingSession:
    def __init__(self, submission: dict[str, object]) -> None:
        self.submission = submission
        self.calls: list[dict[str, Any]] = []
        self.messages: list[object] = []

    async def create_message(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        self.messages.append(args[0])
        return CreateMessageResultWithTools(
            role="assistant",
            content=ToolUseContent(
                name="submit_evidence_backed_projects",
                id="call_1",
                input=self.submission,
            ),
            model="synthetic-tailor",
            stopReason="toolUse",
        )


def _candidate() -> ProjectCandidate:
    return ProjectCandidate(
        id="api-platform",
        title="API Platform",
        latex=(
            r"\resumeProjectHeading{\textbf{API Platform} $|$ \textit{Python, FastAPI}}{}"
            "\n"
            r"\resumeItemListStart"
            "\n"
            r"\resumeItem{Built a Python API serving 100 users with authenticated requests.}"
            "\n"
            r"\resumeItem{Tested 20 API routes covering request validation and failures.}"
            "\n"
            r"\resumeItemListEnd"
        ),
        evidence_ids=("ev_api",),
        bullet_evidence_ids=(("ev_api",), ("ev_api",)),
        tags=("python", "fastapi", "api", "testing"),
        git_repositories=("example/api-platform",),
    )


class AIResumeTailoringTests(unittest.TestCase):
    def test_numeric_normalization_ignores_sentence_punctuation_but_preserves_decimals(
        self,
    ) -> None:
        self.assertEqual(_normalized_number("2026."), "2026")
        self.assertEqual(_normalized_number("99.3%"), "99.3%")

    def test_raw_git_filter_allows_implementation_files_but_blocks_accounting(self) -> None:
        self.assertIsNone(_FORBIDDEN_GIT_PROSE.search("Generated typed configuration files."))
        self.assertIsNotNone(_FORBIDDEN_GIT_PROSE.search("Changed 12 files across 4 commits."))

    def test_model_typography_is_normalized_before_latex_rendering(self) -> None:
        self.assertEqual(
            _latex_text("Shipped ‘typed’ UI—under 30 ms."), "Shipped 'typed' UI-under 30 ms."
        )
        with self.assertRaisesRegex(ValueError, "ASCII"):
            _latex_text("Shipped an interface 🚀")

    def _draft(self, submission: dict[str, object], *, retry_feedback: str = ""):
        with TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.tex"
            resume.write_text(
                r"\newcommand{\resumeItem}[1]{\item #1}"
                "\n"
                r"\begin{document}"
                "\n"
                r"\section{Experience}"
                "\n"
                r"\resumeItem{Deployed a production service.}"
                "\n"
                r"\section{Projects}"
                "\n"
                r"\resumeProjectHeading{\textbf{Template}}{}"
                "\n"
                r"\resumeItem{Built a template project.}"
                "\n"
                r"\section{Technical Skills}"
                "\nPython"
                "\n"
                r"\end{document}"
                "\n",
                encoding="utf-8",
            )
            approved = Evidence(
                id="ev_api",
                source_ref="approved:api-platform",
                text="Approved API Platform evidence.",
                approved=True,
                created_at=datetime.now(UTC),
            )
            git = Evidence(
                id="ev_git",
                source_ref="git-derived:api-platform@abc",
                text="Implemented API testing work across 3 commits and 4 files.",
                approved=True,
                created_at=datetime.now(UTC),
            )
            session = _SamplingSession(submission)
            result = asyncio.run(
                draft_evidence_backed_projects(
                    session=session,
                    related_request_id="request-1",
                    resume_path=resume,
                    job_description="Required: Python FastAPI API testing",
                    candidates=(_candidate(),),
                    evidence=[approved, git],
                    reports=(
                        {
                            "project_id": "api-platform",
                            "evidence_ids": ["ev_git"],
                        },
                    ),
                    project_count=1,
                    bullets_per_project=2,
                    bullet_min_chars=90,
                    bullet_target_chars=105,
                    bullet_max_chars=116,
                    require_unique_lead_verbs=True,
                    retry_feedback=retry_feedback,
                )
            )
            return result, session

    def test_model_can_synthesize_new_bullets_with_project_scoped_evidence(self) -> None:
        result, session = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": (
                                    "Engineered a Python API serving 100 users with authenticated "
                                    "request handling."
                                ),
                                "evidence_ids": ["ev_api"],
                            },
                            {
                                "text": (
                                    "Validated 20 API routes across request validation and failure "
                                    "handling."
                                ),
                                "evidence_ids": ["ev_api"],
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(result.model, "synthetic-tailor")
        self.assertEqual(result.candidates[0].bullet_evidence_ids, (("ev_api",), ("ev_api",)))
        self.assertEqual(
            resume_item_texts(result.candidates[0].latex),
            (
                "Engineered a Python API serving 100 users with authenticated request handling.",
                "Validated 20 API routes across request validation and failure handling.",
            ),
        )
        self.assertEqual(session.calls[0]["tool_choice"].mode, "required")
        self.assertEqual(session.calls[0]["include_context"], "none")
        bullet_schema = session.calls[0]["tools"][0].input_schema["properties"]["projects"][
            "items"
        ]["properties"]["bullets"]["items"]["properties"]["text"]
        self.assertEqual(bullet_schema["maxLength"], 116)
        messages = session.messages[0]
        assert isinstance(messages, list)
        prompt = json.loads(messages[0].content.text)
        self.assertIn("Engineered", prompt["allowed_lead_verbs"])
        self.assertIn("Validated", prompt["allowed_lead_verbs"])

    def test_rejects_a_metric_absent_from_the_cited_project_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "number absent.*999"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": "Engineered a Python API serving 999 users safely.",
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": "Validated 20 API routes across request failures.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_adds_the_project_evidence_id_that_supports_a_number(self) -> None:
        result, _ = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": "Engineered a Python API serving 100 users safely.",
                                "evidence_ids": ["ev_git"],
                            },
                            {
                                "text": "Validated 20 API routes across request failures.",
                                "evidence_ids": ["ev_api"],
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(result.candidates[0].bullet_evidence_ids[0], ("ev_git", "ev_api"))

    def test_retry_forbids_the_numeric_token_from_the_rejected_attempt(self) -> None:
        result, session = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": "Engineered a Python API serving 100 users safely.",
                                "evidence_ids": ["ev_api"],
                            },
                            {
                                "text": "Validated 20 API routes across request failures.",
                                "evidence_ids": ["ev_api"],
                            },
                        ],
                    }
                ]
            },
            retry_feedback="The prior draft invented the unsupported year 2026",
        )

        self.assertEqual(result.model, "synthetic-tailor")
        messages = session.messages[0]
        assert isinstance(messages, list)
        self.assertEqual(len(messages), 2)
        prompt = json.loads(messages[0].content.text)
        self.assertEqual(prompt["forbidden_numeric_tokens_from_prior_attempt"], ["2026"])
        self.assertIn("CORRECTION REQUIRED", messages[1].content.text)

    def test_rejects_duplicate_lead_verbs_across_ai_authored_projects(self) -> None:
        with self.assertRaisesRegex(ValueError, "reuses the lead verb 'Engineered'"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": "Engineered a Python API serving 100 users safely.",
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": "Engineered tests for 20 API routes and failures.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
