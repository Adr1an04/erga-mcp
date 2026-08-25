from __future__ import annotations

import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.applications.discovery import DiscoveryResearchResult
from erga_mcp.cli import _package_for_application, _run_console, main
from erga_mcp.config import load_config
from erga_mcp.models import Application
from erga_mcp.store import ErgaStore


class CliTests(unittest.TestCase):
    def test_no_argument_invocation_is_a_friendly_first_run_guide(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([])

        rendered = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Welcome to Erga", rendered)
        self.assertIn("erga setup", rendered)
        self.assertIn("Tailor my résumé", rendered)
        self.assertNotIn("MCP", rendered)

    def test_human_status_uses_real_next_steps_without_internal_action_ids(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["status", "--config", str(config_path)])

            rendered = output.getvalue()
            self.assertEqual(exit_code, 0)
            self.assertIn("Résumé needed", rendered)
            self.assertIn("erga setup", rendered)
            self.assertNotIn("onboarding.skills.help", rendered)
            self.assertNotIn("MCP", rendered)

    def test_manual_update_reports_safe_checkout_result(self) -> None:
        output = StringIO()
        result = type(
            "Result",
            (),
            {
                "updated": False,
                "previous_revision": "a" * 40,
                "current_revision": "a" * 40,
            },
        )()
        with (
            patch("erga_mcp.cli.update_erga_checkout", return_value=result),
            patch("erga_mcp.cli.erga_checkout_root", return_value=Path("/safe/erga")),
            redirect_stdout(output),
        ):
            exit_code = main(["update"])

        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertFalse(report["updated"])
        self.assertFalse(report["hermes_plugin_updated"])

    def test_scheduled_update_refreshes_router_and_requests_gateway_restart(self) -> None:
        output = StringIO()
        result = type(
            "Result",
            (),
            {
                "updated": True,
                "previous_revision": "a" * 40,
                "current_revision": "b" * 40,
            },
        )()
        with (
            TemporaryDirectory() as directory,
            patch("erga_mcp.cli.update_erga_checkout", return_value=result),
            patch("erga_mcp.cli.erga_checkout_root", return_value=Path("/safe/erga")),
            patch("erga_mcp.cli.synchronize_router_plugin", return_value=True) as sync,
            patch("erga_mcp.cli.request_hermes_gateway_restart", return_value=True) as restart,
            redirect_stdout(output),
        ):
            hermes_home = Path(directory) / "hermes"
            exit_code = main(
                [
                    "update",
                    "--scheduled",
                    "--hermes-home",
                    str(hermes_home),
                ]
            )

        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report["updated"])
        self.assertTrue(report["hermes_plugin_updated"])
        self.assertTrue(report["hermes_gateway_restart_requested"])
        sync.assert_called_once_with(
            checkout_root=Path("/safe/erga"),
            hermes_home=hermes_home,
        )
        restart.assert_called_once_with(hermes_home=hermes_home)

    def test_package_lookup_preserves_query_posting_ids_but_ignores_tracking(self) -> None:
        with TemporaryDirectory() as directory:
            output_root = Path(directory)
            first = output_root / "summer-2027" / "first"
            second = output_root / "summer-2027" / "second"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "package.json").write_text(
                json.dumps({"job_url": "https://jobs.example.test/view?jk=one"}),
                encoding="utf-8",
            )
            (second / "package.json").write_text(
                json.dumps({"job_url": "https://jobs.example.test/view?jk=two"}),
                encoding="utf-8",
            )
            application = Application(
                id="app_test",
                company="Example",
                role="Engineer",
                source_url="https://jobs.example.test/view?utm_source=discord&jk=two",
                status="draft",
                evidence_ids=[],
                created_at=datetime.now(UTC),
            )

            self.assertEqual(_package_for_application(output_root, application), second)

    def test_console_boundary_reports_expected_errors_without_a_traceback(self) -> None:
        with TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            stderr = StringIO()
            with redirect_stderr(stderr):
                exit_code = _run_console(["status", "--config", str(missing)])

        self.assertEqual(exit_code, 1)
        self.assertIn("Erga could not complete the command", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_resume_template_ensure_reports_generated_or_reused_path(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            template = Path(directory) / "generated" / "resume.tex"
            output = StringIO()
            main(["init", "--config", str(config_path)])

            with (
                patch("erga_mcp.cli.ensure_resume_template", return_value=template) as ensure,
                redirect_stdout(output),
            ):
                exit_code = main(["resume", "template", "ensure", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(output.getvalue()),
                {"generated_or_reused": True, "template_path": str(template)},
            )
            ensure.assert_called_once()

    def test_discord_connect_reuses_existing_setup(self) -> None:
        output = StringIO()
        connected = {
            "configured": True,
            "running": True,
            "ready": True,
            "pid": 4321,
            "log_path": "/private/discord.log",
        }

        with (
            patch("erga_mcp.cli.connect_discord_bridge", return_value=connected) as connect,
            redirect_stdout(output),
        ):
            exit_code = main(["discord", "connect", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), connected)
        connect.assert_called_once()

    def test_discord_token_can_be_replaced_without_rerunning_setup(self) -> None:
        output = StringIO()

        with (
            patch("erga_mcp.cli.getpass.getpass", return_value="replacement") as prompt,
            patch("erga_mcp.cli.store_discord_token") as store,
            redirect_stdout(output),
        ):
            exit_code = main(["discord", "set-token", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), {"stored": "OS credential store"})
        prompt.assert_called_once()
        store.assert_called_once()
        self.assertNotIn("replacement", output.getvalue())

    def test_init_creates_a_non_secret_config_and_local_database(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config" / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            config = load_config(config_path)
            self.assertEqual(exit_code, 0)
            self.assertTrue(config_path.exists())
            self.assertTrue((config.data_dir / "erga.sqlite3").exists())
            self.assertNotIn("token", config_path.read_text().lower())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(config.data_dir.stat().st_mode), 0o700)
                self.assertEqual(
                    stat.S_IMODE((config.data_dir / "erga.sqlite3").stat().st_mode),
                    0o600,
                )

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are unavailable")
    def test_init_restricts_config_and_state_to_the_current_user(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config" / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            config = load_config(config_path)
            database_path = config.data_dir / "erga.sqlite3"
            self.assertEqual(exit_code, 0)
            self.assertEqual(stat.S_IMODE(config_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config.data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(database_path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are unavailable")
    def test_init_does_not_change_an_existing_config_parent_permissions(self) -> None:
        with TemporaryDirectory() as directory:
            config_parent = Path(directory) / "shared-config"
            config_parent.mkdir(mode=0o755)
            config_parent.chmod(0o755)
            config_path = config_parent / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stat.S_IMODE(config_parent.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits are unavailable")
    def test_init_does_not_change_existing_state_permissions(self) -> None:
        with TemporaryDirectory() as directory:
            config_parent = Path(directory) / "shared-config"
            config_parent.mkdir(mode=0o755)
            config_parent.chmod(0o755)
            state_dir = config_parent / "state"
            state_dir.mkdir(mode=0o755)
            state_dir.chmod(0o755)
            database_path = state_dir / "erga.sqlite3"
            database_path.touch(mode=0o644)
            database_path.chmod(0o644)
            config_path = config_parent / "config.toml"

            exit_code = main(["init", "--config", str(config_path)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(stat.S_IMODE(state_dir.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(database_path.stat().st_mode), 0o644)

    def test_status_includes_mail_event_count(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["status", "--config", str(config_path), "--json"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.getvalue())["mail_events"], 0)

    def test_tracker_orbit_renders_a_png_without_a_model_call(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            output_path = root / "orbit.png"
            main(["init", "--config", str(config_path)])
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            application = store.create_application(
                company="Example",
                role="Engineer Intern",
                source_url="https://jobs.example.test/role",
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "tracker",
                        "orbit",
                        "--config",
                        str(config_path),
                        "--output",
                        str(output_path),
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(payload["model_api_used"])
            self.assertEqual(payload["image_path"], str(output_path))
            self.assertEqual(output_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

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
