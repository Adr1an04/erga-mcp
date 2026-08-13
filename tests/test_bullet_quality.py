from __future__ import annotations

import unittest

from erga_mcp.portfolio.inventory import ProjectCandidate
from erga_mcp.resumes.quality import (
    analyze_bullet_quality,
    build_project_identity_profile,
    bullet_semantic_overlap,
    compare_project_profiles,
    compare_resume_to_master,
    portfolio_quality_report,
    rank_project_candidates,
    select_quality_project_ids,
)


def _candidate(
    project_id: str,
    title: str,
    bullets: tuple[str, ...],
    *,
    tags: tuple[str, ...],
) -> ProjectCandidate:
    evidence_ids = tuple(f"ev_{project_id}_{index}" for index in range(len(bullets)))
    rendered = "\n".join(rf"\resumeItem{{{bullet}}}" for bullet in bullets)
    return ProjectCandidate(
        id=project_id,
        title=title,
        latex=(
            rf"\resumeProjectHeading{{\textbf{{{title}}}}}{{}}\n"
            rf"\resumeItemListStart\n{rendered}\n\resumeItemListEnd"
        ),
        evidence_ids=evidence_ids,
        bullet_evidence_ids=tuple((evidence_id,) for evidence_id in evidence_ids),
        tags=tags,
    )


class BulletQualityTests(unittest.TestCase):
    def test_scores_supported_outcomes_above_repository_activity_accounting(self) -> None:
        outcome = analyze_bullet_quality(
            "Optimized streaming inference to 24 ms latency for 3 real-time sensor models.",
            evidence_count=1,
        )
        activity = analyze_bullet_quality(
            "Changed 71 implementation files and added 4,200 lines of code.",
            evidence_count=1,
        )

        self.assertIn("performance", outcome.metric_categories)
        self.assertIn("scale", outcome.metric_categories)
        self.assertEqual(outcome.evidence_tier, "A")
        self.assertGreaterEqual(outcome.score, 80)
        self.assertEqual(activity.evidence_tier, "C")
        self.assertIn("activity accounting", activity.issues)
        self.assertLess(activity.score, outcome.score)

    def test_project_profile_exposes_distinct_narrative_and_metric_identity(self) -> None:
        candidate = _candidate(
            "robotics",
            "Autonomous Robot",
            (
                "Engineered ROS2 navigation across 3 LiDAR sensors for autonomous mapping.",
                "Reduced path-planning latency to 18 ms through C++ graph-search optimization.",
            ),
            tags=("c++", "python", "ros2", "lidar", "robotics", "real-time"),
        )

        profile = build_project_identity_profile(candidate)

        self.assertIn("real-time / robotics", profile.narrative_signals)
        self.assertIn("systems / infrastructure", profile.narrative_signals)
        self.assertIn("performance", profile.metric_categories)
        self.assertIn("scale", profile.metric_categories)
        self.assertGreaterEqual(profile.quality_score, 70)
        self.assertEqual(profile.evidence_tier, "A")

    def test_pairwise_comparison_rewards_complementary_project_stories(self) -> None:
        first = build_project_identity_profile(
            _candidate(
                "api-one",
                "API One",
                ("Scaled a Python API to process 12,000 requests across 8 endpoints.",),
                tags=("python", "fastapi", "api", "platform", "scale"),
            )
        )
        redundant = build_project_identity_profile(
            _candidate(
                "api-two",
                "API Two",
                ("Built a Python API processing 9,000 requests across 6 endpoints.",),
                tags=("python", "fastapi", "api", "platform", "scale"),
            )
        )
        complementary = build_project_identity_profile(
            _candidate(
                "robotics",
                "Robotics",
                ("Reduced C++ motion-planning latency to 18 ms for autonomous navigation.",),
                tags=("c++", "ros2", "robotics", "real-time", "performance"),
            )
        )

        repeated = compare_project_profiles(first, redundant)
        differentiated = compare_project_profiles(first, complementary)

        self.assertGreater(repeated.overlap_score, differentiated.overlap_score)
        self.assertLess(repeated.differentiation_score, differentiated.differentiation_score)
        self.assertIn("scale", repeated.shared_metric_categories)
        self.assertIn("performance", differentiated.right_only_metric_categories)

    def test_semantic_overlap_catches_name_swappable_bullets_beyond_lead_verbs(self) -> None:
        repeated = bullet_semantic_overlap(
            "Built a Python platform serving 5,000 users through 8 API routes.",
            "Developed a Python platform serving 4,000 users through 7 API routes.",
        )
        distinct = bullet_semantic_overlap(
            "Built a Python platform serving 5,000 users through 8 API routes.",
            "Optimized CUDA inference to 18 ms for autonomous robot navigation.",
        )

        self.assertGreaterEqual(repeated, 70)
        self.assertLess(distinct, 30)

    def test_portfolio_report_surfaces_repeated_metrics_and_name_swap_risk(self) -> None:
        projects = (
            _candidate(
                "service-one",
                "Service One",
                ("Built a Python service supporting 5 API routes.",),
                tags=("python", "api", "platform"),
            ),
            _candidate(
                "service-two",
                "Service Two",
                ("Developed a Python service supporting 6 API routes.",),
                tags=("python", "api", "platform"),
            ),
            _candidate(
                "ml-system",
                "ML System",
                ("Optimized CUDA inference to 22 ms latency for 4 vision models.",),
                tags=("cuda", "pytorch", "ml", "performance"),
            ),
        )

        report = portfolio_quality_report(projects)

        self.assertEqual(len(report.project_profiles), 3)
        self.assertIn("functional scope", report.repeated_metric_categories)
        self.assertTrue(
            any(pair.overlap_score >= 60 for pair in report.pairwise_comparisons),
            report.pairwise_comparisons,
        )
        self.assertGreaterEqual(report.distinct_narrative_count, 2)
        self.assertTrue(report.issues)

    def test_master_parity_is_a_quality_floor_and_keeps_template_structure(self) -> None:
        master = r"""\section{Projects}
\resumeProjectHeading{\textbf{Robotics} $|$ \textit{C++, ROS2}}{}
\resumeItem{Reduced path-planning latency to 18 ms for 3 autonomous sensors.}
\section{Technical Skills}
\textbf{Languages:} C++, Python
"""
        proposal = master.replace(
            "Reduced path-planning latency to 18 ms for 3 autonomous sensors.",
            "Changed 71 implementation files and added 4,200 lines of code.",
        )

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.template_contract, "preserved")
        self.assertLess(comparison.proposal_quality_score, comparison.master_quality_score)
        self.assertIn("proposal falls below master bullet quality", comparison.issues)

    def test_template_contract_allows_fewer_projects_but_rejects_heading_arity_drift(self) -> None:
        heading = r"\resumeProjectHeading{\textbf{Project}}{Python}"
        master = "\n".join(
            (
                r"\section{Projects}",
                *(
                    f"{heading}\n\\resumeItemListStart\n"
                    r"\resumeItem{Built a Python service supporting 5 API routes.}"
                    "\n\\resumeItemListEnd"
                    for _ in range(4)
                ),
            )
        )
        proposal = "\n".join(master.splitlines()[:-4])

        self.assertEqual(compare_resume_to_master(master, proposal).template_contract, "preserved")
        malformed = proposal.replace(heading, r"\resumeProjectHeading{\textbf{Project}}", 1)
        self.assertEqual(compare_resume_to_master(master, malformed).template_contract, "changed")

    def test_master_parity_tolerates_inherited_semantic_redundancy_only(self) -> None:
        master = r"""\section{Experience}
\resumeItem{Built a Python API serving 5,000 users across 8 routes.}
\resumeItem{Developed a Python API serving 5,000 users across 8 routes.}
\section{Projects}
\resumeItem{Optimized CUDA inference to 18 ms for autonomous navigation.}
"""
        self.assertFalse(compare_resume_to_master(master, master).semantic_redundancy_pairs)
        proposal = master.replace(
            "Optimized CUDA inference to 18 ms for autonomous navigation.",
            "Engineered a Python API serving 5,000 users across 8 routes.",
        )
        self.assertTrue(compare_resume_to_master(master, proposal).semantic_redundancy_pairs)

    def test_master_parity_rejects_material_content_loss(self) -> None:
        bullets = tuple(
            f"\\resumeItem{{Built service {index} supporting {index + 4} API routes.}}"
            for index in range(1, 5)
        )
        master = "\\section{Projects}\n" + "\n".join(bullets)
        proposal = "\\section{Projects}\n" + bullets[0]

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.content_retention_percent, 25)
        self.assertIn("removes too much", " ".join(comparison.issues))

    def test_master_parity_counts_standard_latex_items_and_rejects_deleting_them(self) -> None:
        master = r"""\begin{document}
\section{Projects}
\begin{itemize}
\item Built a Python service supporting 5 API routes.
\item Reduced request latency to 18 ms for 100 users.
\end{itemize}
\end{document}
"""
        proposal = master.replace(
            "\\item Built a Python service supporting 5 API routes.\n"
            "\\item Reduced request latency to 18 ms for 100 users.\n",
            "",
        )

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.content_retention_percent, 0)

    def test_master_parity_counts_custom_and_standard_items_in_one_template(self) -> None:
        master = r"""\begin{document}
\section{Experience}
\resumeItem{Built a Python service supporting 5 API routes.}
\section{Projects}
\begin{itemize}
\item Reduced request latency to 18 ms for 100 users.
\item Validated 20 authenticated API routes.
\end{itemize}
\end{document}
"""
        proposal = master.replace(
            "\\item Reduced request latency to 18 ms for 100 users.\n"
            "\\item Validated 20 authenticated API routes.\n",
            "",
        )

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.content_retention_percent, 33)

    def test_master_parity_rejects_body_font_drift(self) -> None:
        master = r"""\begin{document}
\section{Projects}
\small
\resumeItem{Built a Python service supporting 5 API routes.}
\end{document}
"""
        proposal = master.replace(r"\small", r"\tiny")

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.template_contract, "changed")

    def test_master_parity_rejects_font_environment_and_family_drift(self) -> None:
        master = r"""\begin{document}
\section{Projects}
\resumeItem{Built a Python service supporting 5 API routes.}
\end{document}
"""
        for addition in (
            r"\begin{small}" + "\n" + r"\end{small}",
            r"\fontfamily{cmss}\selectfont",
        ):
            with self.subTest(addition=addition):
                proposal = master.replace(
                    r"\section{Projects}", r"\section{Projects}" + "\n" + addition
                )
                self.assertFalse(compare_resume_to_master(master, proposal).passed)

    def test_master_parity_rejects_project_name_skill_column_drift(self) -> None:
        master = (
            r"\resumeProjectHeading{\textbf{API} $|$ \emph{Python}}{2026}"
            "\n"
            r"\resumeItem{Built a Python service supporting 5 API routes.}"
        )
        proposal = master.replace(
            r"\resumeProjectHeading{\textbf{API} $|$ \emph{Python}}{2026}",
            r"\resumeProjectHeading{\textbf{API}}{Python}",
        )

        comparison = compare_resume_to_master(master, proposal)

        self.assertFalse(comparison.passed)
        self.assertEqual(comparison.template_contract, "changed")

    def test_master_parity_preserves_mixed_heading_format_multiplicity(self) -> None:
        inline = r"\resumeProjectHeading{\textbf{API} $|$ \emph{Python}}{2026}"
        columns = r"\resumeProjectHeading{\textbf{API}}{\emph{Python}}"
        master = "\n".join((inline, inline.replace("API", "CLI"), columns))
        proposal = master.replace(inline.replace("API", "CLI"), columns, 1)

        self.assertFalse(compare_resume_to_master(master, proposal).passed)

    def test_master_parity_rejects_plain_project_name_skill_column_drift(self) -> None:
        master = r"""\section{Projects}
\textbf{API} $|$ \emph{Python} \hfill 2026
\resumeItem{Built a Python service supporting 5 API routes.}
"""
        proposal = master.replace(
            r"\textbf{API} $|$ \emph{Python} \hfill 2026",
            r"\textbf{API} \hfill \emph{Python} \hfill 2026",
        )

        self.assertFalse(compare_resume_to_master(master, proposal).passed)

    def test_master_parity_rejects_plain_styled_heading_reordering_or_columns(self) -> None:
        master = r"""\section{Projects}
\textbf{API} \emph{Python}
\resumeItem{Built a Python service supporting 5 API routes.}
"""
        proposals = (
            master.replace(r"\textbf{API} \emph{Python}", r"\emph{Python} \textbf{API}"),
            master.replace(
                r"\textbf{API} \emph{Python}",
                r"\begin{tabular}{lr}\textbf{API} & \emph{Python}\end{tabular}",
            ),
        )

        for proposal in proposals:
            with self.subTest(proposal=proposal):
                self.assertFalse(compare_resume_to_master(master, proposal).passed)

    def test_ranked_catalogue_explains_selected_and_rejected_projects(self) -> None:
        projects = (
            _candidate(
                "robotics",
                "Robotics",
                ("Reduced C++ navigation latency to 18 ms for 3 autonomous sensors.",),
                tags=("c++", "robotics", "real-time", "performance"),
            ),
            _candidate(
                "weak-api",
                "Weak API",
                ("Changed 71 implementation files for a Python API.",),
                tags=("python", "api", "files"),
            ),
            _candidate(
                "platform",
                "Platform",
                ("Scaled a Rust event platform to process 12,000 events per day.",),
                tags=("rust", "platform", "events", "scale"),
            ),
        )

        decision = rank_project_candidates(
            projects,
            "Required: C++ real-time systems and performance",
            selected_ids=("robotics",),
        )

        self.assertEqual(decision.selected[0].project_id, "robotics")
        rejected = {item.project_id: item for item in decision.alternatives}
        self.assertEqual(set(rejected), {"weak-api", "platform"})
        self.assertIn("lower role relevance", rejected["platform"].reasons)
        self.assertIn("activity accounting", rejected["weak-api"].reasons)

    def test_quality_selection_replaces_a_redundant_keyword_heavy_project(self) -> None:
        projects = (
            _candidate(
                "api-a",
                "API A",
                ("Scaled a Python API to 12,000 requests across 8 routes.",),
                tags=("python", "api", "distributed", "systems"),
            ),
            _candidate(
                "api-churn",
                "API Churn",
                ("Changed 71 Python API files for distributed systems work.",),
                tags=("python", "api", "distributed", "systems", "files"),
            ),
            _candidate(
                "reliable-service",
                "Reliable Service",
                ("Validated 40 integration tests to prevent 6 request failure modes.",),
                tags=("python", "testing", "reliability"),
            ),
        )

        selected = select_quality_project_ids(
            projects,
            "Required: Python API distributed systems testing and reliability",
            project_count=2,
        )

        self.assertEqual(selected, ("api-a", "reliable-service"))

    def test_quality_selection_falls_back_to_quality_for_sparse_posting(self) -> None:
        projects = (
            _candidate("robotics", "Robotics", ("Reduced navigation latency to 18 ms.",), tags=()),
            _candidate(
                "platform",
                "Platform",
                ("Scaled event processing to 12,000 daily events.",),
                tags=(),
            ),
            _candidate(
                "testing", "Testing", ("Validated 40 tests across 6 failure modes.",), tags=()
            ),
        )

        selected = select_quality_project_ids(
            projects,
            "Software Engineer Intern",
            project_count=3,
        )
        decision = rank_project_candidates(
            projects,
            "Software Engineer Intern",
            selected_ids=selected,
        )

        self.assertEqual(len(selected), 3)
        self.assertTrue(
            all(
                "insufficient role specificity; ranked by approved quality and contrast"
                in item.reasons
                for item in decision.selected
            )
        )


if __name__ == "__main__":
    unittest.main()
