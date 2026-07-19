from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.models import Evidence
from recruiting_pipeline.resume import (
    create_baseline_resume_proposal,
    create_section_resume_proposal,
    replace_section_contents,
)


class ResumeEditorTests(unittest.TestCase):
    def test_replaces_only_the_selected_latex_section_body(self) -> None:
        source = "\\section{Experience}\nold experience\n\\section{Projects}\nold project\n"
        self.assertEqual(
            replace_section_contents(source, "Experience", "new experience"),
            "\\section{Experience}\nnew experience\n\\section{Projects}\nold project\n",
        )

    def test_rejects_missing_or_ambiguous_sections(self) -> None:
        with self.assertRaises(ValueError):
            replace_section_contents("\\section{Projects}\nx\n", "Experience", "new")
        with self.assertRaises(ValueError):
            replace_section_contents(
                "\\section{Experience}\nx\n\\section{Experience}\ny\n", "Experience", "new"
            )

    def test_creates_a_reviewable_section_specific_proposal_without_removing_existing_content(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            original = "\\section{Experience}\nold\n\\section{Projects}\nkeep\n"
            source.write_text(original, encoding="utf-8")
            evidence = Evidence("ev1", "Career.md#Experience", "verified", True, datetime.now(UTC))
            proposal = create_section_resume_proposal(
                resume_path=source,
                output_dir=root / "proposal",
                section_name="Experience",
                latex_content="new",
                evidence=[evidence],
            )
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            proposed = proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn("old", proposed)
            self.assertIn("new", proposed)
            self.assertIn("keep", proposed)
            self.assertTrue(proposal.diff_path.exists())
            self.assertTrue(proposal.claim_report_path.exists())

    def test_creates_a_truthful_baseline_proposal_when_no_claim_can_be_added(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            original = "\\section{Experience}\nverified work\n"
            source.write_text(original, encoding="utf-8")

            proposal = create_baseline_resume_proposal(
                resume_path=source,
                output_dir=root / "proposal",
                evidence=[],
                reason="No approved evidence overlapped the job description.",
            )

            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertEqual(proposal.proposed_tex_path.read_text(encoding="utf-8"), original)
            self.assertEqual(proposal.diff_path.read_text(encoding="utf-8"), "")
            report = proposal.claim_report_path.read_text(encoding="utf-8")
            self.assertIn("No approved evidence overlapped", report)
            self.assertIn('"approved_evidence": []', report)


if __name__ == "__main__":
    unittest.main()
