from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cover_letter import create_cover_letter_proposal, load_style_context
from erga_mcp.models import Evidence


class CoverLetterTests(unittest.TestCase):
    def test_loads_a_user_selected_writing_sample_without_assuming_a_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / "writing-sample.md"
            sample.write_text("Short sentences. Specific details.", encoding="utf-8")

            context = load_style_context(sample)

            self.assertEqual(context.source_path, sample.resolve())
            self.assertEqual(context.text, "Short sentences. Specific details.")
            self.assertTrue(context.sha256)

    def test_creates_a_reviewable_proposal_without_modifying_template_or_sample(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "cover-letter.md"
            sample = root / "style.md"
            template_text = "Dear Hiring Team,\n\n{{BODY}}\n\nSincerely,\nAdrian\n"
            sample_text = "I write clearly and directly."
            template.write_text(template_text, encoding="utf-8")
            sample.write_text(sample_text, encoding="utf-8")
            evidence = [
                Evidence(
                    "ev1",
                    "Career.md#Project",
                    "Built a reliable application tracker.",
                    True,
                    datetime.now(UTC),
                )
            ]

            proposal = create_cover_letter_proposal(
                template_path=template,
                writing_sample_path=sample,
                output_dir=root / "proposal",
                body="I would be excited to bring that work to Example Co.",
                evidence=evidence,
            )

            self.assertEqual(template.read_text(encoding="utf-8"), template_text)
            self.assertEqual(sample.read_text(encoding="utf-8"), sample_text)
            self.assertEqual(
                proposal.proposed_path.read_text(encoding="utf-8"),
                "Dear Hiring Team,\n\n"
                "I would be excited to bring that work to Example Co.\n\n"
                "Sincerely,\nAdrian\n",
            )
            self.assertIn("I would be excited", proposal.diff_path.read_text(encoding="utf-8"))
            provenance = json.loads(proposal.provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(provenance["writing_sample"]["source_path"], str(sample.resolve()))
            self.assertTrue(provenance["writing_sample"]["style_only"])
            self.assertEqual(provenance["approved_evidence"][0]["id"], "ev1")
            self.assertTrue(provenance["content_review_required"])

    def test_rejects_unsafe_templates_and_unapproved_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "cover-letter.md"
            sample = root / "style.md"
            template.write_text("No body marker", encoding="utf-8")
            sample.write_text("Style", encoding="utf-8")
            evidence = [Evidence("ev1", "source", "text", False, datetime.now(UTC))]

            with self.assertRaisesRegex(ValueError, "exactly one"):
                create_cover_letter_proposal(
                    template_path=template,
                    writing_sample_path=sample,
                    output_dir=root / "proposal",
                    body="Draft",
                    evidence=evidence,
                )

            template.write_text("{{BODY}}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved evidence"):
                create_cover_letter_proposal(
                    template_path=template,
                    writing_sample_path=sample,
                    output_dir=root / "proposal",
                    body="Draft",
                    evidence=evidence,
                )


if __name__ == "__main__":
    unittest.main()
