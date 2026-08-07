from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from erga_mcp.config import load_config
from erga_mcp.doctor import check_installation
from erga_mcp.resume_sources import resume_source_context
from erga_mcp.setup_wizard import (
    CoreSetupReport,
    CoreSetupSelections,
    apply_core_setup,
    bullet_lengths_from_examples,
    collect_core_setup_selections,
    normalize_dropped_path,
    normalize_output_pdf_name,
    render_core_setup_report,
    render_core_setup_review,
    write_core_setup_plan,
)
from erga_mcp.store import ErgaStore


class SetupWizardTests(unittest.TestCase):
    def test_core_setup_infers_non_project_resume_capabilities(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "experience-only.tex"
            master.write_text(
                r"""\section{Experience}
\resumeItem{Built an approved synthetic service.}
\section{Technical Skills}
Languages: Python
""",
                encoding="utf-8",
            )

            selections = CoreSetupSelections(
                config_path=root / "private" / "config.toml",
                master_resume=master,
            )
            apply_core_setup(selections)
            config = load_config(selections.config_path)

            self.assertEqual(
                config.resume.editable_sections,
                ("Experience", "Technical Skills"),
            )
            self.assertEqual(config.resume.project_selection_mode, "template_only")
            self.assertNotIn(
                r"\section{Projects}",
                config.resume.template_path.read_text(encoding="utf-8"),  # type: ignore[union-attr]
            )

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
            self.assertEqual(
                (
                    config.resume.bullet_min_chars,
                    config.resume.bullet_target_chars,
                    config.resume.bullet_max_chars,
                ),
                (90, 105, 120),
            )
            self.assertEqual(config.resume.max_pages, 1)
            self.assertTrue(config.resume.output_root.is_dir())
            if os.name != "nt":
                self.assertEqual(selections.config_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertFalse(report.obsidian_configured)
            self.assertFalse(report.welcome_note_created)
            self.assertEqual(report.next_steps[0], "Run `erga status` to confirm your local setup.")
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

    def test_core_setup_creates_and_requires_an_obsidian_project_inventory_from_master_projects(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Career Vault"
            vault.mkdir()
            master = root / "Complete Master Resume.tex"
            master.write_text(
                r"""\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textbf{Systems Simulator} $|$ \textit{C++, Linux}}{}
\resumeItemListStart
\resumeItem{Built a C++ systems simulator on Linux.}
\resumeItemListEnd
\resumeSubHeadingListEnd
""",
                encoding="utf-8",
            )

            apply_core_setup(
                CoreSetupSelections(
                    config_path=root / "private" / "config.toml",
                    master_resume=master,
                    obsidian_enabled=True,
                    vault_mode="existing",
                    vault_path=vault,
                )
            )
            config = load_config(root / "private" / "config.toml")

            inventory_path = vault / "Erga" / "Project Inventory.json"
            self.assertEqual(config.resume.project_inventory_path, inventory_path)
            self.assertEqual(config.resume.project_selection_mode, "inventory_required")
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual([project["id"] for project in inventory], ["systems-simulator"])
            self.assertEqual(len(inventory[0]["bullet_evidence_ids"]), 1)
            self.assertTrue(inventory[0]["bullet_evidence_ids"][0][0].startswith("ev_"))

    def test_core_setup_connects_a_conventional_existing_obsidian_inventory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "Career Vault"
            catalogue = vault / "02 Projects" / "Project Inventory.json"
            catalogue.parent.mkdir(parents=True)
            catalogue.write_text("[]\n", encoding="utf-8")
            master = root / "master.tex"
            master.write_text("Approved master resume", encoding="utf-8")

            apply_core_setup(
                CoreSetupSelections(
                    config_path=root / "private" / "config.toml",
                    master_resume=master,
                    obsidian_enabled=True,
                    vault_mode="existing",
                    vault_path=vault,
                )
            )

            self.assertEqual(
                load_config(root / "private" / "config.toml").resume.project_inventory_path,
                catalogue,
            )
            self.assertEqual(catalogue.read_text(encoding="utf-8"), "[]\n")

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

    def test_core_setup_rerun_preserves_comments_and_unknown_owned_table_keys(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "private" / "config.toml"
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")
            selections = CoreSetupSelections(
                config_path=config_path,
                master_resume=master,
            )
            apply_core_setup(selections)
            raw = config_path.read_text(encoding="utf-8")
            raw = raw.replace(
                "[paths]\n",
                "[paths] # Keep table-header comment.\n"
                "# Keep custom path policy.\n"
                'future_path_policy = "strict" # from a newer Erga\n',
            ).replace(
                f"data_dir = {json.dumps(str(config_path.parent / 'state'))}",
                f"data_dir = {json.dumps(str(config_path.parent / 'state'))} # durable state",
            )
            raw = raw.replace(
                "[tracking]\n",
                "[tracking]\n# Keep custom tracker policy.\n"
                'future_tracker_view = "kanban" # user preference\n',
            ).replace(
                "enabled = false",
                "enabled = false # intentionally local-only",
            )
            raw = raw.replace(
                "[mcp]\n",
                "[mcp]\n# Keep custom capability policy.\n"
                'future_approval_mode = "review" # forward-compatible\n',
            ).replace(
                'tool_profile = "career"',
                'tool_profile = "career" # least privilege',
            )
            config_path.write_text(raw, encoding="utf-8")

            apply_core_setup(selections)
            rendered = config_path.read_text(encoding="utf-8")

            self.assertIn("# Keep custom path policy.", rendered)
            self.assertIn('future_path_policy = "strict" # from a newer Erga', rendered)
            self.assertIn("# durable state", rendered)
            self.assertIn("# Keep custom tracker policy.", rendered)
            self.assertIn('future_tracker_view = "kanban" # user preference', rendered)
            self.assertIn("enabled = false # intentionally local-only", rendered)
            self.assertIn("# Keep custom capability policy.", rendered)
            self.assertIn('future_approval_mode = "review" # forward-compatible', rendered)
            self.assertIn('tool_profile = "career" # least privilege', rendered)

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

        self.assertIn("What Erga will set up", review)
        self.assertIn("Your master resume: copied privately", review)
        self.assertIn("Maximum resume length: 1 page (recommended)", review)
        self.assertIn("90 / 105 / 120 characters", review)
        self.assertIn("Obsidian: not set up", review)
        self.assertIn("You can cancel now with no changes", review)
        self.assertIn("No Obsidian installation", report)
        self.assertNotIn("token", json.dumps(plan).casefold())
        self.assertIsNone(plan["vault_mode"])

        style_review = render_core_setup_review(
            CoreSetupSelections(
                config_path=Path("/private/config.toml"),
                master_resume=Path("/master.pdf"),
                style_resume=Path("/style.pdf"),
            )
        )
        self.assertIn("non-factual style metadata", style_review)
        self.assertNotIn("use style.pdf as a style reference", style_review)

    def test_core_setup_persists_custom_resume_shape_constraints(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")

            apply_core_setup(
                CoreSetupSelections(
                    config_path=root / "private" / "config.toml",
                    master_resume=master,
                    bullet_min_chars=100,
                    bullet_target_chars=115,
                    bullet_max_chars=130,
                    max_pages=2,
                )
            )
            resume = load_config(root / "private" / "config.toml").resume

            self.assertEqual(
                (
                    resume.bullet_min_chars,
                    resume.bullet_target_chars,
                    resume.bullet_max_chars,
                ),
                (100, 115, 130),
            )
            self.assertEqual(resume.max_pages, 2)

    def test_example_bullets_calibrate_lengths_without_becoming_setup_state(self) -> None:
        examples = (
            "• Built a private local career workflow with explicit evidence boundaries.",
            "- Added deterministic resume validation and application tracking.",
        )

        minimum, target, maximum = bullet_lengths_from_examples(examples)
        plan = json.loads(
            write_core_setup_plan(
                CoreSetupSelections(
                    config_path=Path("/private/config.toml"),
                    master_resume=Path("/master.pdf"),
                    bullet_min_chars=minimum,
                    bullet_target_chars=target,
                    bullet_max_chars=maximum,
                )
            )
        )

        self.assertLess(minimum, target)
        self.assertLess(target, maximum)
        self.assertNotIn("private local career workflow", json.dumps(plan))

    def test_example_bullet_calibration_rejects_empty_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            bullet_lengths_from_examples(("", "  "))

    def test_interactive_setup_calibrates_examples_but_does_not_retain_them(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")
            text_answers = iter(
                [
                    str(master),
                    "2",
                    "• Built a bounded local workflow for career evidence.",
                    "- Added deterministic validation for generated resumes.",
                    "Adrian_Osorio_Resume",
                ]
            )
            confirm_answers = iter([False, True, False, True, True])

            def prompt(answer: object) -> SimpleNamespace:
                return SimpleNamespace(ask=lambda: answer)

            with (
                patch(
                    "erga_mcp.setup_wizard.questionary.text",
                    side_effect=lambda *_args, **_kwargs: prompt(next(text_answers)),
                ),
                patch(
                    "erga_mcp.setup_wizard.questionary.confirm",
                    side_effect=lambda *_args, **_kwargs: prompt(next(confirm_answers)),
                ),
                patch(
                    "erga_mcp.setup_wizard.questionary.select",
                    return_value=prompt("examples"),
                ),
                patch("erga_mcp.setup_wizard.questionary.print"),
            ):
                selections = collect_core_setup_selections(
                    default_config_path=root / "private" / "config.toml"
                )

            serialized = write_core_setup_plan(selections)
            self.assertEqual(selections.max_pages, 2)
            self.assertEqual(selections.output_pdf_name, "Adrian_Osorio_Resume.pdf")
            self.assertGreater(selections.bullet_min_chars, 0)
            self.assertNotIn("bounded local workflow", serialized)

    def test_dragged_paths_accept_quotes_and_shell_escaped_spaces(self) -> None:
        with TemporaryDirectory() as directory:
            resume = Path(directory) / "Master Resume.pdf"

            self.assertEqual(normalize_dropped_path(f'"{resume}"'), resume.absolute())
            if os.name != "nt":
                escaped = str(resume).replace(" ", r"\ ")
                self.assertEqual(normalize_dropped_path(escaped), resume.absolute())

    def test_resume_output_name_adds_pdf_extension_and_rejects_path_components(self) -> None:
        self.assertEqual(
            normalize_output_pdf_name("Adrian_Osorio_Resume"),
            "Adrian_Osorio_Resume.pdf",
        )
        self.assertEqual(
            normalize_output_pdf_name("Adrian_Osorio_Resume.pdf"),
            "Adrian_Osorio_Resume.pdf",
        )
        for unsafe_name in (
            "../resume.pdf",
            r"..\resume.pdf",
            r"C:\temp\resume.pdf",
            r"\\server\share\resume.pdf",
        ):
            with self.subTest(unsafe_name=unsafe_name):
                with self.assertRaisesRegex(ValueError, "filename"):
                    normalize_output_pdf_name(unsafe_name)

    def test_core_setup_persists_the_user_selected_resume_output_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            master.write_text("Approved master", encoding="utf-8")

            apply_core_setup(
                CoreSetupSelections(
                    config_path=root / "private" / "config.toml",
                    master_resume=master,
                    output_pdf_name="Adrian_Osorio_Resume.pdf",
                )
            )

            self.assertEqual(
                load_config(root / "private" / "config.toml").resume.output_pdf_name,
                "Adrian_Osorio_Resume.pdf",
            )


if __name__ == "__main__":
    unittest.main()
