from __future__ import annotations

import asyncio
import json
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from erga_mcp.ai_resume_tailoring import AIProjectTailoring
from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.git_project_enrichment import GitProjectEnrichment
from erga_mcp.mcp_server import (
    IntakeJobResult,
    IntakeValidationResult,
    _ai_research_shortlist_ids,
    _ai_tailored_project_enrichment,
    _compile_intake_proposal,
    _layout_safe_project_selection,
    _metadata_from_url,
    _require_constraint_valid_proposal,
    _require_master_template_parity,
    build_server,
    build_streamable_http_app,
)
from erga_mcp.models import Evidence
from erga_mcp.project_inventory import ProjectCandidate
from erga_mcp.resume import LatexValidation, ResumeItemLayoutValidation
from erga_mcp.store import ErgaStore


class McpServerTests(unittest.TestCase):
    def test_application_tracker_returns_complete_paginated_cross_cycle_results(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker_dir = root / "trackers"
            tracker_dir.mkdir()
            header = (
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                header + "| Alpha | Engineer | Remote | [Job](https://example.test/a) | "
                "Applied | | Wait | Note |\n"
                "| Beta | Engineer | Remote | [Job](https://example.test/b) | "
                "OA | | Test | Note |\n",
                encoding="utf-8",
            )
            (tracker_dir / "Summer 2027 Applications.md").write_text(
                header + "| Gamma | Engineer | Remote | [Job](https://example.test/c) | "
                "Draft | | Apply | Note |\n"
                "| Delta | Engineer | Remote | [Job](https://example.test/d) | "
                "Researching | | Review | Note |\n"
                "| Epsilon | Engineer | Remote | [Job](https://example.test/e) | "
                "Applied | | Wait | Note |\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    f'enabled = true\ntracker_dir = "{tracker_dir}"',
                    1,
                ),
                encoding="utf-8",
            )
            server = build_server(config_path)
            tool = server._tool_manager.get_tool("application_tracker")

            result = tool.fn(query="all", page=2, page_size=2)

        self.assertEqual(result["total_entries"], 5)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["page_count"], 3)
        self.assertTrue(result["has_previous"])
        self.assertTrue(result["has_next"])
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["summary"]["assessment"], 1)
        self.assertIn("Page 2 of 3 · showing 3-4 of 5 roles.", result["message"])
        self.assertNotIn("Search: all", result["message"])

    def test_ai_shortlist_ranks_projects_without_rejecting_their_old_bullet_wording(self) -> None:
        def candidate(project_id: str, tags: tuple[str, ...], bullet: str) -> ProjectCandidate:
            return ProjectCandidate(
                id=project_id,
                title=project_id.replace("-", " ").title(),
                latex=(
                    rf"\resumeProjectHeading{{\textbf{{{project_id}}}}}{{}}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    rf"\resumeItem{{{bullet}}}"
                    "\n"
                    r"\resumeItem{Approved second source bullet.}"
                    "\n"
                    r"\resumeItemListEnd"
                ),
                evidence_ids=(f"ev_{project_id}",),
                bullet_evidence_ids=((f"ev_{project_id}",), (f"ev_{project_id}",)),
                tags=tags,
            )

        candidates = (
            candidate(
                "api-runtime",
                ("python", "fastapi", "api"),
                "Analyzed 8 commits and 12 files from Git history.",
            ),
            candidate("frontend", ("react", "css"), "Built an approved interface."),
            candidate("robotics", ("c++", "ros2"), "Created an approved controller."),
        )

        selected = _ai_research_shortlist_ids(
            candidates,
            job_description="Required: Python FastAPI API engineering",
            project_count=1,
            minimum_bullets=2,
        )

        self.assertEqual(selected[0], "api-runtime")
        self.assertEqual(len(selected), 2)

    def test_ai_layout_retry_lowers_the_hard_cap_and_disables_the_soft_minimum(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "resume.tex"
            resume.write_text("synthetic resume", encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', f'template_path = "{resume}"')
                .replace("project_count = 4", "project_count = 1")
                .replace("bullet_min_chars = 0", "bullet_min_chars = 99")
                .replace("bullet_target_chars = 0", "bullet_target_chars = 105")
                .replace("bullet_max_chars = 0", "bullet_max_chars = 116"),
                encoding="utf-8",
            )
            config = load_config(config_path)
            evidence = Evidence(
                "ev_api",
                "approved:api",
                "Approved API evidence.",
                True,
                datetime.now(UTC),
            )
            candidate = ProjectCandidate(
                id="api",
                title="API",
                latex="synthetic project",
                evidence_ids=(evidence.id,),
                bullet_evidence_ids=((evidence.id,), (evidence.id,)),
                tags=("python", "api"),
            )
            enrichment = GitProjectEnrichment(
                candidates=(candidate,),
                evidence=(),
                reports=({"project_id": "api", "title": "API", "evidence_ids": []},),
                warnings=(),
                catalogue_candidate_count=1,
            )
            drafted = AIProjectTailoring((candidate,), "synthetic-tailor", (evidence.id,))
            plan = SimpleNamespace(selected=(candidate,), quality_rejections=())

            with (
                patch(
                    "erga_mcp.mcp_server.draft_evidence_backed_projects",
                    new=AsyncMock(return_value=drafted),
                ) as draft,
                patch("erga_mcp.mcp_server.plan_resume_project_selection", return_value=plan),
                patch(
                    "erga_mcp.mcp_server._layout_safe_project_selection",
                    side_effect=[
                        (("api",), ({"id": "api", "title": "API", "reasons": ["wrap"]},)),
                        (("api",), ()),
                    ],
                ),
            ):
                result = asyncio.run(
                    _ai_tailored_project_enrichment(
                        ctx=SimpleNamespace(session=object(), request_id="request-1"),
                        config=config,
                        resume_path=resume,
                        job_description="Required: Python API",
                        evidence=[evidence],
                        enrichment=enrichment,
                    )
                )

            self.assertEqual(
                result.reports[0]["resume_bullets_source"], "host_model_evidence_synthesis"
            )
            self.assertEqual(
                [call.kwargs["bullet_max_chars"] for call in draft.await_args_list],
                [116, 106],
            )
            self.assertEqual(
                [call.kwargs["bullet_min_chars"] for call in draft.await_args_list],
                [99, 0],
            )

    def test_layout_fallback_rejects_a_wrapped_project_and_selects_the_next_candidate(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "resume.tex"
            resume.write_text(
                r"\newcommand{\resumeSubHeadingListStart}{\begin{itemize}}"
                "\n"
                r"\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}"
                "\n"
                r"\newcommand{\resumeItemListStart}{\begin{itemize}}"
                "\n"
                r"\newcommand{\resumeItemListEnd}{\end{itemize}}"
                "\n"
                r"\newcommand{\resumeProjectHeading}[2]{\item #1}"
                "\n"
                r"\newcommand{\resumeItem}[1]{\item #1}"
                "\n"
                r"\begin{document}"
                "\n"
                r"\section{Projects}"
                "\n"
                r"\resumeSubHeadingListStart"
                "\n"
                r"\resumeProjectHeading{\textbf{Template}}{}"
                "\n"
                r"\resumeItemListStart"
                "\n"
                r"\resumeItem{Template project bullet.}"
                "\n"
                r"\resumeItemListEnd"
                "\n"
                r"\resumeSubHeadingListEnd"
                "\n"
                r"\end{document}"
                "\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', f'template_path = "{resume}"')
                .replace("editable_sections = []", 'editable_sections = ["projects"]')
                .replace("bullet_min_chars = 0", "bullet_min_chars = 1")
                .replace("bullet_target_chars = 0", "bullet_target_chars = 60")
                .replace("bullet_max_chars = 0", "bullet_max_chars = 116")
                .replace("project_count = 4", "project_count = 1"),
                encoding="utf-8",
            )
            config = load_config(config_path)
            evidence = Evidence(
                id="ev_project",
                source_ref="synthetic:test",
                text="Approved synthetic project evidence.",
                approved=True,
                created_at=datetime.now(UTC),
            )

            def candidate(project_id: str, title: str) -> ProjectCandidate:
                return ProjectCandidate(
                    id=project_id,
                    title=title,
                    latex=(
                        rf"\resumeProjectHeading{{\textbf{{{title}}} $|$ \textit{{Python}}}}{{}}"
                        "\n"
                        r"\resumeItemListStart"
                        "\n"
                        r"\resumeItem{Built a Python service from approved synthetic evidence.}"
                        "\n"
                        r"\resumeItemListEnd"
                        "\n"
                    ),
                    evidence_ids=(evidence.id,),
                    bullet_evidence_ids=((evidence.id,),),
                    tags=("python",),
                )

            layouts = (
                ResumeItemLayoutValidation(("latexmk",), 0, 1, (0,), "", ""),
                ResumeItemLayoutValidation(("latexmk",), 0, 1, (), "", ""),
            )
            with patch(
                "erga_mcp.mcp_server.validate_single_line_resume_items",
                side_effect=layouts,
            ) as validate:
                selected, rejections = _layout_safe_project_selection(
                    resume_path=resume,
                    output_dir=root / "output",
                    job_description="Python service engineering",
                    evidence=[evidence],
                    project_candidates=(
                        candidate("alpha-wrap", "Alpha Wrap"),
                        candidate("beta-safe", "Beta Safe"),
                    ),
                    config=config,
                )

            self.assertEqual(selected, ("beta-safe",))
            self.assertEqual(rejections[0]["id"], "alpha-wrap")
            self.assertEqual(validate.call_count, 2)

    def test_master_resume_must_match_the_template_when_a_master_is_configured(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            template = root / "template.tex"
            master.write_text("Master résumé", encoding="utf-8")
            template.write_text("Different résumé", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "does not match the configured master"):
                _require_master_template_parity(master_path=master, template_path=template)

    def test_constraint_fallback_proposals_are_rejected_before_a_resume_is_compiled(self) -> None:
        automatic = SimpleNamespace(constraint_violations=("duplicate lead verb 'built'",))
        with self.assertRaisesRegex(ValueError, "duplicate lead verb 'built'"):
            _require_constraint_valid_proposal(automatic)

    def test_modern_streamable_http_discovery_is_stateless_and_origin_guarded(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            app = build_streamable_http_app(build_server(config_path))
            request = {
                "jsonrpc": "2.0",
                "id": "discover-1",
                "method": "server/discover",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "Erga protocol test",
                            "version": "1.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
            headers = {
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "server/discover",
                "Mcp-Name": "",
            }
            tools_request = {
                "jsonrpc": "2.0",
                "id": "tools-1",
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientInfo": {
                            "name": "Erga protocol test",
                            "version": "1.0",
                        },
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                response = client.post("/mcp", json=request, headers=headers)
                tools_response = client.post(
                    "/mcp",
                    json=tools_request,
                    headers={**headers, "Mcp-Method": "tools/list"},
                )
                browser_response = client.post(
                    "/mcp", json=request, headers={**headers, "Origin": "https://evil.test"}
                )
                loopback_host_responses = {
                    host: client.post("/mcp", json=request, headers={**headers, "Host": host})
                    for host in {
                        "127.0.0.1",
                        "127.0.0.1:8765",
                        "localhost",
                        "localhost:8765",
                        "[::1]",
                        "[::1]:8765",
                    }
                }
                hostile_host_response = client.post(
                    "/mcp", json=request, headers={**headers, "Host": "evil.test"}
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "Erga MCP",
        )
        self.assertIn("2026-07-28", payload["result"]["supportedVersions"])
        self.assertEqual(payload["result"]["ttlMs"], 60_000)
        self.assertEqual(payload["result"]["cacheScope"], "private")
        self.assertEqual(tools_response.status_code, 200)
        tools_payload = tools_response.json()["result"]
        self.assertEqual(tools_payload["ttlMs"], 60_000)
        self.assertEqual(tools_payload["cacheScope"], "private")
        self.assertNotIn("Mcp-Session-Id", tools_response.headers)
        tool_names = {tool["name"] for tool in tools_payload["tools"]}

        self.assertIn("erga_capabilities", tool_names)
        self.assertEqual(browser_response.status_code, 403)
        for host, host_response in loopback_host_responses.items():
            with self.subTest(host=host):
                self.assertEqual(host_response.status_code, 200)
        self.assertEqual(hostile_host_response.status_code, 421)

    def test_legacy_streamable_http_is_stateless_and_host_guarded(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            app = build_streamable_http_app(build_server(config_path))
            initialize_request = {
                "jsonrpc": "2.0",
                "id": "legacy-initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-fixture", "version": "1.0"},
                },
            }
            list_request = {
                "jsonrpc": "2.0",
                "id": "legacy-list-tools",
                "method": "tools/list",
                "params": {},
            }
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                initialized = client.post(
                    "/mcp",
                    json=initialize_request,
                    headers={"Mcp-Method": "initialize", "Host": "127.0.0.1"},
                )
                listed = client.post(
                    "/mcp",
                    json=list_request,
                    headers={"Mcp-Method": "tools/list", "Host": "127.0.0.1"},
                )
                hostile = client.post(
                    "/mcp",
                    json=list_request,
                    headers={"Mcp-Method": "tools/list", "Host": "evil.test"},
                )

        self.assertEqual(initialized.status_code, 200)
        self.assertIn('"protocolVersion":"2025-06-18"', initialized.text)
        self.assertNotIn("Mcp-Session-Id", initialized.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn('"tools"', listed.text)
        self.assertNotIn("Mcp-Session-Id", listed.headers)
        self.assertEqual(hostile.status_code, 421)

    def test_modern_review_prompt_requires_explicit_save_before_changing_a_draft(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(Path(directory) / "state" / "erga.sqlite3")
            draft = store.add_manual_git_research_draft(
                title="Offline planner",
                description="Built a local-first planning tool.",
            )
            app = build_streamable_http_app(build_server(config_path))
            headers = {
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "review_git_draft_prompt",
            }
            params = {
                "name": "review_git_draft_prompt",
                "arguments": {"draft_id": draft.id},
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "Erga protocol test",
                        "version": "1.0",
                    },
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            }
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                initial = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "review-1",
                        "method": "tools/call",
                        "params": params,
                    },
                    headers=headers,
                )
                initial_result = initial.json()["result"]
                self.assertEqual(
                    store.review_git_research_draft(action="show", draft_id=draft.id)[
                        0
                    ].review_status,
                    "pending",
                )
                tampered = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "review-tampered",
                        "method": "tools/call",
                        "params": {
                            **params,
                            "inputResponses": {
                                "review_decision": {
                                    "action": "accept",
                                    "content": {"decision": "save"},
                                }
                            },
                            "requestState": initial_result["requestState"] + "tampered",
                        },
                    },
                    headers=headers,
                )
                self.assertEqual(tampered.status_code, 400)
                self.assertEqual(tampered.json()["error"]["code"], -32602)
                self.assertEqual(
                    store.review_git_research_draft(action="show", draft_id=draft.id)[
                        0
                    ].review_status,
                    "pending",
                )
                resumed = client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": "review-2",
                        "method": "tools/call",
                        "params": {
                            **params,
                            "inputResponses": {
                                "review_decision": {
                                    "action": "accept",
                                    "content": {"decision": "save"},
                                }
                            },
                            "requestState": initial_result["requestState"],
                        },
                    },
                    headers=headers,
                )

        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial_result["resultType"], "input_required")
        self.assertEqual(
            initial_result["inputRequests"]["review_decision"]["params"]["requestedSchema"][
                "properties"
            ]["decision"]["enum"],
            ["save", "skip"],
        )
        resumed_result = resumed.json()["result"]["structuredContent"]
        self.assertEqual(resumed_result["draft"]["review_status"], "saved")
        self.assertFalse(resumed_result["evidence_approved"])
        self.assertFalse(resumed_result["resume_changed"])

    def test_review_tool_adds_and_saves_manual_draft_without_approving_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)

            added_result: Any = asyncio.run(
                server.call_tool(
                    "review_git_drafts",
                    {
                        "action": "add",
                        "title": "Personal finance tracker",
                        "description": "Built an offline budgeting application.",
                    },
                )
            )
            added = cast(dict[str, Any], added_result.structured_content)
            saved_result: Any = asyncio.run(
                server.call_tool(
                    "review_git_drafts",
                    {"action": "save", "draft_id": added["draft"]["id"]},
                )
            )
            saved = cast(dict[str, Any], saved_result.structured_content)

        self.assertEqual(added["draft"]["source"], "manual")
        self.assertEqual(added["draft"]["title"], "Personal finance tracker")
        self.assertEqual(saved["draft"]["review_status"], "saved")
        self.assertFalse(saved["evidence_approved"])
        self.assertFalse(saved["resume_changed"])

    def test_resume_source_context_exposes_master_but_not_style_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            style = root / "style.tex"
            master.write_text("Complete factual master resume", encoding="utf-8")
            style.write_text("Education\nExperience\nProjects", encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'master_path = ""',
                    'master_path = "master.tex"',
                ).replace(
                    'reference_path = ""',
                    'reference_path = "style.tex"',
                ),
                encoding="utf-8",
            )
            server = build_server(config_path)

            result: Any = asyncio.run(server.call_tool("resume_source_context", {}))
            payload = cast(dict[str, Any], result.structured_content)

            self.assertEqual(payload["master"]["text"], "Complete factual master resume")
            self.assertTrue(payload["master"]["user_approved_source"])
            self.assertNotIn("path", payload["master"])
            self.assertNotIn("text", payload["style_reference"])
            self.assertEqual(
                payload["style_reference"]["observed_section_order"],
                ["Education", "Experience", "Projects"],
            )

    def test_git_research_tool_returns_redacted_provenance_for_explicit_local_roots(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            repo = root / "projects" / "sample-repo"
            repo.mkdir(parents=True)
            for arguments in (
                ("init",),
                ("config", "user.email", "test@example.test"),
                ("config", "user.name", "Test User"),
            ):
                subprocess.run(["git", *arguments], cwd=repo, check=True, capture_output=True)
            source = repo / "src" / "research" / "routes.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "def create_research_job():\n    return {'route': 'POST /jobs/research'}\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "updates"], cwd=repo, check=True, capture_output=True
            )
            commit_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            server = build_server(config_path)

            result: Any = asyncio.run(
                server.call_tool("research_git_worktrees", {"roots": [str(root / "projects")]})
            )
            payload = cast(dict[str, Any], result.structured_content)

        self.assertEqual(payload["repositories_scanned"], 1)
        self.assertEqual(payload["observations_created"], 1)
        self.assertEqual(payload["research_drafts"], 1)
        self.assertFalse(payload["auto_approved"])
        self.assertEqual(len(payload["drafts"]), 1)
        draft = payload["drafts"][0]
        self.assertEqual(draft["source_commit_shas"], [commit_sha])
        self.assertEqual(draft["source_files"], ["src/research/routes.py"])
        self.assertTrue(draft["diff_hashes"])
        rendered = json.dumps(payload)
        self.assertNotIn("def create_research_job", rendered)
        self.assertNotIn("POST /jobs/research", rendered)
        self.assertNotIn("summary", draft)
        self.assertNotIn("bullet_candidates", draft)

    def test_git_research_tool_requires_existing_explicit_roots(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)

            with self.assertRaisesRegex(Exception, "at least one explicit local root"):
                asyncio.run(server.call_tool("research_git_worktrees", {"roots": []}))
            with self.assertRaisesRegex(Exception, "existing directory"):
                asyncio.run(
                    server.call_tool(
                        "research_git_worktrees", {"roots": [str(Path(directory) / "missing")]}
                    )
                )

    def test_opaque_ats_ids_produce_stable_distinct_package_slugs(self) -> None:
        first = _metadata_from_url(
            "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000001",
            cycle="fall-2026",
            application_slug="",
        )
        second = _metadata_from_url(
            "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000002",
            cycle="fall-2026",
            application_slug="",
        )

        self.assertEqual(first[0], "fall-2026")
        self.assertEqual(second[0], "fall-2026")
        self.assertNotEqual(first[1], second[1])
        self.assertRegex(first[1], r"example-job-opportunity-[0-9a-f]{16}$")
        self.assertRegex(second[1], r"example-job-opportunity-[0-9a-f]{16}$")

    def test_query_posting_ids_and_long_roles_keep_distinct_slug_suffixes(self) -> None:
        first = _metadata_from_url(
            "https://www.indeed.com/viewjob?jk=posting-one&utm_source=chat",
            cycle="",
            application_slug="",
        )
        second = _metadata_from_url(
            "https://www.indeed.com/viewjob?jk=posting-two&utm_source=chat",
            cycle="",
            application_slug="",
        )
        long_role = _metadata_from_url(
            "https://careers.example.test/jobs/"
            + "principal-software-engineer-for-real-time-distributed-audio-systems-" * 3,
            cycle="",
            application_slug="",
        )

        self.assertEqual(first[0], "unsorted")
        self.assertNotEqual(first[1], second[1])
        self.assertRegex(first[1], r"indeed-job-opportunity-[0-9a-f]{16}$")
        self.assertRegex(second[1], r"indeed-job-opportunity-[0-9a-f]{16}$")
        self.assertLessEqual(len(long_role[1]), 80)
        self.assertRegex(long_role[1], r"-[0-9a-f]{16}$")

    def test_rejects_coerced_boolean_token_counts_at_the_mcp_boundary(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)
            store = ErgaStore(Path(directory) / "state" / "erga.sqlite3")
            application = store.create_application(
                company="Example",
                role="Engineer",
                source_url="https://example.test/job",
                evidence_ids=[],
            )

            with self.assertRaises(Exception):
                asyncio.run(
                    server.call_tool(
                        "record_token_usage",
                        {
                            "application_id": application.id,
                            "operation": "test",
                            "input_tokens": True,
                            "output_tokens": 3,
                        },
                    )
                )

    def test_exposes_read_and_explicit_local_workspace_tools(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            server = build_server(config_path)
            tools = asyncio.run(server.list_tools())

            by_name = {tool.name: tool for tool in tools}
            self.assertEqual(
                set(by_name),
                {
                    "erga_capabilities",
                    "pipeline_status",
                    "list_applications",
                    "update_application_status",
                    "application_tracker",
                    "list_evidence",
                    "resume_source_context",
                    "list_mail_events",
                    "token_usage",
                    "record_token_usage",
                    "sync_recruiting_mail",
                    "intake_job_url",
                    "install_mail_monitor_scripts",
                    "export_data",
                    "record_secondary_research",
                    "discover_job_research",
                    "scrape_public_page",
                    "extract_public_page",
                    "create_research_brief",
                    "record_deep_research",
                    "prepare_job_workspace",
                    "create_tailored_resume",
                    "cover_letter_style_context",
                    "create_cover_letter",
                    "validate_tailored_resume",
                    "research_git_worktrees",
                    "review_git_drafts",
                    "review_git_draft_prompt",
                },
            )
            for name in {
                "pipeline_status",
                "list_applications",
                "application_tracker",
                "list_evidence",
                "resume_source_context",
                "list_mail_events",
            }:
                annotations = by_name[name].annotations
                self.assertIsNotNone(annotations)
                assert annotations is not None
                self.assertTrue(annotations.read_only_hint)
                self.assertFalse(annotations.open_world_hint)
            workspace_annotations = by_name["prepare_job_workspace"].annotations
            status_annotations = by_name["update_application_status"].annotations
            self.assertIsNotNone(status_annotations)
            assert status_annotations is not None
            self.assertFalse(status_annotations.read_only_hint)
            self.assertTrue(status_annotations.idempotent_hint)
            self.assertFalse(status_annotations.open_world_hint)
            mail_sync_annotations = by_name["sync_recruiting_mail"].annotations
            resume_annotations = by_name["create_tailored_resume"].annotations
            validation_annotations = by_name["validate_tailored_resume"].annotations
            assert workspace_annotations is not None
            assert mail_sync_annotations is not None
            assert resume_annotations is not None
            assert validation_annotations is not None
            self.assertFalse(workspace_annotations.read_only_hint)
            self.assertTrue(workspace_annotations.open_world_hint)
            self.assertFalse(mail_sync_annotations.read_only_hint)
            self.assertTrue(mail_sync_annotations.open_world_hint)
            self.assertFalse(resume_annotations.read_only_hint)
            self.assertFalse(validation_annotations.read_only_hint)

    def test_rejects_resume_validation_outside_configured_package_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            external = root / "external.tex"
            external.write_text("\\\\begin{document}outside\\\\end{document}\n", encoding="utf-8")

            server = build_server(config_path)
            with self.assertRaises(Exception) as error:
                asyncio.run(
                    server.call_tool("validate_tailored_resume", {"proposal_tex": str(external)})
                )
            self.assertIn("inside configured resume output_root", str(error.exception))

    def test_create_tailored_resume_softens_configured_minimum_bullet_length(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    "editable_sections = []", 'editable_sections = ["Experience"]'
                )
                .replace("bullet_min_chars = 0", "bullet_min_chars = 90")
                .replace("bullet_target_chars = 0", "bullet_target_chars = 105")
                .replace("bullet_max_chars = 0", "bullet_max_chars = 120"),
                encoding="utf-8",
            )
            package = root / "output" / "fall-2026" / "example"
            (package / "source").mkdir(parents=True)
            (package / "artifacts").mkdir()
            (package / "source" / "resume.tex").write_text(
                "\\section{Experience}\nexisting\n", encoding="utf-8"
            )
            evidence = ErgaStore(root / "state" / "erga.sqlite3").add_evidence(
                source_ref="approved", text="Verified outcome", approved=True
            )
            server = build_server(config_path)

            call: Any = asyncio.run(
                server.call_tool(
                    "create_tailored_resume",
                    {
                        "package_dir": str(package),
                        "section": "Experience",
                        "latex_content": "\\resumeItem{Too short}",
                        "evidence_ids": [evidence.id],
                    },
                )
            )

            result = cast(dict[str, Any], call.structured_content)
            report = json.loads(Path(result["claim_report"]).read_text(encoding="utf-8"))
            lengths = report["constraints"]["bullet_characters"]
            self.assertTrue(lengths["passed"])
            self.assertEqual(lengths["violations"], [])
            self.assertEqual(lengths["soft_deviations"][0]["length"], 9)
            self.assertTrue((package / "artifacts" / "proposal.tex").exists())

    def test_scrape_tools_return_bounded_untrusted_content(self) -> None:
        from erga_mcp.web_scraping import ScrapedPage

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)
            scraped = ScrapedPage(
                url="https://example.com/report",
                title="Public report",
                text="Bounded research text",
                links=("https://example.com/more",),
            )
            with (
                patch("erga_mcp.mcp_server.scrape_page", return_value=scraped),
                patch("erga_mcp.mcp_server.extract_page", return_value="Selected fact"),
            ):
                page: Any = asyncio.run(
                    server.call_tool("scrape_public_page", {"url": scraped.url})
                )
                section: Any = asyncio.run(
                    server.call_tool(
                        "extract_public_page",
                        {"url": scraped.url, "css_selector": "article"},
                    )
                )

        self.assertEqual(page.structured_content["text"], "Bounded research text")
        self.assertEqual(page.structured_content["links"], ["https://example.com/more"])
        self.assertTrue(page.structured_content["untrusted"])
        self.assertEqual(section.structured_content["text"], "Selected fact")
        self.assertTrue(section.structured_content["untrusted"])

    def test_creates_briefs_and_deep_dossiers_only_for_existing_packages(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "package"
            research_dir = package_dir / "research"
            research_dir.mkdir(parents=True)
            (research_dir / "role-research.md").write_text(
                "# Example Co — Engineer research\n", encoding="utf-8"
            )
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            server = build_server(config_path)
            existing = IntakeJobResult(
                package_dir=str(package_dir),
                job_snapshot="",
                selected_evidence="",
                selection_strategy="",
                proposal_tex="",
                diff="",
                claim_report="",
                validation=IntakeValidationResult(returncode=None, pdf=None),
            )
            result_json = json.dumps(
                {
                    "data": {
                        "web": [
                            {
                                "title": "Interview report",
                                "url": "https://www.reddit.com/r/example/comments/123/report/",
                            }
                        ]
                    }
                }
            )

            with patch(
                "erga_mcp.mcp_server._existing_intake_result_by_identity", return_value=existing
            ):
                brief: Any = asyncio.run(
                    server.call_tool(
                        "create_research_brief",
                        {"job_url": "https://jobs.example.test/123", "stage": "oa"},
                    )
                )
                deep: Any = asyncio.run(
                    server.call_tool(
                        "record_deep_research",
                        {
                            "job_url": "https://jobs.example.test/123",
                            "stage": "interview",
                            "searches": [{"query": "Example interview", "result": result_json}],
                        },
                    )
                )

        self.assertEqual(
            Path(cast(dict[str, Any], brief.structured_content)["research_brief"]).name,
            "oa-brief.md",
        )
        self.assertEqual(
            Path(cast(dict[str, Any], deep.structured_content)["deep_research_note"]).name,
            "interview-deep-research.md",
        )

    def test_exposes_one_job_url_tool_for_end_to_end_intake(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            tools = asyncio.run(build_server(config_path).list_tools())

        by_name = {tool.name: tool for tool in tools}
        self.assertIn("intake_job_url", by_name)
        tool = by_name["intake_job_url"]
        description = tool.description or ""
        self.assertIn("Use this tool immediately", description)
        self.assertIn("including a bare URL", description)
        self.assertIn("unfurled title and job-description preview", description)
        self.assertIn("do not browse or merely summarize", description)
        self.assertEqual(tool.input_schema["required"], ["job_url"])
        job_url_schema = tool.input_schema["properties"]["job_url"]
        self.assertEqual(job_url_schema["format"], "uri")
        self.assertIn("copied unchanged", job_url_schema["description"])
        self.assertIsNotNone(tool.output_schema)
        assert tool.output_schema is not None
        self.assertIn("package_dir", tool.output_schema["properties"])
        self.assertIn("reused", tool.output_schema["properties"])
        self.assertIsNotNone(tool.annotations)
        assert tool.annotations is not None
        self.assertFalse(tool.annotations.read_only_hint)
        self.assertTrue(tool.annotations.open_world_hint)
        self.assertFalse(tool.annotations.idempotent_hint)

        advanced = by_name["prepare_job_workspace"]
        self.assertIn("Advanced second-stage", advanced.description or "")
        self.assertIn(
            "Do not use this tool for a pasted or bare job URL", advanced.description or ""
        )

    def test_uses_an_injected_store_factory_for_another_storage_backend(self) -> None:
        class RecordingStoreFactory:
            def __init__(self) -> None:
                self.paths: list[Path] = []

            def create(self, database_path: Path) -> ErgaStore:
                self.paths.append(database_path)
                return ErgaStore(database_path)

        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            factory = RecordingStoreFactory()
            server = build_server(config_path, store_factory=factory)
            asyncio.run(server.call_tool("pipeline_status", {}))

        self.assertEqual(len(factory.paths), 1)
        self.assertEqual(factory.paths[0].name, "erga.sqlite3")

    def test_updates_application_status_in_local_state_only(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(root / "state" / "erga.sqlite3")
            application = store.create_application(
                company="Example",
                role="Software Engineering Intern",
                source_url="https://jobs.example.com/intern",
                evidence_ids=[],
            )
            server = build_server(config_path)

            result: Any = asyncio.run(
                server.call_tool(
                    "update_application_status",
                    {"application_id": application.id, "status": "Interview"},
                )
            )

            updated = cast(dict[str, object], result.structured_content)
            self.assertEqual(updated["status"], "interview")
            self.assertEqual(store.list_applications()[0].status, "interview")
            status_audits = [
                event
                for event in store.audit_events()
                if event.action == "application.status_updated"
            ]
            self.assertEqual(len(status_audits), 1)

            asyncio.run(
                server.call_tool(
                    "update_application_status",
                    {"application_id": application.id, "status": "interview"},
                )
            )
            status_audits = [
                event
                for event in store.audit_events()
                if event.action == "application.status_updated"
            ]
            self.assertEqual(len(status_audits), 1)

    def test_hermes_monitor_tool_prepares_scripts_without_creating_delivery_jobs(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            hermes_home = Path(directory) / "hermes-profile"
            server = build_server(config_path)
            prepared = {
                "mail_script": "erga-mcp-mail.py",
                "history_script": "erga-mcp-history.py",
                "suggested_jobs": [],
            }

            with (
                patch(
                    "erga_mcp.mcp_server.install_hermes_monitor_scripts",
                    return_value=prepared,
                ) as install,
                patch.dict("os.environ", {"HERMES_HOME": str(hermes_home)}),
            ):
                result: Any = asyncio.run(
                    server.call_tool("install_mail_monitor_scripts", {"history_days": 14})
                )

            self.assertEqual(result.structured_content, prepared)
            install.assert_called_once_with(
                config_path=config_path,
                scripts_dir=hermes_home / "scripts",
                history_days=14,
                replace=True,
            )

    def test_export_tool_creates_a_private_attachable_zip(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            result: Any = asyncio.run(build_server(config_path).call_tool("export_data", {}))

            exported = cast(dict[str, object], result.structured_content)
            archive = Path(str(exported["archive"]))
            self.assertTrue(archive.is_file())
            self.assertEqual(archive.suffix, ".zip")
            self.assertEqual(archive.parent, Path(str(exported["export_root"])))

    def test_intake_rejects_a_non_job_page_before_writing_a_package(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text(
                "\\section{Experience}\nVerified work.\n", encoding="utf-8"
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            with patch(
                "erga_mcp.mcp_server.fetch_job_snapshot",
                return_value="GitHub repository and source code for a project.",
            ):
                with self.assertRaisesRegex(ToolError, "specific job posting"):
                    asyncio.run(
                        server.call_tool(
                            "intake_job_url",
                            {"job_url": "https://github.com/example/project"},
                        )
                    )

            self.assertFalse((root / "output").exists())

    def test_advanced_workspace_setup_rejects_a_non_job_page_before_writing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text(
                "\\section{Experience}\nVerified work.\n", encoding="utf-8"
            )
            (root / "vault").mkdir()
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('vault_path = ""', 'vault_path = "vault"').replace(
                    'template_path = ""', 'template_path = "resume.tex"'
                ),
                encoding="utf-8",
            )
            server = build_server(config_path)
            with patch(
                "erga_mcp.mcp_server.fetch_job_snapshot",
                return_value="GitHub repository and source code for a project.",
            ):
                with self.assertRaisesRegex(ToolError, "specific job posting"):
                    asyncio.run(
                        server.call_tool(
                            "prepare_job_workspace",
                            {
                                "job_url": "https://github.com/example/project",
                                "company": "Example",
                                "role": "Engineer",
                                "cycle": "fall-2026",
                                "application_slug": "example-engineer",
                            },
                        )
                    )

            self.assertFalse((root / "output").exists())

    def test_intakes_one_url_end_to_end_and_safely_reuses_an_exact_repeat(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "resume.tex"
            original = "\\section{Experience}\nVerified work.\n"
            template.write_text(original, encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'template_path = ""', 'template_path = "resume.tex"'
                ).replace(
                    'output_pdf_name = "Firstname_Lastname_Resume.pdf"',
                    'output_pdf_name = "Candidate_Resume.pdf"',
                ),
                encoding="utf-8",
            )
            server = build_server(config_path)
            job_url = (
                "https://jobs.ashbyhq.com/example/"
                "00000000-0000-0000-0000-000000000000?source=discord%20preview"
            )
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_success(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(b"synthetic pdf")
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Python software engineering internship",
                ) as fetch,
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ) as validate,
            ):
                first_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )
                second_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )

            first = cast(dict[str, Any], first_call.structured_content)
            second = cast(dict[str, Any], second_call.structured_content)
            self.assertEqual(first["reused"], False)
            self.assertEqual(second["reused"], True)
            self.assertEqual(second["package_dir"], first["package_dir"])
            self.assertEqual(second["selection_strategy"], "existing_package")
            self.assertEqual(Path(first["validation"]["pdf"]).name, "Candidate_Resume.pdf")
            self.assertEqual(second["validation"]["pdf"], first["validation"]["pdf"])
            self.assertTrue(Path(first["validation"]["pdf"]).is_file())
            fetch.assert_called_once_with(job_url)
            validate.assert_called_once()
            self.assertEqual(template.read_text(encoding="utf-8"), original)
            for key in {
                "job_snapshot",
                "selected_evidence",
                "proposal_tex",
                "diff",
                "claim_report",
            }:
                self.assertTrue(Path(str(first[key])).is_file(), key)

    def test_primary_intake_builds_and_returns_the_exact_tailored_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "resume.tex"
            template.write_text(
                "\\section{Experience}\n"
                "\\resumeSubheading{Engineer}{2026}{Example}{Remote}\n"
                "\\resumeItemListStart\n"
                "\\resumeItem{Designed marketing websites with React.}\n"
                "\\resumeItem{Built Python low-latency services with FastAPI.}\n"
                "\\resumeItemListEnd\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"')
                .replace("editable_sections = []", 'editable_sections = ["experience"]')
                .replace(
                    'output_pdf_name = "Firstname_Lastname_Resume.pdf"',
                    'output_pdf_name = "Candidate_Resume.pdf"',
                ),
                encoding="utf-8",
            )
            server = build_server(config_path)
            job_url = "https://jobs.example.test/jobs/python-intern"
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_success(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(b"exact tailored pdf")
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value=(
                        "Python FastAPI low-latency software internship. "
                        "Responsibilities include building reliable services. "
                        "Requirements include Python experience. Apply now."
                    ),
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ),
            ):
                call: Any = asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            result = cast(dict[str, Any], call.structured_content)
            proposed = Path(result["proposal_tex"]).read_text(encoding="utf-8")
            self.assertLess(proposed.index("Built Python"), proposed.index("Designed marketing"))
            self.assertGreater(Path(result["diff"]).stat().st_size, 0)
            self.assertTrue(result["tailoring_meaningful_change"])
            self.assertEqual(result["tailoring_changed_sections"], ["Experience"])
            self.assertEqual(result["tailoring_version"], 15)
            self.assertEqual(result["git_project_research"], [])
            output_pdf = Path(result["validation"]["pdf"])
            self.assertEqual(output_pdf.name, "Candidate_Resume.pdf")
            self.assertEqual(output_pdf.read_bytes(), b"exact tailored pdf")
            manifest = json.loads(
                (Path(result["package_dir"]) / "package.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["tailoring"]["meaningful_change"])
            self.assertEqual(manifest["tailoring"]["version"], 15)

    def test_rebuilds_an_incomplete_legacy_package_and_preserves_its_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "resume.tex"
            template.write_text(
                "\\section{Experience}\n"
                "\\resumeSubheading{Engineer}{2026}{Example}{Remote}\n"
                "\\resumeItemListStart\n"
                "\\resumeItem{Built marketing pages with React.}\n"
                "\\resumeItem{Built production Python services.}\n"
                "\\resumeItemListEnd\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'template_path = ""', 'template_path = "resume.tex"'
                ).replace("editable_sections = []", 'editable_sections = ["experience"]'),
                encoding="utf-8",
            )
            job_url = "https://jobs.example.test/jobs/python-intern"
            legacy = root / "output" / "fall-2026" / "legacy-python-intern"
            (legacy / "artifacts").mkdir(parents=True)
            (legacy / "artifacts" / "old-resume.tex").write_text(
                "unsupported legacy content", encoding="utf-8"
            )
            (legacy / "package.json").write_text(
                json.dumps({"job_url": job_url, "template_status": "not_copied"}),
                encoding="utf-8",
            )
            server = build_server(config_path)
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_success(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(b"rebuilt pdf")
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value=(
                        "Python software engineering internship. "
                        "Responsibilities include building production services. "
                        "Requirements include Python experience. Apply now."
                    ),
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ),
            ):
                call: Any = asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            result = cast(dict[str, Any], call.structured_content)
            repaired = Path(result["package_dir"])
            self.assertEqual(repaired, legacy)
            self.assertTrue((repaired / "source" / "resume.tex").is_file())
            self.assertTrue((repaired / "artifacts" / "proposal.diff").is_file())
            self.assertTrue((repaired / "legacy-backup" / "legacy-package.json").is_file())
            self.assertEqual(
                (repaired / "legacy-backup" / "artifacts" / "old-resume.tex").read_text(
                    encoding="utf-8"
                ),
                "unsupported legacy content",
            )
            manifest = json.loads((repaired / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["legacy_backup"], "legacy-backup")
            self.assertEqual(manifest["tailoring"]["version"], 15)
            self.assertIn("Legacy package preserved", result["integration_warnings"][-1])

    def test_compile_rejects_a_pdf_over_the_configured_page_cap(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text("synthetic", encoding="utf-8")
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_two_pages(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(
                    b"%PDF-1.4\n1 0 obj<</Type /Page>>endobj\n2 0 obj<</Type /Page>>endobj\n%%EOF"
                )
                return validation

            with patch(
                "erga_mcp.mcp_server.validate_latex_proposal",
                side_effect=compile_two_pages,
            ):
                result = _compile_intake_proposal(
                    proposal,
                    latexmk="latexmk",
                    output_pdf_name="Candidate_Resume.pdf",
                    max_pages=1,
                )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.page_count, 2)
            self.assertIsNone(result.pdf)
            self.assertIn("configured maximum is 1", result.skipped or "")
            self.assertFalse(proposal.with_suffix(".pdf").exists())

    def test_primary_intake_writes_research_application_and_multicycle_obsidian_note(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text(
                "\\section{Experience}\nVerified work.\n", encoding="utf-8"
            )
            tracker = root / "tracker"
            tracker.mkdir()
            for cycle, filename in (
                ("Fall 2026", "Fall 2026 Application Tracker.md"),
                ("Summer 2027", "Summer 2027 Applications.md"),
            ):
                (tracker / filename).write_text(
                    f"# {cycle}\n\n## Application tracker\n\n"
                    "| Company | Role | Location / work mode | Source | Status | Applied | "
                    "Next action | Contact / link |\n"
                    "| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                    encoding="utf-8",
                )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'template_path = ""', 'template_path = "resume.tex"'
                ).replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )
            posting = {
                "@context": "https://schema.org/",
                "@type": "JobPosting",
                "title": "Software Engineering Internship (Fall 2026/Summer 2027)",
                "description": "Ship a project end to end using Codex for real-time voice AI.",
                "hiringOrganization": {"name": "Example Voice"},
                "jobLocationType": "TELECOMMUTE",
                "applicantLocationRequirements": {"name": "United States"},
            }
            snapshot = "Role @ Example Voice " + json.dumps(posting)
            server = build_server(config_path)
            job_url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_success(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(b"synthetic pdf")
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value=snapshot,
                ) as fetch,
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ),
            ):
                first_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )
                second_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )

            first = cast(dict[str, Any], first_call.structured_content)
            second = cast(dict[str, Any], second_call.structured_content)
            self.assertTrue(Path(first["research_note"]).is_file())
            self.assertIsNotNone(first["application_id"])
            self.assertEqual(Path(first["package_dir"]).parent.name, "fall-2026")
            self.assertTrue(
                Path(first["package_dir"]).name.startswith(
                    "example-voice-software-engineering-internship-"
                )
            )
            self.assertEqual(first["tracker_cycles"], ["Fall 2026", "Summer 2027"])
            self.assertEqual(len(first["integration_warnings"]), 1)
            self.assertIn("Project inventory is not configured", first["integration_warnings"][0])
            self.assertEqual(first["tracker_notes"], second["tracker_notes"])
            self.assertEqual(first["application_id"], second["application_id"])
            fetch.assert_called_once_with(job_url)

            note = Path(first["tracker_notes"][0])
            self.assertEqual(note.parent.name, "Fall 2026 Application Notes")
            self.assertIn("Role research", note.read_text(encoding="utf-8"))
            for filename in (
                "Fall 2026 Application Tracker.md",
                "Summer 2027 Applications.md",
            ):
                tracker_text = (tracker / filename).read_text(encoding="utf-8")
                self.assertEqual(
                    tracker_text.count("[[Example Voice — Software Engineering Internship]]"),
                    1,
                )

            applications: Any = asyncio.run(server.call_tool("list_applications", {}))
            self.assertEqual(len(applications.structured_content), 1)

    def test_tracking_only_url_changes_reuse_the_same_completed_package(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text("\\section{Experience}\nVerified work.\n")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            base = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
            validation = LatexValidation(command=("latexmk",), returncode=1, stdout="", stderr="")

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ) as fetch,
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    return_value=validation,
                ),
            ):
                first_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": f"{base}?source=discord"})
                )
                second_call: Any = asyncio.run(
                    server.call_tool(
                        "intake_job_url",
                        {"job_url": f"{base}?source=website&utm_campaign=fall"},
                    )
                )

            first = cast(dict[str, Any], first_call.structured_content)
            second = cast(dict[str, Any], second_call.structured_content)
            self.assertEqual(first["package_dir"], second["package_dir"])
            self.assertTrue(second["reused"])
            self.assertEqual(second["validation"]["returncode"], 1)
            fetch.assert_called_once()

    def test_failed_staging_does_not_claim_the_final_slug_and_retry_succeeds(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text("\\section{Experience}\nVerified work.\n")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            job_url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
            _, slug = _metadata_from_url(job_url, cycle="", application_slug="")
            final_dir = root / "output" / "unsorted" / slug

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "erga_mcp.mcp_server.create_automatic_resume_proposal",
                    side_effect=RuntimeError("synthetic proposal failure"),
                ),
                self.assertRaisesRegex(Exception, "synthetic proposal failure"),
            ):
                asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            self.assertFalse(final_dir.exists())
            validation = LatexValidation(command=("latexmk",), returncode=1, stdout="", stderr="")
            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    return_value=validation,
                ),
            ):
                result: Any = asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            structured = cast(dict[str, Any], result.structured_content)
            self.assertFalse(structured["reused"])
            self.assertTrue(Path(structured["package_dir"]).is_dir())
            self.assertEqual(Path(structured["package_dir"]).name, slug)

    def test_non_object_existing_manifest_reports_an_actionable_incomplete_package(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text("\\section{Experience}\nVerified work.\n")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            job_url = "https://boards.greenhouse.io/example/jobs/123456"
            cycle, slug = _metadata_from_url(job_url, cycle="", application_slug="")
            package_dir = root / "output" / cycle / slug
            package_dir.mkdir(parents=True)
            (package_dir / "package.json").write_text("[]\n", encoding="utf-8")

            with self.assertRaisesRegex(Exception, "existing job package is incomplete"):
                asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

    def test_compile_timeout_is_persisted_as_structured_validation_status(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text("\\section{Experience}\nVerified work.\n")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            job_url = "https://boards.greenhouse.io/example/jobs/123456"

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=subprocess.TimeoutExpired(("latexmk",), 120),
                ) as validate,
            ):
                first_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )
                second_call: Any = asyncio.run(
                    server.call_tool("intake_job_url", {"job_url": job_url})
                )

            first = cast(dict[str, Any], first_call.structured_content)
            second = cast(dict[str, Any], second_call.structured_content)
            self.assertIsNone(first["validation"]["returncode"])
            self.assertIn("did not complete", first["validation"]["skipped"])
            self.assertIsNone(second["validation"]["returncode"])
            self.assertIn("did not complete", second["validation"]["skipped"])
            self.assertTrue(second["reused"])
            self.assertEqual(validate.call_count, 2)
            manifest = json.loads(
                (Path(first["package_dir"]) / "package.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "complete")
            self.assertIsNone(manifest["validation"]["returncode"])

    def test_concurrent_identical_intakes_publish_one_complete_package(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.tex").write_text("\\section{Experience}\nVerified work.\n")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('template_path = ""', 'template_path = "resume.tex"'),
                encoding="utf-8",
            )
            server = build_server(config_path)
            tool = server._tool_manager.get_tool("intake_job_url")
            assert tool is not None
            job_url = "https://boards.greenhouse.io/example/jobs/123456"
            ready = Barrier(2)
            validation = LatexValidation(command=("latexmk",), returncode=1, stdout="", stderr="")

            def validate_together(*_: Any, **__: Any) -> LatexValidation:
                ready.wait(timeout=5)
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=validate_together,
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                results = list(pool.map(lambda _: asyncio.run(tool.fn(job_url)), range(2)))

            self.assertEqual(sorted(result.reused for result in results), [False, True])
            self.assertEqual(results[0].package_dir, results[1].package_dir)
            package_dir = Path(results[0].package_dir)
            self.assertTrue(package_dir.is_dir())
            self.assertEqual(
                json.loads((package_dir / "package.json").read_text())["status"], "complete"
            )
            self.assertFalse(
                any(path.name.startswith(".") for path in package_dir.parent.iterdir())
            )


if __name__ == "__main__":
    unittest.main()
