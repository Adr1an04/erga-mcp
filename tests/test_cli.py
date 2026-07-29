from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.cli import main
from erga_mcp.config import load_config
from erga_mcp.job_discovery import DiscoveryResearchResult
from erga_mcp.store import ErgaStore


class CliTests(unittest.TestCase):
    def test_onboard_returns_machine_readable_ready_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "private" / "config.toml"
            executable = root / "erga-mcp"
            executable.write_text("", encoding="utf-8")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "onboard",
                        "codex",
                        "--config",
                        str(config_path),
                        "--project-dir",
                        str(root),
                        "--server-command",
                        str(executable),
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "ready")
            self.assertTrue(payload["core_ready"])
            self.assertFalse(payload["model_api_required"])
            self.assertEqual(payload["tool_profile"], "career")

    def test_client_configure_writes_project_scoped_codex_entry_without_model_key(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "erga.toml"
            main(["init", "--config", str(config_path)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "client",
                        "configure",
                        "codex",
                        "--config",
                        str(config_path),
                        "--project-dir",
                        str(root),
                        "--write",
                    ]
                )

            payload = json.loads(output.getvalue())
            generated = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["written"])
            self.assertFalse(payload["model_api_required"])
            self.assertIn('ERGA_MCP_TOOL_PROFILE = "career"', generated)
            self.assertNotIn("API_KEY", generated)

    def test_init_creates_a_non_secret_config_and_local_database(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config" / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            config = load_config(config_path)
            self.assertEqual(exit_code, 0)
            self.assertTrue(config_path.exists())
            self.assertTrue((config.data_dir / "erga.sqlite3").exists())
            self.assertNotIn("token", config_path.read_text().lower())

    def test_status_includes_mail_event_count(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["status", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["mail_events"], 0)

    def test_notes_command_renders_one_tracked_application_and_its_research(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            application = store.create_application(
                company="Google",
                role="Software Engineering Intern",
                source_url="https://careers.example.test/jobs/google-intern",
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")
            package = (
                config.resume.output_root / "summer-2027" / "google-software-engineering-intern"
            )
            research = package / "research"
            research.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"job_url": application.source_url}), encoding="utf-8"
            )
            (research / "role-research.md").write_text(
                "# Google role research\n\nOfficial requirements.", encoding="utf-8"
            )
            (research / "secondary-research.md").write_text(
                "# Secondary online research\n\nCommunity context.", encoding="utf-8"
            )
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["notes", "google", "--config", str(config_path)])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("# Google - Software Engineering Intern", rendered)
            self.assertIn("Status: applied", rendered)
            self.assertIn("Official requirements.", rendered)
            self.assertIn("Community context.", rendered)

    def test_research_command_discovers_and_saves_research_for_one_application(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            application = store.create_application(
                company="Google",
                role="Software Engineering Intern",
                source_url="https://careers.example.test/jobs/google-intern",
                evidence_ids=[],
            )
            package = config.resume.output_root / "summer-2027" / "google-intern"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"job_url": application.source_url}), encoding="utf-8"
            )
            output = StringIO()
            result = DiscoveryResearchResult(
                path=package / "research" / "discovery-research.md",
                sources_scraped=3,
                outreach_leads=1,
            )

            with patch("erga_mcp.cli.discover_job_research", return_value=result) as discover:
                with redirect_stdout(output):
                    exit_code = main(["research", "google", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(discover.call_args.kwargs["application"], application)
            self.assertEqual(discover.call_args.kwargs["package_dir"], package)
            self.assertIn("3 sources scraped", output.getvalue())
            self.assertIn("1 public outreach lead", output.getvalue())

    def test_tokens_command_reports_input_output_and_total_for_one_application(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            application_output = StringIO()
            with redirect_stdout(application_output):
                main(
                    [
                        "applications",
                        "add",
                        "--config",
                        str(config_path),
                        "--company",
                        "Example",
                        "--role",
                        "Engineer",
                        "--source-url",
                        "https://jobs.example.test/123",
                    ]
                )
            application_id = json.loads(application_output.getvalue())["id"]
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            store.record_token_usage(
                application_id=application_id,
                operation="intake",
                input_tokens=700,
                output_tokens=123,
            )
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["tokens", "--config", str(config_path), "--application-id", application_id]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "applications": 1,
                    "events": 1,
                    "input_tokens": 700,
                    "output_tokens": 123,
                    "total_tokens": 823,
                },
            )


if __name__ == "__main__":
    unittest.main()
