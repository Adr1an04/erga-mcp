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

from recruiting_pipeline.config import DEFAULT_CONFIG
from recruiting_pipeline.mcp_server import _metadata_from_url, build_server
from recruiting_pipeline.resume import LatexValidation


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
                    "list_evidence",
                    "list_mail_events",
                    "intake_job_url",
                    "prepare_job_workspace",
                    "create_tailored_resume",
                    "validate_tailored_resume",
                },
            )
            for name in {
                "pipeline_status",
                "list_applications",
                "list_evidence",
                "list_mail_events",
            }:
                annotations = by_name[name].annotations
                self.assertIsNotNone(annotations)
                assert annotations is not None
                self.assertTrue(annotations.readOnlyHint)
                self.assertFalse(annotations.openWorldHint)
            workspace_annotations = by_name["prepare_job_workspace"].annotations
            resume_annotations = by_name["create_tailored_resume"].annotations
            validation_annotations = by_name["validate_tailored_resume"].annotations
            assert workspace_annotations is not None
            assert resume_annotations is not None
            assert validation_annotations is not None
            self.assertFalse(workspace_annotations.readOnlyHint)
            self.assertTrue(workspace_annotations.openWorldHint)
            self.assertFalse(resume_annotations.readOnlyHint)
            self.assertFalse(validation_annotations.readOnlyHint)

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
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Python software engineering internship",
                ) as fetch,
                patch(
                    "recruiting_pipeline.mcp_server.validate_latex_proposal",
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
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ) as fetch,
                patch(
                    "recruiting_pipeline.mcp_server.validate_latex_proposal",
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
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "recruiting_pipeline.mcp_server.create_baseline_resume_proposal",
                    side_effect=RuntimeError("synthetic proposal failure"),
                ),
                self.assertRaisesRegex(Exception, "synthetic proposal failure"),
            ):
                asyncio.run(server.call_tool("intake_job_url", {"job_url": job_url}))

            self.assertFalse(final_dir.exists())
            validation = LatexValidation(command=("latexmk",), returncode=1, stdout="", stderr="")
            with (
                patch(
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "recruiting_pipeline.mcp_server.validate_latex_proposal",
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
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "recruiting_pipeline.mcp_server.validate_latex_proposal",
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
            validate.assert_called_once()
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
                    "recruiting_pipeline.mcp_server.fetch_job_snapshot",
                    return_value="Software engineering internship",
                ),
                patch(
                    "recruiting_pipeline.mcp_server.validate_latex_proposal",
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
