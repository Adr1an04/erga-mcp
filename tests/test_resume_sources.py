from __future__ import annotations

import json
import os
import stat
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from erga_mcp.resume_sources import (
    import_master_resume,
    load_resume_source,
    resume_source_context,
    snapshot_resume_source,
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

    def test_pdf_extraction_requests_layout_preserving_line_boundaries(self) -> None:
        with TemporaryDirectory() as directory:
            master = Path(directory) / "master.pdf"
            master.write_bytes(b"%PDF synthetic")
            calls: list[dict[str, str]] = []

            class Page:
                def extract_text(self, **kwargs: str) -> str:
                    calls.append(kwargs)
                    return "Experience\n   Built a service\n   across two regions."

            reader = SimpleNamespace(is_encrypted=False, pages=[Page()])
            with patch("erga_mcp.resume_sources.PdfReader", return_value=reader):
                source = load_resume_source(master)

            self.assertEqual(calls, [{"extraction_mode": "layout"}])
            self.assertIn("\n   Built a service\n", source.text)

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

    def test_new_master_supersedes_old_approved_master_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "master-v1.tex"
            second_path = root / "master-v2.tex"
            first_path.write_text("First approved master", encoding="utf-8")
            second_path.write_text("Updated approved master", encoding="utf-8")
            store = ErgaStore(root / "state" / "erga.sqlite3")

            first = import_master_resume(store, load_resume_source(first_path))
            second = import_master_resume(store, load_resume_source(second_path))
            evidence = store.list_evidence()

            self.assertNotEqual(first.id, second.id)
            self.assertEqual([item.id for item in evidence if item.approved], [second.id])
            self.assertFalse(next(item for item in evidence if item.id == first.id).approved)

    def test_managed_snapshot_survives_original_move_and_records_provenance(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "Complete Master Resume.tex"
            original.write_text("Durable factual master content", encoding="utf-8")
            source = load_resume_source(original)

            first = snapshot_resume_source(source, data_dir=root / "state", role="master")
            second = snapshot_resume_source(source, data_dir=root / "state", role="master")
            metadata = json.loads((first.path.parent / "master.json").read_text(encoding="utf-8"))
            original.unlink()

            context = resume_source_context(master_path=first.path, reference_path=None)

            self.assertEqual(first.path, second.path)
            self.assertEqual(first.path.name, "master.tex")
            self.assertTrue(first.path.is_relative_to(root / "state" / "resume-sources"))
            self.assertEqual(metadata["original_paths"], [str(original)])
            self.assertEqual(metadata["sha256"], source.sha256)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(first.path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o600)
                self.assertEqual(
                    stat.S_IMODE((first.path.parent / "master.json").stat().st_mode),
                    0o600,
                )
            self.assertEqual(
                context["master"]["text"],  # type: ignore[index]
                "Durable factual master content",
            )

    def test_managed_snapshot_refuses_content_address_corruption(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "master.tex"
            original.write_text("Approved source", encoding="utf-8")
            source = load_resume_source(original)
            managed = snapshot_resume_source(source, data_dir=root / "state", role="master")
            managed.path.write_text("tampered", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "integrity verification"):
                snapshot_resume_source(source, data_dir=root / "state", role="master")
            with self.assertRaisesRegex(ValueError, "integrity verification"):
                resume_source_context(master_path=managed.path, reference_path=None)

    def test_managed_snapshot_rejects_a_source_changed_after_extraction(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "master.tex"
            original.write_text("Initially approved source", encoding="utf-8")
            source = load_resume_source(original)
            original.write_text("Changed after extraction", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "changed before"):
                snapshot_resume_source(source, data_dir=root / "state", role="master")

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
            self.assertFalse(context["style_reference"]["raw_text_exposed"])  # type: ignore[index]
            self.assertNotIn("text", context["style_reference"])  # type: ignore[operator]
            self.assertNotIn("One-page visual order", repr(context["style_reference"]))
            self.assertEqual(context["preferences"]["source"], "user-reference")  # type: ignore[index]
            self.assertTrue(context["preferences"]["style_override_confirmed"])  # type: ignore[index]
            self.assertEqual(
                context["preferences"]["reference_metadata"],  # type: ignore[index]
                [
                    "page count",
                    "section order",
                    "content density",
                    "typography and margins",
                ],
            )
            self.assertEqual(
                context["preferences"]["rendered_layout_control"],  # type: ignore[index]
                "editable-latex-template",
            )
            self.assertEqual(
                context["preferences"]["not_automatically_transformed"],  # type: ignore[index]
                [],
            )
            self.assertEqual(  # type: ignore[index]
                context["preferences"]["automatically_applied"],
                ["section presence", "section order"],
            )
            self.assertNotIn("adjust_from_reference", context["preferences"])  # type: ignore[operator]

    def test_docx_rejects_oversized_decompressed_document_xml(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "oversized.docx"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", b"x" * (8 * 1024 * 1024 + 1))

            with self.assertRaisesRegex(ValueError, "decompression limit"):
                load_resume_source(source)

    def test_without_style_resume_erga_uses_default_preferences(self) -> None:
        with TemporaryDirectory() as directory:
            master = Path(directory) / "master.tex"
            master.write_text("Master factual content", encoding="utf-8")

            context = resume_source_context(master_path=master, reference_path=None)

            self.assertIsNone(context["style_reference"])
            self.assertEqual(context["preferences"]["source"], "erga-default")  # type: ignore[index]
            self.assertEqual(context["preferences"]["max_pages"], 1)  # type: ignore[index]
            self.assertFalse(context["preferences"]["style_override_confirmed"])  # type: ignore[index]

    def test_resume_context_exposes_inferred_template_profile_to_any_mcp_host(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            template = root / "resume.tex"
            metadata = root / "template.json"
            master.write_text("Approved master", encoding="utf-8")
            template.write_text("Generated template", encoding="utf-8")
            profile = {
                "editable_sections": ["Projects", "Technical Skills"],
                "project_count": 3,
                "repeatable_sections": ["Projects"],
                "section_item_counts": {"Projects": 9, "Technical Skills": 0},
                "section_order": ["Projects", "Technical Skills"],
            }
            style_profile = {
                "project_count": 3,
                "section_item_counts": {"Projects": 6},
            }
            visual_profile = {"body_font_size_pt": 10.0, "margin_in": 0.5}
            metadata.write_text(
                json.dumps(
                    {
                        "layout_profile": profile,
                        "style_layout_profile": style_profile,
                        "visual_style_profile": visual_profile,
                    }
                ),
                encoding="utf-8",
            )

            context = resume_source_context(
                master_path=master,
                reference_path=None,
                template_path=template,
            )

            self.assertEqual(context["layout_profile"], profile)
            self.assertEqual(context["style_layout_profile"], style_profile)
            self.assertEqual(context["visual_style_profile"], visual_profile)
            self.assertEqual(
                context["preferences"]["section_order"],  # type: ignore[index]
                ["Projects", "Technical Skills"],
            )
            self.assertEqual(
                context["preferences"]["automatically_applied"],  # type: ignore[index]
                [
                    "section presence",
                    "section order",
                    "project count",
                    "content density",
                    "typography and margins",
                ],
            )

    def test_pdf_style_reference_applies_only_its_page_count_automatically(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.tex"
            style = root / "style.pdf"
            master.write_text("Approved master", encoding="utf-8")
            style.write_bytes(b"%PDF synthetic")
            reader = SimpleNamespace(
                is_encrypted=False,
                pages=[
                    SimpleNamespace(extract_text=lambda: "Education"),
                    SimpleNamespace(extract_text=lambda: "Experience"),
                ],
            )

            with patch("erga_mcp.resume_sources.PdfReader", return_value=reader):
                context = resume_source_context(master_path=master, reference_path=style)

            self.assertEqual(context["preferences"]["max_pages"], 2)  # type: ignore[index]
            self.assertEqual(
                context["preferences"]["automatically_applied"],  # type: ignore[index]
                ["maximum page count", "section presence", "section order"],
            )
            self.assertEqual(
                context["preferences"]["not_automatically_transformed"],  # type: ignore[index]
                [],
            )


if __name__ == "__main__":
    unittest.main()
