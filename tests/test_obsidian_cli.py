from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cli import main


class ObsidianCliTests(unittest.TestCase):
    def test_imports_a_configured_note_as_unapproved_local_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            note = vault / "Career.md"
            vault.mkdir()
            note.write_text("## Project\n\nVerified delivery outcome.\n", encoding="utf-8")
            config = root / "config.toml"
            config.write_text(
                f'[paths]\ndata_dir = "state"\nvault_path = {json.dumps(str(vault))}\n',
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["obsidian", "import", "--config", str(config), "--note", "Career.md"]
                )

            imported = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(imported[0]["source_ref"], "Career.md#Project")
            self.assertFalse(imported[0]["approved"])


if __name__ == "__main__":
    unittest.main()
