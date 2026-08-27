from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from erga_mcp.models import Evidence
from erga_mcp.portfolio.inventory import ProjectCandidate
from erga_mcp.resumes.ai_tailoring import (
    _FORBIDDEN_GIT_PROSE,
    ResumeStylePreferences,
    TailoringDraftRequest,
    TailoringDraftResponse,
    _latex_bullet_text,
    _latex_text,
    _master_bolds_project_metrics,
    _metric_claims,
    _normalized_number,
    _resume_quality_numbers,
    _resume_safe_approved_bullet,
    draft_evidence_backed_projects,
)
from erga_mcp.resumes.artifacts import resume_item_texts


class _SamplingSession:
    def __init__(self, submission: dict[str, object]) -> None:
        self.submission = submission
        self.calls: list[dict[str, Any]] = []
        self.messages: list[object] = []

    async def draft(self, request: TailoringDraftRequest) -> TailoringDraftResponse:
        self.calls.append(
            {
                "tools": request.tools,
                "tool_choice": "required",
                "include_context": "none",
                "system_prompt": request.system_prompt,
            }
        )
        self.messages.append(list(request.messages))
        return TailoringDraftResponse(
            submission=self.submission,
            model="synthetic-tailor",
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
            r"\resumeItem{Built a Python API serving 100 users safely with authenticated requests.}"
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
    def test_reapplies_the_master_project_metric_bolding_convention(self) -> None:
        with TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.tex"
            resume.write_text(
                r"""\begin{document}
\section{Projects}
\resumeItem{Won an award among \textbf{85 projects} in 2026.}
\section{Technical Skills}
Python
\end{document}
""",
                encoding="utf-8",
            )

            self.assertTrue(_master_bolds_project_metrics(resume))
            self.assertEqual(
                _latex_bullet_text(
                    "Built an API serving 100 users with 99.3% accuracy in 2027.",
                    bold_metric_tokens=True,
                ),
                (
                    r"Built an API serving \textbf{100 users} with "
                    r"\textbf{99.3\% accuracy} in 2027."
                ),
            )
            self.assertEqual(
                _latex_bullet_text(
                    "Added health telemetry for 40% faster incident detection.",
                    bold_metric_tokens=True,
                ),
                r"Added health telemetry for \textbf{40\% faster incident detection}.",
            )

    def test_numeric_normalization_ignores_sentence_punctuation_but_preserves_decimals(
        self,
    ) -> None:
        self.assertEqual(_normalized_number("2026."), "2026")
        self.assertEqual(_normalized_number("99.3%"), "99.3%")

    def test_raw_git_filter_allows_implementation_files_but_blocks_accounting(self) -> None:
        self.assertIsNone(_FORBIDDEN_GIT_PROSE.search("Generated typed configuration files."))
        self.assertIsNotNone(_FORBIDDEN_GIT_PROSE.search("Changed 12 files across 4 commits."))
        self.assertIsNotNone(
            _FORBIDDEN_GIT_PROSE.search("Engineered a CUDA CLI across 71 implementation files.")
        )

    def test_resume_quality_numbers_exclude_activity_counts_and_standalone_years(self) -> None:
        self.assertEqual(
            _resume_quality_numbers("Served 1,000+ members with 99.3% request accuracy."),
            frozenset({"1000+", "99.3%"}),
        )
        self.assertEqual(
            _resume_quality_numbers("Changed 71 implementation files in 2026."), frozenset()
        )
        self.assertEqual(_resume_quality_numbers("Built the service with Python 3."), frozenset())
        self.assertEqual(
            _resume_quality_numbers("Processed 2,000+ submissions across 5 API routes."),
            frozenset({"2000+", "5"}),
        )
        self.assertEqual(
            _resume_quality_numbers("Won 1st of 245 at a hackathon in 2026."),
            frozenset({"1", "245"}),
        )

    def test_model_typography_is_normalized_before_latex_rendering(self) -> None:
        self.assertEqual(
            _latex_text("Shipped ‘typed’ UI—under 30 ms."), "Shipped 'typed' UI-under 30 ms."
        )
        with self.assertRaisesRegex(ValueError, "ASCII"):
            _latex_text("Shipped an interface 🚀")

    def test_legacy_activity_clause_is_removed_without_losing_implementation_detail(self) -> None:
        self.assertEqual(
            _resume_safe_approved_bullet(
                "Evolved a Rust daemon across 16 Git commits and 57 files, totaling 3,186 lines."
            ),
            "Evolved a Rust daemon.",
        )

    def _draft(
        self,
        submission: dict[str, object],
        *,
        retry_feedback: str = "",
        required_project_ids: tuple[str, ...] = (),
        maximum_bullets: int = 2,
        minimum_bullets: int | None = None,
        style_preferences: ResumeStylePreferences | None = None,
        candidate: ProjectCandidate | None = None,
    ):
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
                r"\resumeItem{Built a template project for 10 users.}"
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
            metric = Evidence(
                id="ev_metric",
                source_ref="git-metric:api-platform@abc",
                text="Attributed work touched 3 implementation files and 2 test files.",
                approved=True,
                created_at=datetime.now(UTC),
            )
            scope = Evidence(
                id="ev_scope",
                source_ref="git-scope:api-platform@def",
                text=(
                    "Verified 5 distinct attributed test cases present in the current repository."
                ),
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
                    candidates=(candidate or _candidate(),),
                    evidence=[approved, git, metric, scope],
                    reports=(
                        {
                            "project_id": "api-platform",
                            "evidence_ids": ["ev_git", "ev_metric", "ev_scope"],
                            "repositories": [
                                {
                                    "repository": "example/api-platform",
                                    "metric_context": {
                                        "status": "verified",
                                        "attributed_changes_observed": True,
                                        "has_implementation_changes": True,
                                        "has_test_changes": True,
                                        "languages": ["Python"],
                                        "resume_metric_candidates": [
                                            "Attributed work touched 3 implementation files and "
                                            "2 test files."
                                        ],
                                        "resume_use": "draft_scale_evidence",
                                        "requires_user_confirmation": True,
                                    },
                                }
                            ],
                        },
                    ),
                    project_count=1,
                    bullets_per_project=maximum_bullets,
                    minimum_bullets_per_project=minimum_bullets,
                    bullet_min_chars=90,
                    bullet_target_chars=105,
                    bullet_max_chars=116,
                    require_unique_lead_verbs=True,
                    retry_feedback=retry_feedback,
                    required_project_ids=required_project_ids,
                    style_preferences=style_preferences,
                )
            )
            return result, session

    def test_model_returns_a_variable_bullet_pool_without_user_configuration(self) -> None:
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
            minimum_bullets=1,
            maximum_bullets=4,
        )

        self.assertEqual(len(result.candidates[0].bullet_evidence_ids), 2)
        schema = session.calls[0]["tools"][0].input_schema["properties"]["projects"]["items"][
            "properties"
        ]["bullets"]
        self.assertEqual(schema["minItems"], 1)
        self.assertEqual(schema["maxItems"], 4)

    def test_ranks_multiple_evidence_valid_variants_from_one_model_response(self) -> None:
        result, session = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": "Implemented a Python API serving 100 users.",
                                "evidence_ids": ["ev_api"],
                            },
                            {
                                "text": "Validated 20 API routes across request failures.",
                                "evidence_ids": ["ev_api"],
                            },
                        ],
                    }
                ],
                "alternatives": [
                    {
                        "projects": [
                            {
                                "project_id": "api-platform",
                                "bullets": [
                                    {
                                        "text": (
                                            "Engineered a Python API serving 100 users with "
                                            "authenticated request handling."
                                        ),
                                        "evidence_ids": ["ev_api"],
                                    },
                                    {
                                        "text": (
                                            "Validated 20 API routes across request validation "
                                            "and failure handling."
                                        ),
                                        "evidence_ids": ["ev_api"],
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        )

        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            resume_item_texts(result.candidates[0].latex)[0],
            "Engineered a Python API serving 100 users with authenticated request handling.",
        )
        self.assertEqual(result.quality_report["variant_selection"]["evaluated_count"], 2)
        self.assertEqual(result.quality_report["variant_selection"]["selected_index"], 1)

    def test_discards_one_invalid_optional_variant_without_aborting_valid_copy(self) -> None:
        result, _ = self._draft(
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
                ],
                "alternatives": [
                    {
                        "projects": [
                            {
                                "project_id": "api-platform",
                                "bullets": [
                                    {
                                        "text": "Built a Python API serving 999 users.",
                                        "evidence_ids": ["ev_api"],
                                    },
                                    {
                                        "text": "Validated 20 API routes across failures.",
                                        "evidence_ids": ["ev_api"],
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        )

        selection = result.quality_report["variant_selection"]
        self.assertEqual(selection["evaluated_count"], 1)
        self.assertEqual(selection["rejected_count"], 1)
        self.assertIn("number absent", selection["rejections"][0])

    def test_uses_valid_alternative_when_primary_variant_is_invalid(self) -> None:
        result, _ = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": "Built a Python API serving 999 users.",
                                "evidence_ids": ["ev_api"],
                            },
                            {
                                "text": "Validated 20 API routes across failures.",
                                "evidence_ids": ["ev_api"],
                            },
                        ],
                    }
                ],
                "alternatives": [
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
                    }
                ],
            }
        )

        selection = result.quality_report["variant_selection"]
        self.assertEqual(selection["evaluated_count"], 1)
        self.assertEqual(selection["selected_index"], 1)
        self.assertEqual(selection["rejected_count"], 1)

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
        self.assertEqual(session.calls[0]["tool_choice"], "required")
        self.assertEqual(session.calls[0]["include_context"], "none")
        bullet_schema = session.calls[0]["tools"][0].input_schema["properties"]["projects"][
            "items"
        ]["properties"]["bullets"]["items"]["properties"]
        self.assertEqual(bullet_schema["text"]["maxLength"], 116)
        self.assertEqual(bullet_schema["graph_path_id"], {"type": "string"})
        messages = session.messages[0]
        assert isinstance(messages, list)
        prompt = json.loads(messages[0].text)
        self.assertIn("Engineered", prompt["allowed_lead_verbs"])
        self.assertIn("Validated", prompt["allowed_lead_verbs"])
        self.assertEqual(prompt["projects"][0]["relevance_rank"], 1)
        self.assertIn("every eligible project", prompt["selection_objective"]["instruction"])
        self.assertEqual(
            prompt["selection_objective"]["priority_order"][0],
            "required-role coverage",
        )
        self.assertIn("identity_profile", prompt["projects"][0])
        self.assertIn("metric_categories", prompt["projects"][0]["identity_profile"])
        self.assertIn("portfolio_differentiators", prompt["projects"][0])
        evidence_graph = prompt["projects"][0]["evidence_graph"]
        self.assertEqual(evidence_graph["project_id"], "api-platform")
        self.assertGreater(len(evidence_graph["nodes"]), 0)
        self.assertGreater(len(evidence_graph["paths"]), 0)
        self.assertEqual(evidence_graph["paths"][0]["assembly_order"][0], "object")
        self.assertNotIn("sources", prompt["projects"][0])
        self.assertNotIn("edges", evidence_graph)
        self.assertIn("performance", prompt["preferred_metric_categories"])
        self.assertTrue(prompt["projects"][0]["git_engineering_signals"][0]["has_test_changes"])
        self.assertFalse(
            prompt["projects"][0]["git_engineering_signals"][0][
                "activity_metrics_allowed_in_resume"
            ]
        )
        self.assertNotIn("implementation files", json.dumps(prompt))
        self.assertIn("verified_git_functional_scope_evidence", json.dumps(prompt))
        self.assertEqual(prompt["master_project_quantitative_coverage_percent"], 100)
        self.assertEqual(prompt["required_quantified_bullets_per_project_at_minimum"], 1)
        provenance = result.quality_report["metric_provenance"]
        self.assertEqual(provenance[0]["value"], "100")
        self.assertEqual(provenance[0]["unit"], "users")
        self.assertEqual(provenance[0]["evidence_ids"], ["ev_api"])
        self.assertEqual(provenance[0]["basis"], "approved_exact_numeric_token")
        graph_quality = result.quality_report["evidence_graph_alignment"][0]
        self.assertGreater(graph_quality["graph"]["node_count"], 0)
        self.assertTrue(all(item["passed"] for item in graph_quality["bullets"]))

    def test_explicit_style_preferences_are_run_scoped_and_contain_no_personal_facts(self) -> None:
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
            style_preferences=ResumeStylePreferences(
                preferred_narratives=("developer tooling",),
                preferred_metric_categories=("reliability",),
            ),
        )

        self.assertEqual(result.model, "synthetic-tailor")
        prompt = json.loads(session.messages[0][0].text)
        self.assertEqual(prompt["style_preferences"]["scope"], "current_generation_only")
        self.assertEqual(
            prompt["style_preferences"]["preferred_metric_categories"], ["reliability"]
        )
        self.assertNotIn("evidence", json.dumps(prompt["style_preferences"]).casefold())

    def test_retry_can_lock_the_semantic_project_selection_during_copy_repair(self) -> None:
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
            retry_feedback="Shorten the same project bullets.",
            required_project_ids=("api-platform",),
        )

        self.assertEqual(result.candidates[0].id, "api-platform")
        prompt = json.loads(session.messages[0][0].text)
        self.assertEqual(prompt["required_project_ids"], ["api-platform"])
        self.assertIn("Do not substitute another project", session.calls[0]["system_prompt"])

    def test_rejects_a_retry_that_substitutes_a_locked_project(self) -> None:
        with self.assertRaisesRegex(ValueError, "preserve the required project selection"):
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
                                    "text": "Validated 20 API routes across request failures.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                },
                required_project_ids=("different-project",),
            )

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

    def test_rejects_reusing_approved_numbers_with_invented_units_or_meaning(self) -> None:
        with self.assertRaisesRegex(ValueError, "value/unit/context"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": "Engineered a Python API with 100 ms latency.",
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": "Validated request processing across 20 GPUs.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_rejects_metric_magnitude_and_rate_modifiers_absent_from_evidence(self) -> None:
        unsupported = (
            "Engineered a Python API serving 100 users per second.",
            "Engineered a Python API serving 100 million users.",
            "Engineered a Python API serving over 100 users.",
            "Engineered a Python API serving 100 users per API route.",
            "Engineered a Python API serving -100 users.",
            "Engineered a Python API serving +100 users.",
            "Engineered a Python API serving ~100 users.",
        )
        for text in unsupported:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "value/unit/context"):
                self._draft(
                    {
                        "projects": [
                            {
                                "project_id": "api-platform",
                                "bullets": [
                                    {"text": text, "evidence_ids": ["ev_api"]},
                                    {
                                        "text": "Validated 20 API routes across request failures.",
                                        "evidence_ids": ["ev_api"],
                                    },
                                ],
                            }
                        ]
                    }
                )

    def test_rejects_lead_verbs_that_assert_unsupported_delivery_or_ownership(self) -> None:
        with self.assertRaisesRegex(ValueError, "(?:lead verb.*stronger|implementation claims)"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Launched a Python API serving 100 users safely with "
                                        "authenticated requests."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": (
                                        "Architected 20 API routes covering request validation "
                                        "and failures."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_metric_rate_qualifiers_do_not_leak_across_multiple_metrics(self) -> None:
        for text, qualifier in (
            ("Served 100 users and handled 20 API routes per day.", "per-day"),
            ("Served 100 users and handled 20 requests per second.", "per-second"),
        ):
            with self.subTest(text=text):
                claims = _metric_claims(text)
                self.assertTrue(all(qualifier not in signature[0] for signature in claims["100"]))
                self.assertTrue(any(qualifier in signature[0] for signature in claims["20"]))

    def test_rejects_technologies_and_implementation_claims_absent_from_cited_evidence(
        self,
    ) -> None:
        unsupported = (
            (
                "Engineered Kubernetes clusters serving 100 users with zero-downtime autoscaling.",
                "technologies absent.*Kubernetes",
            ),
            (
                "Engineered a Python API serving 100 users with zero-downtime autoscaling.",
                "implementation claims absent",
            ),
        )
        for text, error in unsupported:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                self._draft(
                    {
                        "projects": [
                            {
                                "project_id": "api-platform",
                                "bullets": [
                                    {"text": text, "evidence_ids": ["ev_api"]},
                                    {
                                        "text": "Validated 20 API routes across request failures.",
                                        "evidence_ids": ["ev_api"],
                                    },
                                ],
                            }
                        ]
                    }
                )

    def test_rejects_unseen_qualitative_claim_atoms_outside_a_finite_denylist(self) -> None:
        unsupported_variants = (
            (
                "Engineered an event-driven Python API serving 100 users with "
                "end-to-end ownership.",
                "Validated 20 API routes through chaos engineering and property-based testing.",
            ),
            (
                "Architected a multi-tenant Python API serving 100 users with zero data loss.",
                "Validated 20 API routes using schema migrations and blue-green deployments.",
            ),
        )
        for first, second in unsupported_variants:
            with (
                self.subTest(first=first),
                self.assertRaisesRegex(ValueError, "implementation claims absent"),
            ):
                self._draft(
                    {
                        "projects": [
                            {
                                "project_id": "api-platform",
                                "bullets": [
                                    {"text": first, "evidence_ids": ["ev_api"]},
                                    {"text": second, "evidence_ids": ["ev_api"]},
                                ],
                            }
                        ]
                    }
                )

    def test_rejects_negation_that_inverts_approved_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "changes the polarity"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users without "
                                        "authenticated requests."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": (
                                        "Validated 20 API routes without request validation "
                                        "and failures."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_unrelated_negated_clause_does_not_authorize_claim_inversion(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Tested a client without external services.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "changes the polarity"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users without "
                                        "authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_negated_detail_from_another_subject_does_not_transfer_to_api(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Tested a client without external services.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "changes the polarity"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users without "
                                        "external services."
                                    ),
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
                candidate=candidate,
            )

    def test_rejects_removing_negation_from_an_approved_claim(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests and was not shipped.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "changes the polarity"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": "Shipped a Python API serving 100 users safely.",
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
                candidate=candidate,
            )

    def test_rejects_unsupported_standalone_year(self) -> None:
        with self.assertRaisesRegex(ValueError, "number absent.*2027"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users safely in 2027."
                                    ),
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

    def test_year_from_an_unrelated_claim_does_not_transfer_to_api(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Won an award in 2027.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "supported claim context.*2027"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users safely in 2027."
                                    ),
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
                candidate=candidate,
            )

    def test_failed_behavior_cannot_be_rewritten_as_success(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "with authenticated requests.",
                    "but failed to authenticate requests.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "changes the polarity"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users safely with "
                                        "authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_same_metric_on_two_components_does_not_merge_their_technologies(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Built a separate Kafka benchmark serving 100 users.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "technologies absent.*Kafka"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python Kafka API serving 100 users safely "
                                        "with authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_api_metric_does_not_transfer_to_a_separate_benchmark_component(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Built a separate Kafka benchmark tool.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "technologies absent|implementation claims"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Kafka benchmark tool serving 100 users "
                                        "safely."
                                    ),
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
                candidate=candidate,
            )

    def test_same_component_clauses_can_synthesize_one_supported_bullet(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "with authenticated requests.",
                    "safely. Implemented authenticated requests for the API.",
                ),
            }
        )
        result, _ = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": (
                                    "Engineered a Python API serving 100 users safely with "
                                    "authenticated requests."
                                ),
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
            candidate=candidate,
        )

        self.assertIn("authenticated requests", result.candidates[0].latex)

    def test_repeated_unlisted_component_can_synthesize_supported_clauses(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "Python API serving 100 users safely with authenticated requests.",
                    (
                        "Python agent serving 100 users safely. Implemented authenticated "
                        "requests for the agent."
                    ),
                ),
            }
        )
        result, _ = self._draft(
            {
                "projects": [
                    {
                        "project_id": "api-platform",
                        "bullets": [
                            {
                                "text": (
                                    "Engineered a Python agent serving 100 users safely with "
                                    "authenticated requests."
                                ),
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
            candidate=candidate,
        )

        self.assertIn("Python agent serving 100 users", result.candidates[0].latex)

    def test_shared_auth_detail_does_not_fuse_separate_components(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "with authenticated requests.",
                    "with authenticated requests. Built a separate Kafka benchmark with "
                    "authenticated requests.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "technologies absent|implementation claims"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python Kafka API serving 100 users safely "
                                        "with authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_project_level_technology_does_not_transfer_between_components(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Built a separate Kafka benchmark tool.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "technologies absent.*Kafka"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python Kafka API serving 100 users safely "
                                        "with authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_unrelated_launched_clause_does_not_authorize_stronger_api_lead(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests. Launched a documentation website.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "(?:lead verb.*stronger|implementation claims)"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Launched a Python API serving 100 users safely with "
                                        "authenticated requests."
                                    ),
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
                candidate=candidate,
            )

    def test_unrelated_launched_conjunct_does_not_authorize_stronger_api_lead(self) -> None:
        candidate = _candidate()
        candidate = ProjectCandidate(
            **{
                **candidate.__dict__,
                "latex": candidate.latex.replace(
                    "authenticated requests.",
                    "authenticated requests and launched a documentation website.",
                ),
            }
        )
        with self.assertRaisesRegex(ValueError, "(?:lead verb.*stronger|implementation claims)"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Launched a Python API serving 100 users safely with a "
                                        "documentation website."
                                    ),
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
                candidate=candidate,
            )

    def test_rejects_metric_free_copy_below_master_quantitative_coverage(self) -> None:
        with self.assertRaisesRegex(ValueError, "quantitative bullet coverage"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": "Engineered a Python API with authenticated requests.",
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": "Validated API routes across request failures.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_rejects_a_factually_supported_bullet_with_a_tacked_on_claim(self) -> None:
        with self.assertRaisesRegex(ValueError, "cohesion.tacked_claim"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users; built "
                                        "authenticated request handling."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                                {
                                    "text": (
                                        "Validated 20 API routes covering request validation and "
                                        "failures."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_rejects_a_model_selected_graph_path_that_does_not_exist(self) -> None:
        with self.assertRaisesRegex(ValueError, "graph.unknown_path"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API serving 100 users with "
                                        "authenticated requests."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                    "graph_path_id": "bg_not_real",
                                },
                                {
                                    "text": (
                                        "Validated 20 API routes covering request validation and "
                                        "failures."
                                    ),
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_rejects_legacy_git_activity_metrics_for_master_parity(self) -> None:
        with self.assertRaisesRegex(ValueError, "forbidden Git-accounting phrase"):
            self._draft(
                {
                    "projects": [
                        {
                            "project_id": "api-platform",
                            "bullets": [
                                {
                                    "text": (
                                        "Engineered a Python API across 71 implementation files."
                                    ),
                                    "evidence_ids": ["ev_metric", "ev_api"],
                                },
                                {
                                    "text": (
                                        "Validated authenticated requests across 2 test files."
                                    ),
                                    "evidence_ids": ["ev_metric", "ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )

    def test_accepts_git_verified_functional_scope_with_an_approved_outcome(self) -> None:
        result, _ = self._draft(
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
                                "text": "Validated 5 distinct attributed test cases.",
                                "evidence_ids": ["ev_scope", "ev_git"],
                            },
                        ],
                    }
                ]
            }
        )

        self.assertEqual(
            resume_item_texts(result.candidates[0].latex)[1],
            "Validated 5 distinct attributed test cases.",
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
        prompt = json.loads(messages[0].text)
        self.assertEqual(prompt["forbidden_numeric_tokens_from_prior_attempt"], ["2026"])
        self.assertIn("CORRECTION REQUIRED", messages[1].text)

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

    def test_rejects_name_swappable_bullets_even_when_lead_verbs_differ(self) -> None:
        with self.assertRaisesRegex(ValueError, "semantically interchangeable"):
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
                                    "text": "Developed a Python API serving 100 users safely.",
                                    "evidence_ids": ["ev_api"],
                                },
                            ],
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
