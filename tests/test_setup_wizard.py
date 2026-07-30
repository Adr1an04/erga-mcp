from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import load_config
from erga_mcp.doctor import check_installation
from erga_mcp.resume_sources import resume_source_context
from erga_mcp.setup_wizard import (
    CoreSetupReport,
    CoreSetupSelections,
    apply_core_setup,
    normalize_dropped_path,
    render_core_setup_report,
    render_core_setup_review,
    write_core_setup_plan,
)
from erga_mcp.store import ErgaStore


class SetupWizardTests(unittest.TestCase):
    def test_core_setup_is_ready_without_obsidian_or_an_external_client(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "Complete Master Resume.tex"
            master.write_text("Approved factual master content", encoding="utf-8")
            selections = CoreSetupSelections(
                config_path=root / "private" / "config.toml",
                master_resume=master,
            )

            report = apply_core_setup(selections)
            config = load_config(selections.config_path)

            self.assertEqual(report.status, "ready")
            self.assertTrue(check_installation(selections.config_path).core_ready)
            self.assertIsNone(config.vault_path)
            self.assertFalse(config.tracker.enabled)
            self.assertEqual(config.resume.output_root, root / "private" / "generated-resumes")
            self.assertTrue(config.resume.output_root.is_dir())
            self.assertFalse(report.obsidian_configured)
            self.assertFalse(report.welcome_note_created)
            self.assertIn("Private local application tracking", report.completed)

    def test_core_setup_optionally_configures_obsidian_projection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Career Vault"
            vault.mkdir()
            master = root / "Complete Master Resume.tex"
            style = root / "Preferred Resume.tex"
            master.write_text("Approved factual master content", encoding="utf-8")
            style.write_text("Education\nExperience\nProjects", encoding="utf-8")
            selections = CoreSetupSelections(
                config_path=root / "private" / "config.toml",
                master_resume=master,
                style_resume=style,
                obsidian_enabled=True,
                vault_mode="existing",
                vault_path=vault,
            )

            report = apply_core_setup(selections)
            config = load_config(selections.config_path)
            master.unlink()
            style.unlink()
            context = resume_source_context(
                master_path=config.resume.master_path,  # type: ignore[arg-type]
                reference_path=config.resume.reference_path,
            )

            self.assertEqual(report.status, "ready")
            self.assertEqual(config.vault_path, vault)
            self.assertTrue(config.tracker.enabled)
            self.assertEqual(config.tracker.tracker_dir, vault / "Erga" / "Applications")
            self.assertTrue(config.tracker.tracker_dir.is_dir())
            self.assertEqual(config.resume.output_root, vault / "Erga" / "Generated Resumes")
            self.assertEqual(config.mcp.tool_profile, "career")
            self.assertTrue(check_installation(selections.config_path).core_ready)
            self.assertEqual(context["master"]["text"], "Approved factual master content")  # type: ignore[index]
            self.assertNotIn("text", context["style_reference"])  # type: ignore[operator]
            self.assertTrue((vault / "Erga" / "Start Here.md").is_file())
            self.assertNotEqual(config.resume.master_path, master)
            self.assertTrue(
                config.resume.master_path.is_relative_to(config.data_dir / "resume-sources")  # type: ignore[union-attr]
            )
            evidence = ErgaStore(config.data_dir / "erga.sqlite3").list_evidence()
            self.assertEqual(len(evidence), 1)
            self.assertTrue(evidence[0].approved)

    def test_core_setup_can_create_a_new_obsidian_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")
            vault = root / "New Vault"

            report = apply_core_setup(
                CoreSetupSelections(
                    config_path=root / "private" / "config.toml",
                    master_resume=master,
                    obsidian_enabled=True,
                    vault_mode="new",
                    vault_path=vault,
                )
            )

            self.assertTrue(vault.is_dir())
            self.assertEqual(report.vault_path, str(vault))
            self.assertFalse(report.style_configured)

    def test_core_setup_is_idempotent_and_never_overwrites_start_note(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            vault.mkdir()
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")
            selections = CoreSetupSelections(
                config_path=root / "private" / "config.toml",
                master_resume=master,
                obsidian_enabled=True,
                vault_mode="existing",
                vault_path=vault,
            )

            first = apply_core_setup(selections)
            start_note = vault / "Erga" / "Start Here.md"
            start_note.write_text("My edited note", encoding="utf-8")
            second = apply_core_setup(selections)

            self.assertTrue(first.welcome_note_created)
            self.assertFalse(second.welcome_note_created)
            self.assertEqual(start_note.read_text(encoding="utf-8"), "My edited note")
            config = load_config(selections.config_path)
            self.assertEqual(
                len(ErgaStore(config.data_dir / "erga.sqlite3").list_evidence()),
                1,
            )

    def test_core_setup_preserves_unrelated_existing_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                """
[paths]
data_dir = "private-state"
vault_path = ""

[mail]
provider = "gmail"
folder = "Recruiting"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            vault = root / "vault"
            vault.mkdir()
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")

            apply_core_setup(
                CoreSetupSelections(
                    config_path=config_path,
                    master_resume=master,
                    obsidian_enabled=True,
                    vault_mode="existing",
                    vault_path=vault,
                )
            )
            config = load_config(config_path)

            self.assertEqual(config.data_dir, root / "private-state")
            self.assertEqual(config.mail_provider, "gmail")
            self.assertEqual(config.mail_folder, "Recruiting")

    def test_review_and_report_make_optional_connection_boundary_explicit(self) -> None:
        selections = CoreSetupSelections(
            config_path=Path("/private/config.toml"),
            master_resume=Path("/master.pdf"),
        )

        review = render_core_setup_review(selections)
        report = render_core_setup_report(
            CoreSetupReport(
                status="ready",
                config_path="/private/config.toml",
                data_dir="/private/state",
                vault_path=None,
                tracker_dir=None,
                output_root="/private/generated-resumes",
                master_sha256="0" * 64,
                style_configured=False,
                obsidian_configured=False,
                welcome_note_created=False,
                completed=["Private local application tracking"],
                next_steps=["Optionally connect a coding assistant."],
            )
        )
        plan = json.loads(write_core_setup_plan(selections))

        self.assertIn("Coding AI:         not required or configured", review)
        self.assertIn("Discord:           not required or configured", review)
        self.assertIn("Obsidian:          not configured (optional)", review)
        self.assertIn("No Obsidian installation", report)
        self.assertNotIn("token", json.dumps(plan).casefold())
        self.assertIsNone(plan["vault_mode"])

    def test_dragged_paths_accept_quotes_and_shell_escaped_spaces(self) -> None:
        with TemporaryDirectory() as directory:
            resume = Path(directory) / "Master Resume.pdf"

            self.assertEqual(normalize_dropped_path(f'"{resume}"'), resume.absolute())
            if os.name != "nt":
                escaped = str(resume).replace(" ", r"\ ")
                self.assertEqual(normalize_dropped_path(escaped), resume.absolute())


if __name__ == "__main__":
    unittest.main()
