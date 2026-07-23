from __future__ import annotations

import asyncio
import json
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any, cast
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.mcp_server import (
    IntakeJobResult,
    IntakeValidationResult,
    _compile_intake_proposal,
    _metadata_from_url,
    build_server,
)
from erga_mcp.resume import LatexValidation
from erga_mcp.store import ErgaStore


class McpServerTests(unittest.TestCase):
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
            config_path.write_text(DEFAULT_CONFIG)
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
            config_path.write_text(DEFAULT_CONFIG)

            server = build_server(config_path)
            tools = asyncio.run(server.list_tools())

            by_name = {tool.name: tool for tool in tools}
            self.assertEqual(
                set(by_name),
                {
                    "pipeline_status",
                    "list_applications",
                    "application_tracker",
                    "list_evidence",
                    "list_mail_events",
                    "token_usage",
                    "record_token_usage",
                    "sync_recruiting_mail",
                    "intake_job_url",
                    "install_mail_monitor_scripts",
                    "export_data",
                    "record_secondary_research",
                    "create_research_brief",
                    "record_deep_research",
                    "prepare_job_workspace",
                    "create_tailored_resume",
                    "cover_letter_style_context",
                    "create_cover_letter",
                    "validate_tailored_resume",
                },
            )
            for name in {
                "pipeline_status",
                "list_applications",
                "application_tracker",
                "list_evidence",
                "list_mail_events",
            }:
                annotations = by_name[name].annotations
                self.assertIsNotNone(annotations)
                assert annotations is not None
                self.assertTrue(annotations.readOnlyHint)
                self.assertFalse(annotations.openWorldHint)
            workspace_annotations = by_name["prepare_job_workspace"].annotations
            mail_sync_annotations = by_name["sync_recruiting_mail"].annotations
            resume_annotations = by_name["create_tailored_resume"].annotations
            validation_annotations = by_name["validate_tailored_resume"].annotations
            assert workspace_annotations is not None
            assert mail_sync_annotations is not None
            assert resume_annotations is not None
            assert validation_annotations is not None
            self.assertFalse(workspace_annotations.readOnlyHint)
            self.assertTrue(workspace_annotations.openWorldHint)
            self.assertFalse(mail_sync_annotations.readOnlyHint)
            self.assertTrue(mail_sync_annotations.openWorldHint)
            self.assertFalse(resume_annotations.readOnlyHint)
            self.assertFalse(validation_annotations.readOnlyHint)

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

        self.assertEqual(Path(cast(dict[str, Any], brief[1])["research_brief"]).name, "oa-brief.md")
        self.assertEqual(
            Path(cast(dict[str, Any], deep[1])["deep_research_note"]).name,
            "interview-deep-research.md",
        )

    def test_exposes_one_job_url_tool_for_end_to_end_intake(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG)

            tools = asyncio.run(build_server(config_path).list_tools())

        by_name = {tool.name: tool for tool in tools}
        self.assertIn("intake_job_url", by_name)
        tool = by_name["intake_job_url"]
        description = tool.description or ""
        self.assertIn("Use this tool immediately", description)
        self.assertIn("including a bare URL", description)
        self.assertIn("unfurled title and job-description preview", description)
        self.assertIn("do not browse or merely summarize", description)
        self.assertEqual(tool.inputSchema["required"], ["job_url"])
        job_url_schema = tool.inputSchema["properties"]["job_url"]
        self.assertEqual(job_url_schema["format"], "uri")
        self.assertIn("copied unchanged", job_url_schema["description"])
        self.assertIsNotNone(tool.outputSchema)
        assert tool.outputSchema is not None
        self.assertIn("package_dir", tool.outputSchema["properties"])
        self.assertIn("reused", tool.outputSchema["properties"])
        self.assertIsNotNone(tool.annotations)
        assert tool.annotations is not None
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertTrue(tool.annotations.openWorldHint)
        self.assertTrue(tool.annotations.idempotentHint)

        advanced = by_name["prepare_job_workspace"]
        self.assertIn("Advanced second-stage", advanced.description or "")
        self.assertIn(
            "Do not use this tool for a pasted or bare job URL", advanced.description or ""
        )

    def test_monitor_setup_tool_prepares_scripts_without_creating_delivery_jobs(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG)
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

            self.assertEqual(result[1], prepared)
            install.assert_called_once_with(
                config_path=config_path,
                scripts_dir=hermes_home / "scripts",
                history_days=14,
                replace=True,
            )

    def test_export_tool_creates_a_private_attachable_zip(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG)
            result: Any = asyncio.run(build_server(config_path).call_tool("export_data", {}))

            exported = cast(dict[str, object], result[1])
            archive = Path(str(exported["archive"]))
            self.assertTrue(archive.is_file())
            self.assertEqual(archive.suffix, ".zip")
            self.assertEqual(archive.parent, Path(str(exported["export_root"])))

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

            first = cast(dict[str, Any], first_call[1])
            second = cast(dict[str, Any], second_call[1])
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
            job_url = "https://jobs.example.test/python-intern"
            validation = LatexValidation(command=("latexmk",), returncode=0, stdout="", stderr="")

            def compile_success(proposal_path: Path, **_: Any) -> LatexValidation:
                proposal_path.with_suffix(".pdf").write_bytes(b"exact tailored pdf")
                return validation

            with (
                patch(
                    "erga_mcp.mcp_server.fetch_job_snapshot",
                    return_value="Python FastAPI low-latency software internship",
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ),
            ):
                call: Any = asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            result = cast(dict[str, Any], call[1])
            proposed = Path(result["proposal_tex"]).read_text(encoding="utf-8")
            self.assertLess(proposed.index("Built Python"), proposed.index("Designed marketing"))
            self.assertGreater(Path(result["diff"]).stat().st_size, 0)
            self.assertTrue(result["tailoring_meaningful_change"])
            self.assertEqual(result["tailoring_changed_sections"], ["Experience"])
            self.assertEqual(result["tailoring_version"], 4)
            output_pdf = Path(result["validation"]["pdf"])
            self.assertEqual(output_pdf.name, "Candidate_Resume.pdf")
            self.assertEqual(output_pdf.read_bytes(), b"exact tailored pdf")
            manifest = json.loads(
                (Path(result["package_dir"]) / "package.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["tailoring"]["meaningful_change"])
            self.assertEqual(manifest["tailoring"]["version"], 4)

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
            job_url = "https://jobs.example.test/python-intern"
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
                    return_value="Python software engineering internship",
                ),
                patch(
                    "erga_mcp.mcp_server.validate_latex_proposal",
                    side_effect=compile_success,
                ),
            ):
                call: Any = asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            result = cast(dict[str, Any], call[1])
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
            self.assertEqual(manifest["tailoring"]["version"], 4)
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

            first = cast(dict[str, Any], first_call[1])
            second = cast(dict[str, Any], second_call[1])
            self.assertTrue(Path(first["research_note"]).is_file())
            self.assertIsNotNone(first["application_id"])
            self.assertEqual(Path(first["package_dir"]).parent.name, "fall-2026")
            self.assertTrue(
                Path(first["package_dir"]).name.startswith(
                    "example-voice-software-engineering-internship-"
                )
            )
            self.assertEqual(first["tracker_cycles"], ["Fall 2026", "Summer 2027"])
            self.assertEqual(first["integration_warnings"], [])
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
            self.assertEqual(len(applications[1]), 1)

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

            first = cast(dict[str, Any], first_call[1])
            second = cast(dict[str, Any], second_call[1])
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

            structured = cast(dict[str, Any], result[1])
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

            first = cast(dict[str, Any], first_call[1])
            second = cast(dict[str, Any], second_call[1])
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
                results = list(pool.map(lambda _: tool.fn(job_url), range(2)))

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
