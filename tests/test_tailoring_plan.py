from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.project_inventory import ProjectCandidate
from erga_mcp.store import ErgaStore
from erga_mcp.tailoring_plan import (
    answer_tailoring_plan,
    approve_tailoring_plan,
    build_tailoring_plan,
    reopen_previous_question,
    tailoring_plan_preferences,
)


def _candidate(
    project_id: str,
    title: str,
    bullet: str,
    *,
    tags: tuple[str, ...],
) -> ProjectCandidate:
    evidence_id = f"ev_{project_id}"
    return ProjectCandidate(
        id=project_id,
        title=title,
        latex=(
            rf"\resumeProjectHeading{{\textbf{{{title}}}}}{{}}\n"
            rf"\resumeItemListStart\n\resumeItem{{{bullet}}}\n\resumeItemListEnd"
        ),
        evidence_ids=(evidence_id,),
        bullet_evidence_ids=((evidence_id,),),
        tags=tags,
    )


class TailoringPlanTests(unittest.TestCase):
    def _plan(self):
        return build_tailoring_plan(
            job_url="https://jobs.example.test/software-engineer",
            company="Example",
            role="Software Engineer Intern",
            job_snapshot="<main>untrusted fixture posting</main>",
            job_description=(
                "Build Python and C++ real-time systems, reliable developer tools, and scalable "
                "APIs."
            ),
            candidates=(
                _candidate(
                    "robotics",
                    "Robotics",
                    "Reduced C++ navigation latency to 18 ms across 3 LiDAR sensors.",
                    tags=("c++", "robotics", "real-time", "lidar"),
                ),
                _candidate(
                    "platform",
                    "Platform",
                    "Scaled a Python API to 12,000 requests across 8 endpoints.",
                    tags=("python", "api", "platform", "scale"),
                ),
                _candidate(
                    "tooling",
                    "Tooling",
                    "Automated 14 release workflows with Python validation and testing.",
                    tags=("python", "testing", "developer-tools", "automation"),
                ),
                _candidate(
                    "ml",
                    "ML",
                    "Optimized CUDA inference to 22 ms latency for 4 vision models.",
                    tags=("cuda", "pytorch", "ml", "performance"),
                ),
            ),
            project_count=3,
            now=datetime(2026, 8, 11, tzinfo=UTC),
        )

    def test_plan_exposes_short_emoji_choices_without_leaking_the_snapshot(self) -> None:
        plan = self._plan()

        self.assertEqual(plan.status, "planning")
        self.assertEqual(plan.current_question.id, "portfolio")
        self.assertGreaterEqual(len(plan.current_question.options), 2)
        self.assertLessEqual(len(plan.current_question.options), 3)
        self.assertTrue(all(option.label[0] in "⚖️🎯💎" for option in plan.current_question.options))
        payload = plan.as_public_dict()
        self.assertNotIn("job_snapshot", payload)
        self.assertNotIn("job_description", payload)
        self.assertEqual(payload["current_question"]["id"], "portfolio")

    def test_answers_are_sequential_reviewable_and_resolve_generation_preferences(self) -> None:
        plan = self._plan()
        with self.assertRaisesRegex(ValueError, "current question"):
            answer_tailoring_plan(plan, question_id="copy_strategy", option_id="synthesize")

        portfolio = plan.current_question.options[0]
        plan = answer_tailoring_plan(
            plan,
            question_id="portfolio",
            option_id=portfolio.id,
        )
        self.assertEqual(plan.current_question.id, "copy_strategy")
        plan = answer_tailoring_plan(
            plan,
            question_id="copy_strategy",
            option_id="synthesize",
        )

        self.assertEqual(plan.status, "review")
        self.assertIsNone(plan.current_question)
        preferences = tailoring_plan_preferences(plan)
        self.assertEqual(preferences.project_ids, portfolio.project_ids)
        self.assertTrue(preferences.allow_ai_synthesis)
        self.assertEqual(preferences.emphasis, portfolio.emphasis)

        ready = approve_tailoring_plan(plan)
        self.assertEqual(ready.status, "ready")
        revised = reopen_previous_question(ready)
        self.assertEqual(revised.status, "planning")
        self.assertEqual(revised.current_question.id, "copy_strategy")

    def test_store_round_trips_private_plan_state_and_updates_answers(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            plan = self._plan()
            store.save_tailoring_plan(plan)

            loaded = store.get_tailoring_plan(plan.id)
            self.assertEqual(loaded, plan)
            assert loaded is not None
            answered = answer_tailoring_plan(
                loaded,
                question_id="portfolio",
                option_id=loaded.current_question.options[0].id,
            )
            store.save_tailoring_plan(answered)

            self.assertEqual(store.get_tailoring_plan(plan.id), answered)
            self.assertEqual(store.list_tailoring_plans()[0].id, plan.id)


if __name__ == "__main__":
    unittest.main()
