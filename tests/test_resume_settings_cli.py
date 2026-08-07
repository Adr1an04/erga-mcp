from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cli import main
from erga_mcp.config import load_config
from erga_mcp.resume_settings import update_settings


class ResumeSettingsCliTests(unittest.TestCase):
    def _json_command(self, arguments: list[str]) -> dict[str, object]:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return json.loads(output.getvalue())

    def test_sets_and_shows_generic_resume_settings(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            main(["init", "--config", str(config)])

            self._json_command(
                [
                    "resume",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    "templates/master.tex",
                    "--editable-section",
                    "experience",
                    "--editable-section",
                    "projects",
                    "--bullet-min-chars",
                    "90",
                    "--bullet-target-chars",
                    "105",
                    "--bullet-max-chars",
                    "120",
                    "--max-pages",
                    "1",
                    "--output-root",
                    "applications",
                    "--output-pdf-name",
                    "Candidate_Resume.pdf",
                ]
            )

            settings = self._json_command(["resume", "settings", "show", "--config", str(config)])

            self.assertEqual(settings["template_path"], str(root / "templates/master.tex"))
            self.assertEqual(settings["editable_sections"], ["experience", "projects"])
            self.assertEqual(settings["bullet_target_chars"], 105)
            self.assertEqual(settings["output_root"], str(root / "applications"))
            self.assertEqual(settings["output_pdf_name"], "Candidate_Resume.pdf")
            stored_config = config.read_text(encoding="utf-8")
            self.assertIn('template_path = "templates/master.tex"', stored_config)
            self.assertIn('output_root = "applications"', stored_config)
            self.assertIn('output_pdf_name = "Candidate_Resume.pdf"', stored_config)

    def test_rejects_invalid_settings_without_changing_the_config_file(self) -> None:
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            main(["init", "--config", str(config)])
            original = config.read_text(encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "bullet character lengths"):
                update_settings(
                    config,
                    {
                        "bullet_min_chars": 120,
                        "bullet_target_chars": 105,
                        "bullet_max_chars": 90,
                    },
                )

            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_update_preserves_resume_comments_and_unknown_settings(self) -> None:
        with TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            main(["init", "--config", str(config)])
            raw = config.read_text(encoding="utf-8")
            raw = raw.replace(
                "[resume]\n",
                "[resume]\n# Keep this explanation for future editors.\n"
                'future_layout_mode = "compact" # owned by a newer Erga\n',
            ).replace(
                "bullet_target_chars = 0",
                "bullet_target_chars = 0 # preferred visual density",
            )
            config.write_text(raw, encoding="utf-8")

            update_settings(
                config,
                {
                    "bullet_min_chars": 90,
                    "bullet_target_chars": 95,
                    "bullet_max_chars": 100,
                },
            )
            rendered = config.read_text(encoding="utf-8")

            self.assertIn("# Keep this explanation for future editors.", rendered)
            self.assertIn('future_layout_mode = "compact" # owned by a newer Erga', rendered)
            self.assertIn("bullet_target_chars = 95 # preferred visual density", rendered)

    def test_imports_durable_master_and_style_sources(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            master = root / "Complete Master Resume.tex"
            style = root / "Preferred Style.tex"
            master.write_text("Approved master facts", encoding="utf-8")
            style.write_text("Education\nExperience\nProjects", encoding="utf-8")
            main(["init", "--config", str(config)])

            imported = self._json_command(
                [
                    "resume",
                    "sources",
                    "import",
                    "--config",
                    str(config),
                    "--master",
                    str(master),
                    "--style",
                    str(style),
                ]
            )
            master.unlink()
            style.unlink()
            context = self._json_command(["resume", "sources", "context", "--config", str(config)])

            self.assertTrue(Path(str(imported["master_path"])).is_file())
            self.assertTrue(Path(str(imported["style_path"])).is_file())
            self.assertEqual(context["master"]["text"], "Approved master facts")  # type: ignore[index]
            self.assertNotIn("text", context["style_reference"])  # type: ignore[operator]
            self.assertEqual(
                context["style_reference"]["observed_section_order"],  # type: ignore[index]
                ["Education", "Experience", "Projects"],
            )

    def test_resets_to_default_template_without_replacing_the_master(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            master = root / "master.tex"
            style = root / "style.tex"
            master.write_text(
                "Jane Candidate\nExperience\nBuilt an approved service.\n",
                encoding="utf-8",
            )
            style.write_text("Projects\nTechnical Skills\n", encoding="utf-8")
            main(["init", "--config", str(config_path)])
            self._json_command(
                [
                    "resume",
                    "sources",
                    "import",
                    "--config",
                    str(config_path),
                    "--master",
                    str(master),
                    "--style",
                    str(style),
                ]
            )
            before = load_config(config_path).resume
            assert before.master_path is not None
            assert before.template_path is not None
            managed_master = before.master_path
            previous_template = before.template_path

            result = self._json_command(
                ["resume", "template", "reset", "--config", str(config_path)]
            )
            after = load_config(config_path).resume

            self.assertTrue(result["reset"])
            self.assertEqual(result["master_path"], str(managed_master))
            self.assertIsNone(result["style_path"])
            self.assertEqual(after.master_path, managed_master)
            self.assertIsNone(after.reference_path)
            self.assertNotEqual(after.template_path, previous_template)
            self.assertTrue(previous_template.is_file())
            self.assertTrue(after.template_path.is_file())  # type: ignore[union-attr]
            self.assertIn(
                "% Erga semantic resume template version:",
                after.template_path.read_text(encoding="utf-8"),  # type: ignore[union-attr]
            )

    def test_creates_a_package_using_the_configured_output_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            main(["init", "--config", str(config)])

            package = self._json_command(
                [
                    "resume",
                    "create-package",
                    "--config",
                    str(config),
                    "--cycle",
                    "Fall26",
                    "--application-slug",
                    "Fall26ExampleSystems",
                    "--job-url",
                    "https://jobs.example.test/example-systems",
                ]
            )

            self.assertEqual(
                package["package_dir"],
                str(root / "output" / "Fall26" / "Fall26ExampleSystems"),
            )
            self.assertTrue(Path(str(package["manifest_path"])).exists())


if __name__ == "__main__":
    unittest.main()
