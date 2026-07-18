from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.integrations.obsidian import import_markdown_evidence


class ObsidianImporterTests(unittest.TestCase):
    def test_imports_heading_scoped_evidence_from_a_note_inside_the_configured_vault(self) -> None:
        with TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            note = vault / "Career" / "Projects.md"
            note.parent.mkdir(parents=True)
            original = "# Projects\n\n## Pipeline\n\nReduced manual review time by 30%.\n"
            note.write_text(original, encoding="utf-8")

            candidates = import_markdown_evidence(vault, Path("Career/Projects.md"))

            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].source_ref, "Career/Projects.md#Pipeline")
            self.assertEqual(candidates[0].text, "Reduced manual review time by 30%.")
            self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_rejects_a_note_outside_the_configured_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            vault = root / "vault"
            vault.mkdir()
            outside = root / "private.md"
            outside.write_text("# Private\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                import_markdown_evidence(vault, outside)


if __name__ == "__main__":
    unittest.main()
