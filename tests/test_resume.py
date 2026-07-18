from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.models import Evidence
from recruiting_pipeline.resume import create_resume_proposal


class ResumeProposalTests(unittest.TestCase):
    def test_creates_a_reviewable_latex_patch_without_modifying_the_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "resume.tex"
            original = "\\documentclass{article}\n\\begin{document}\n\\end{document}\n"
            resume.write_text(original, encoding="utf-8")
            evidence = Evidence(
                id="ev_example",
                source_ref="Career.md#Project",
                text="Verified delivery outcome.",
                approved=True,
                created_at=datetime.now(UTC),
            )

            proposal = create_resume_proposal(
                resume_path=resume,
                output_dir=root / "proposals",
                latex_snippet="\\item Verified delivery outcome.",
                evidence=[evidence],
            )

            self.assertEqual(resume.read_text(encoding="utf-8"), original)
            self.assertTrue(proposal.diff_path.exists())
            self.assertTrue(proposal.claim_report_path.exists())
            self.assertIn("+\\item Verified delivery outcome.", proposal.diff_path.read_text())
            self.assertIn('"ev_example"', proposal.claim_report_path.read_text())


if __name__ == "__main__":
    unittest.main()
