from __future__ import annotations

import unittest

from erga_mcp.portfolio.inventory import ProjectCandidate
from erga_mcp.resumes.quality import (
    analyze_bullet_quality,
    build_project_identity_profile,
    bullet_semantic_overlap,
    compare_project_profiles,
    portfolio_quality_report,
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


if __name__ == "__main__":
    unittest.main()
