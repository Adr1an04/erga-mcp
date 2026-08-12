from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.applications.navigator import (
    build_research_navigator,
    research_stage_for_status,
)
from erga_mcp.tracking.tracker import TrackerEntry


class ResearchNavigatorTests(unittest.TestCase):
    def test_flags_a_tracked_repository_instead_of_presenting_it_as_a_job_posting(self) -> None:
        entry = TrackerEntry(
            cycle="Unscheduled",
            company="Github",
            role="Job Opportunity",
            location="",
            source_url="https://github.com/example/career-tool.git",
            status="OA",
            applied="",
            next_action="Complete assessment",
        )

        navigator = build_research_navigator(entry=entry, package_dir=None)

        self.assertIsNotNone(navigator.source_warning)
        self.assertEqual(len(navigator.links), 1)
        rendered = navigator.card.as_text()
        self.assertIn("Source problem", rendered)
        self.assertIn("not a job posting", rendered)
        self.assertEqual(
            [action.action_id for action in navigator.card.actions],
            ["research.back"],
        )

    def test_prefers_ranked_research_index_and_shows_coverage_over_legacy_junk(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory)
            research_dir = package_dir / "research"
            research_dir.mkdir()
            (research_dir / "secondary-research.md").write_text(
                "[Irrelevant](https://example.test/generic-github-mention)\n",
                encoding="utf-8",
            )
            (research_dir / "discovery-research.md").write_text(
                "# Ranked research\n",
                encoding="utf-8",
            )
            (research_dir / "discovery-research.json").write_text(
                json.dumps(
                    {
                        "statistics": {
                            "candidates_reviewed": 37,
                            "sources_retained": 8,
                            "sources_rejected": 29,
                        },
                        "coverage": [
                            {"label": "Official role source", "covered": True},
                            {"label": "Assessment-process source", "covered": False},
                        ],
                        "sources": [
                            {
                                "title": "Example engineering interview",
                                "url": "https://engineering.example.test/interview",
                                "trust": "official",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            entry = TrackerEntry(
                cycle="Summer 2027",
                company="Example",
                role="Software Engineer Intern",
                location="Remote",
                source_url="https://jobs.example.test/role",
                status="OA",
                applied="2026-08-01",
                next_action="Complete assessment",
            )

            navigator = build_research_navigator(entry=entry, package_dir=package_dir)

        rendered = navigator.card.as_text()
        self.assertIn("37 candidates reviewed", rendered)
        self.assertIn("Assessment-process source: gap", rendered)
        self.assertIn("https://engineering.example.test/interview", rendered)
        self.assertNotIn("generic-github-mention", rendered)

    def test_research_becomes_available_at_oa_and_later_active_stages(self) -> None:
        self.assertIsNone(research_stage_for_status("Applied"))
        self.assertEqual(research_stage_for_status("Online Assessment"), "oa")
        self.assertEqual(research_stage_for_status("Interview"), "interview")
        self.assertEqual(research_stage_for_status("Offer"), "offer")
        self.assertIsNone(research_stage_for_status("Rejected"))

    def test_builds_a_mobile_safe_view_from_saved_research(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory)
            research_dir = package_dir / "research"
            research_dir.mkdir()
            (research_dir / "role-research.md").write_text(
                "# Example — Engineer research\n\n"
                "[Official posting](https://jobs.example.test/role?utm_source=feed)\n",
                encoding="utf-8",
            )
            (research_dir / "secondary-research.md").write_text(
                "# Secondary research\n\n"
                "[Community interview report](https://www.reddit.com/r/cscareerquestions/x)\n"
                "[Duplicate](https://www.reddit.com/r/cscareerquestions/x)\n"
                "[Local note](role-research.md)\n",
                encoding="utf-8",
            )
            (research_dir / "oa-brief.md").write_text(
                "# OA preparation brief\n",
                encoding="utf-8",
            )
            (package_dir / "Example_Resume.pdf").write_bytes(b"%PDF-fixture")
            entry = TrackerEntry(
                cycle="Summer 2027",
                company="Example",
                role="Software Engineer Intern",
                location="Remote",
                source_url="https://jobs.example.test/role",
                status="OA",
                applied="2026-08-01",
                next_action="Complete assessment by Friday",
            )

            navigator = build_research_navigator(entry=entry, package_dir=package_dir)

        self.assertEqual(navigator.stage, "oa")
        self.assertEqual(navigator.saved_artifact_count, 3)
        self.assertTrue(navigator.resume_available)
        self.assertEqual(
            [link.url for link in navigator.links],
            [
                "https://jobs.example.test/role",
                "https://www.reddit.com/r/cscareerquestions/x",
            ],
        )
        self.assertFalse(navigator.links[0].unverified)
        self.assertTrue(navigator.links[1].unverified)
        rendered = navigator.card.as_text()
        self.assertIn("OA research · Example", rendered)
        self.assertIn("[Official posting](https://jobs.example.test/role)", rendered)
        self.assertIn("Community interview report (unverified)", rendered)
        self.assertIn("Complete assessment by Friday", rendered)
        self.assertNotIn(str(package_dir), rendered)
        self.assertEqual(
            [action.action_id for action in navigator.card.actions],
            ["research.refresh", "research.brief", "research.back"],
        )


if __name__ == "__main__":
    unittest.main()
