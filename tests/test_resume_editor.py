from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import Evidence
from erga_mcp.resume import (
    create_baseline_resume_proposal,
    create_section_resume_proposal,
    replace_section_contents,
    resume_bullet_length_report,
)


class ResumeEditorTests(unittest.TestCase):
    def test_replaces_only_the_selected_latex_section_body(self) -> None:
        source = "\\section{Experience}\nold experience\n\\section{Projects}\nold project\n"
        self.assertEqual(
            replace_section_contents(source, "Experience", "new experience"),
            "\\section{Experience}\nnew experience\n\\section{Projects}\nold project\n",
        )

    def test_resolves_configured_section_case_and_separators(self) -> None:
        source = "\\section{Technical Skills}\nold skills\n"
        self.assertEqual(
            replace_section_contents(source, "technical-skills", "new skills"),
            "\\section{Technical Skills}\nnew skills\n",
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

    def test_section_proposal_enforces_configured_limits_on_new_bullets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text("\\section{Experience}\nold\n", encoding="utf-8")
            evidence = Evidence("ev1", "approved", "verified", True, datetime.now(UTC))

            with self.assertRaisesRegex(ValueError, "between 90 and 120.*received 9"):
                create_section_resume_proposal(
                    resume_path=source,
                    output_dir=root / "proposal",
                    section_name="Experience",
                    latex_content="\\resumeItem{Too short}",
                    evidence=[evidence],
                    bullet_min_chars=90,
                    bullet_target_chars=105,
                    bullet_max_chars=120,
                )

            self.assertFalse((root / "proposal").exists())

    def test_section_proposal_records_a_passing_bullet_constraint_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text("\\section{Experience}\nold\n", encoding="utf-8")
            evidence = Evidence("ev1", "approved", "verified", True, datetime.now(UTC))
            bullet = "A" * 105

            proposal = create_section_resume_proposal(
                resume_path=source,
                output_dir=root / "proposal",
                section_name="Experience",
                latex_content=f"\\resumeItem{{{bullet}}}",
                evidence=[evidence],
                bullet_min_chars=90,
                bullet_target_chars=105,
                bullet_max_chars=120,
            )
            report = proposal.claim_report_path.read_text(encoding="utf-8")

            self.assertIn('"passed": true', report)
            self.assertIn('"validated_bullets": 1', report)
            self.assertIn('"target": 105', report)

    def test_bullet_length_measurement_handles_nested_latex_and_ignores_non_bullet_content(
        self,
    ) -> None:
        report = resume_bullet_length_report(
            "\\resumeItem{Built \\textbf{fast} systems with 20\\% lower latency.}\n"
            "\\resumeSubheading{Company}{Date}",
            minimum=40,
            target=45,
            maximum=60,
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["validated_bullets"], 1)

    def test_bullet_length_measurement_rejects_malformed_resume_item(self) -> None:
        with self.assertRaisesRegex(ValueError, "unterminated"):
            resume_bullet_length_report(
                "\\resumeItem{unfinished",
                minimum=90,
                target=105,
                maximum=120,
            )


if __name__ == "__main__":
    unittest.main()
