from __future__ import annotations

import unittest

from erga_mcp.resumes.bullet_editor import analyze_bullet_editorially, analyze_resume_editorially


class ResumeBulletEditorTests(unittest.TestCase):
    def test_parses_a_complete_action_scope_method_and_outcome_story(self) -> None:
        report = analyze_bullet_editorially(
            "Automated invoice reviews with a Python risk dashboard, preventing $2M/year in fees."
        )

        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 85)
        self.assertEqual(report.structure.action, "Automated")
        self.assertEqual(report.structure.metrics, ("$2M/year",))
        self.assertIn("python", report.structure.technical_terms)
        self.assertIn("outcome-oriented", report.strengths)

    def test_accepts_specific_engineering_outcomes_without_forcing_a_number(self) -> None:
        report = analyze_bullet_editorially(
            "Hardened YAML config parsing with schema validation, preventing malformed catalogs "
            "from breaking release workflows."
        )

        self.assertTrue(report.passed)
        self.assertFalse(report.structure.metrics)
        self.assertNotIn("outcome.missing", {item.code for item in report.issues})

    def test_accepts_a_directional_engineering_result_with_functional_proof(self) -> None:
        report = analyze_bullet_editorially(
            "Reduced API error rates through schema validation and retry handling."
        )

        self.assertTrue(report.passed)
        self.assertTrue(report.structure.has_outcome)

    def test_rejects_generic_improvement_language_even_with_technology_names(self) -> None:
        report = analyze_bullet_editorially("Improved API performance with Python for users.")

        self.assertFalse(report.passed)
        self.assertIn("language.vague", {item.code for item in report.issues})

    def test_versions_and_calendar_years_do_not_count_as_impact_metrics(self) -> None:
        report = analyze_bullet_editorially(
            "Built a Python 3 API in 2024 with FastAPI for customer workflows."
        )

        self.assertEqual(report.structure.metrics, ())
        self.assertFalse(report.passed)

    def test_rejects_two_accomplishments_tacked_together(self) -> None:
        report = analyze_bullet_editorially(
            "Grew partnerships to 20 and doubled the budget to $70K; launched Atlas, an "
            "operations platform."
        )

        self.assertFalse(report.passed)
        self.assertIn("cohesion.tacked_claim", {item.code for item in report.issues})
        self.assertIn("move the other claim", report.repair_brief().casefold())

    def test_flags_vague_language_without_discarding_an_otherwise_strong_fact(self) -> None:
        report = analyze_bullet_editorially(
            "Deployed workflow automation across 2 businesses, saving 10 hrs/wk for "
            "higher-value work."
        )

        self.assertTrue(report.passed)
        self.assertIn("language.vague", {item.code for item in report.issues})

    def test_rejects_participation_and_repository_activity_as_impact(self) -> None:
        report = analyze_bullet_editorially(
            "Helped with various features across 12 commits and 30 files."
        )

        self.assertFalse(report.passed)
        codes = {item.code for item in report.issues}
        self.assertIn("action.missing", codes)
        self.assertIn("content.low_signal", codes)
        self.assertIn("proof.activity_accounting", codes)

    def test_reports_supported_metrics_that_the_draft_left_unused(self) -> None:
        report = analyze_bullet_editorially(
            "Engineered a Python service with authenticated request handling for customers.",
            supporting_text="Reduced latency by 40% across 8 services.",
        )

        self.assertEqual(report.supported_metrics_not_used, ("40%", "8"))

    def test_parses_a_complete_latex_resume_and_reports_cross_bullet_leads(self) -> None:
        report = analyze_resume_editorially(
            r"""\begin{document}
\section{Projects}
\resumeItem{Built a Python API serving 100 users with authenticated requests.}
\resumeItem{Built a React dashboard supporting 8 teams with live incident status.}
\end{document}
"""
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.bullets), 2)
        self.assertEqual(report.duplicate_lead_verbs, ("built",))


if __name__ == "__main__":
    unittest.main()
