from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from erga_mcp.config import load_config
from erga_mcp.resume_sources import resume_source_context
from erga_mcp.setup_wizard import (
    SetupSelections,
    _master_resume_file,
    _normalize_dropped_path,
    _parse_discord_identities,
    _style_resume_file,
    apply_setup,
    collect_setup_selections,
    render_setup_review,
    write_setup_plan,
)
from erga_mcp.store import ErgaStore


class SetupWizardTests(unittest.TestCase):
    def _command(self, root: Path, name: str) -> Path:
        command = root / name
        command.write_text("", encoding="utf-8")
        return command

    def test_local_setup_configures_resume_and_client(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "resume.tex"
            resume.write_text("\\documentclass{article}\n", encoding="utf-8")
            reference = root / "Current Resume.tex"
            reference.write_text("\\documentclass{article}\n", encoding="utf-8")
            selections = SetupSelections(
                experience="local",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("resume",),
                master_resume=resume,
                style_resume=reference,
                output_root=root / "applications",
            )

            with patch(
                "erga_mcp.setup_wizard.verify_subscription_login",
                return_value=(True, "Logged in using ChatGPT"),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                    client_command=self._command(root, "codex"),
                )

            config = load_config(selections.config_path)
            self.assertEqual(report.status, "ready")
            self.assertTrue(report.resume_configured)
            self.assertNotEqual(config.resume.master_path, resume)
            self.assertIsNotNone(config.resume.master_path)
            self.assertTrue(
                config.resume.master_path.is_relative_to(config.data_dir / "resume-sources")
            )
            self.assertIsNone(config.resume.template_path)
            self.assertIsNotNone(config.resume.reference_path)
            self.assertTrue(
                config.resume.reference_path.is_relative_to(config.data_dir / "resume-sources")
            )
            resume.unlink()
            reference.unlink()
            context = resume_source_context(
                master_path=config.resume.master_path,
                reference_path=config.resume.reference_path,
            )
            self.assertIn("\\documentclass", context["master"]["text"])  # type: ignore[index]
            self.assertIsNotNone(context["style_reference"])
            self.assertTrue((root / ".codex" / "config.toml").is_file())

    def test_missing_client_stops_before_workspace_resume_or_discord_questions(self) -> None:
        def answer(value: str) -> SimpleNamespace:
            return SimpleNamespace(ask=lambda: value)

        with (
            patch(
                "erga_mcp.setup_wizard.questionary.select",
                side_effect=[answer("full"), answer("codex")],
            ),
            patch("erga_mcp.setup_wizard.questionary.path") as path_prompt,
            patch("erga_mcp.setup_wizard.questionary.password") as password_prompt,
            patch("erga_mcp.setup_wizard.questionary.print"),
            patch(
                "erga_mcp.setup_wizard.resolve_client_command",
                side_effect=FileNotFoundError("Codex was not found"),
            ),
        ):
            with self.assertRaisesRegex(FileNotFoundError, "Codex was not found"):
                collect_setup_selections(
                    default_project_dir=Path("/workspace"),
                    default_config_path=Path("/private/config.toml"),
                )

        path_prompt.assert_not_called()
        password_prompt.assert_not_called()

    def test_apply_reuses_the_subscription_preflight_from_the_same_setup_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._command(root, "codex")
            selections = SetupSelections(
                experience="custom",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=(),
                client_command=command,
                client_preflight_verified=True,
            )

            with patch("erga_mcp.setup_wizard.verify_subscription_login") as verify:
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                )

            self.assertEqual(report.status, "ready")
            verify.assert_not_called()

    def test_dragged_resume_paths_accept_quotes_and_escaped_spaces(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "Master Resume.tex"
            resume.write_text("\\documentclass{article}\n", encoding="utf-8")

            self.assertEqual(_normalize_dropped_path(f'"{resume}"'), resume.absolute())
            if os.name != "nt":
                escaped = str(resume).replace(" ", r"\ ")
                self.assertEqual(_normalize_dropped_path(escaped), resume.absolute())

    def test_pdf_master_becomes_approved_knowledge_without_becoming_the_latex_template(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "Complete Master Resume.pdf"
            master.write_bytes(b"%PDF synthetic")
            selections = SetupSelections(
                experience="local",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("resume",),
                master_resume=master,
                output_root=root / "erga-applications",
            )
            reader = SimpleNamespace(
                is_encrypted=False,
                pages=[
                    SimpleNamespace(extract_text=lambda: "Experience and project knowledge"),
                    SimpleNamespace(extract_text=lambda: "Additional master resume page"),
                ],
            )

            with (
                patch("erga_mcp.resume_sources.PdfReader", return_value=reader),
                patch(
                    "erga_mcp.setup_wizard.verify_subscription_login",
                    return_value=(True, "ready"),
                ),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                    client_command=self._command(root, "codex"),
                )

            config = load_config(selections.config_path)
            evidence = ErgaStore(config.data_dir / "erga.sqlite3").list_evidence()
            self.assertTrue(report.resume_configured)
            self.assertNotEqual(config.resume.master_path, master)
            self.assertIsNotNone(config.resume.master_path)
            self.assertTrue(
                config.resume.master_path.is_relative_to(config.data_dir / "resume-sources")
            )
            self.assertEqual(config.resume.master_path.read_bytes(), b"%PDF synthetic")
            self.assertIsNone(config.resume.template_path)
            self.assertEqual(config.resume.max_pages, 1)
            self.assertEqual(len(evidence), 1)
            self.assertTrue(evidence[0].approved)
            self.assertTrue(evidence[0].source_ref.endswith(":Complete Master Resume.pdf"))
            self.assertIn("Additional master resume page", evidence[0].text)

    def test_master_and_optional_style_formats_accept_typical_resume_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master_pdf = root / "master.pdf"
            master_pdf.write_bytes(b"%PDF-1.4\n")
            reference_docx = root / "current.docx"
            reference_docx.write_bytes(b"PK")

            self.assertTrue(_master_resume_file(str(master_pdf)))
            self.assertTrue(_style_resume_file(str(reference_docx)))

    def test_full_setup_configures_native_discord_without_hermes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selections = SetupSelections(
                experience="full",
                client="claude-code",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("discord",),
                discord_token="discord-secret",
                discord_user_ids=(123456789,),
                start_discord=True,
            )

            with (
                patch(
                    "erga_mcp.setup_wizard.verify_subscription_login",
                    return_value=(True, '{"subscriptionType":"max"}'),
                ),
                patch("erga_mcp.setup_wizard.store_discord_token") as store_token,
                patch(
                    "erga_mcp.setup_wizard.start_discord_bridge",
                    return_value={"running": True, "pid": 123},
                ),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                    client_command=self._command(root, "claude"),
                )

            self.assertTrue(report.discord_configured)
            self.assertTrue(report.discord_running)
            store_token.assert_called_once_with(selections.config_path, "discord-secret")
            settings = (selections.config_path.parent / "discord-bridge.json").read_text()
            self.assertNotIn("discord-secret", settings)
            self.assertNotIn("hermes", settings.casefold())

    def test_modern_discord_usernames_do_not_require_developer_mode(self) -> None:
        user_ids, usernames = _parse_discord_identities("student.dev, @trusted.friend, 123456789")

        self.assertEqual(user_ids, (123456789,))
        self.assertEqual(usernames, ("student.dev", "trusted.friend"))

    def test_invalid_discord_discriminator_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid Discord"):
            _parse_discord_identities("oldname#1234")

    def test_review_and_dry_run_redact_the_discord_token(self) -> None:
        selections = SetupSelections(
            experience="full",
            client="codex",
            project_dir=Path("/workspace"),
            config_path=Path("/private/config.toml"),
            features=("discord",),
            discord_token="super-secret-token",
            discord_user_ids=(123,),
        )

        rendered = render_setup_review(selections) + write_setup_plan(selections)

        self.assertNotIn("super-secret-token", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("Hermes", rendered)

    def test_local_review_omits_unselected_discord_fields(self) -> None:
        selections = SetupSelections(
            experience="local",
            client="codex",
            project_dir=Path("/workspace"),
            config_path=Path("/private/config.toml"),
            features=("resume",),
        )

        rendered = render_setup_review(selections)

        self.assertNotIn("Discord", rendered)
        self.assertIn("Master knowledge", rendered)
        self.assertIn("Erga default", rendered)

    def test_generic_client_persists_headless_arguments_for_discord(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._command(root, "another-agent")
            selections = SetupSelections(
                experience="full",
                client="generic-mcp",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("discord",),
                client_command=command,
                custom_arguments=("--headless", "{prompt}"),
                discord_token="discord-secret",
                discord_user_ids=(123,),
                start_discord=False,
            )

            with (
                patch(
                    "erga_mcp.setup_wizard.verify_subscription_login",
                    return_value=(True, "ready"),
                ),
                patch("erga_mcp.setup_wizard.store_discord_token"),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                )

            settings = json.loads(
                (selections.config_path.parent / "discord-bridge.json").read_text()
            )
            self.assertTrue(report.discord_configured)
            self.assertEqual(settings["custom_arguments"], ["--headless", "{prompt}"])
            self.assertTrue((root / ".mcp.json").is_file())

    def test_setup_stops_when_the_coding_subscription_is_not_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selections = SetupSelections(
                experience="local",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("resume",),
            )

            with patch(
                "erga_mcp.setup_wizard.verify_subscription_login",
                return_value=(False, "Not logged in"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Sign in first"):
                    apply_setup(
                        selections,
                        server_command=self._command(root, "erga-mcp"),
                        client_command=self._command(root, "codex"),
                    )

            self.assertFalse(selections.config_path.exists())


if __name__ == "__main__":
    unittest.main()
