from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cli import main


class CoverLetterSettingsCliTests(unittest.TestCase):
    def _json_command(self, arguments: list[str]) -> dict[str, object]:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        return json.loads(output.getvalue())

    def test_sets_and_shows_template_and_obsidian_relative_writing_sample(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            main(["init", "--config", str(config)])
            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    'vault_path = ""', 'vault_path = "vault"'
                ),
                encoding="utf-8",
            )

            updated = self._json_command(
                [
                    "cover-letter",
                    "settings",
                    "set",
                    "--config",
                    str(config),
                    "--template-path",
                    "templates/cover-letter.md",
                    "--writing-sample-path",
                    "Writing Samples/Personal Statement.md",
                ]
            )
            shown = self._json_command(
                ["cover-letter", "settings", "show", "--config", str(config)]
            )

            self.assertEqual(updated, shown)
            self.assertEqual(updated["template_path"], str(root / "templates/cover-letter.md"))
            self.assertEqual(
                updated["writing_sample_path"],
                str(root / "vault/Writing Samples/Personal Statement.md"),
            )
            stored = config.read_text(encoding="utf-8")
            self.assertIn('template_path = "templates/cover-letter.md"', stored)
            self.assertIn('writing_sample_path = "Writing Samples/Personal Statement.md"', stored)


if __name__ == "__main__":
    unittest.main()
