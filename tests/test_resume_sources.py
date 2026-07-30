from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from erga_mcp.resume_sources import (
    import_master_resume,
    load_resume_source,
    resume_source_context,
)
from erga_mcp.store import ErgaStore


class ResumeSourceTests(unittest.TestCase):
    def test_extracts_every_page_of_a_multi_page_master_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            master = Path(directory) / "master.pdf"
            master.write_bytes(b"%PDF synthetic")
            reader = SimpleNamespace(
                is_encrypted=False,
                pages=[
                    SimpleNamespace(extract_text=lambda: "First page experience"),
                    SimpleNamespace(extract_text=lambda: "Second page projects"),
                    SimpleNamespace(extract_text=lambda: "Third page skills"),
                ],
            )

            with patch("erga_mcp.resume_sources.PdfReader", return_value=reader):
                source = load_resume_source(master)

            self.assertEqual(source.page_count, 3)
            self.assertIn("[Page 1]", source.text)
            self.assertIn("Second page projects", source.text)
            self.assertIn("[Page 3]", source.text)

    def test_master_import_is_approved_and_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            master.write_text("Verified master resume content", encoding="utf-8")
            source = load_resume_source(master)
            store = ErgaStore(root / "state" / "erga.sqlite3")

            first = import_master_resume(store, source)
            second = import_master_resume(store, source)

            self.assertTrue(first.approved)
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(store.list_evidence()), 1)

    def test_optional_style_resume_cannot_introduce_claims(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            style = root / "style.tex"
            master.write_text("Master factual content", encoding="utf-8")
            style.write_text("One-page visual order", encoding="utf-8")

            context = resume_source_context(master_path=master, reference_path=style)

            self.assertTrue(context["master"]["user_approved_source"])  # type: ignore[index]
            self.assertFalse(context["style_reference"]["may_introduce_claims"])  # type: ignore[index]
            self.assertEqual(context["preferences"]["source"], "user-reference")  # type: ignore[index]
            self.assertTrue(context["preferences"]["style_override_confirmed"])  # type: ignore[index]

    def test_without_style_resume_erga_uses_default_preferences(self) -> None:
        with TemporaryDirectory() as directory:
            master = Path(directory) / "master.tex"
            master.write_text("Master factual content", encoding="utf-8")

            context = resume_source_context(master_path=master, reference_path=None)

            self.assertIsNone(context["style_reference"])
            self.assertEqual(context["preferences"]["source"], "erga-default")  # type: ignore[index]
            self.assertEqual(context["preferences"]["max_pages"], 1)  # type: ignore[index]
            self.assertFalse(context["preferences"]["style_override_confirmed"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
