from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.cli import main
from recruiting_pipeline.resume_settings import update_settings


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
                ]
            )

            settings = self._json_command(["resume", "settings", "show", "--config", str(config)])

            self.assertEqual(settings["template_path"], str(root / "templates/master.tex"))
            self.assertEqual(settings["editable_sections"], ["experience", "projects"])
            self.assertEqual(settings["bullet_target_chars"], 105)
            self.assertEqual(settings["output_root"], str(root / "applications"))
            stored_config = config.read_text(encoding="utf-8")
            self.assertIn('template_path = "templates/master.tex"', stored_config)
            self.assertIn('output_root = "applications"', stored_config)

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
                    "Fall26Palantir",
                    "--job-url",
                    "https://jobs.example.test/palantir",
                ]
            )

            self.assertEqual(
                package["package_dir"], str(root / "output" / "Fall26" / "Fall26Palantir")
            )
            self.assertTrue(Path(str(package["manifest_path"])).exists())


if __name__ == "__main__":
    unittest.main()
