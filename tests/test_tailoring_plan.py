from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.portfolio.inventory import ProjectCandidate
from erga_mcp.resumes.planning import (
    TailoringPlanAnswer,
    TailoringPlanOption,
    TailoringPlanQuestion,
    answer_tailoring_plan,
    approve_tailoring_plan,
    build_tailoring_plan,
    migrate_tailoring_plan_project_selection,
    reopen_previous_question,
    tailoring_plan_preferences,
)
from erga_mcp.store import ErgaStore


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

    def test_plan_auto_selects_projects_without_leaking_the_snapshot(self) -> None:
        plan = self._plan()

        self.assertEqual(plan.status, "planning")
        self.assertEqual(plan.current_question.id, "copy_strategy")
        self.assertEqual(plan.project_selection_mode, "automatic_strength")
        self.assertEqual(plan.project_ids, ())
        self.assertNotIn("portfolio", {question.id for question in plan.questions})
        payload = plan.as_public_dict()
        self.assertNotIn("job_snapshot", payload)
        self.assertNotIn("job_description", payload)
        self.assertEqual(payload["current_question"]["id"], "copy_strategy")
        self.assertEqual(payload["project_selection"]["mode"], "automatic_strength")

    def test_answers_are_sequential_reviewable_and_resolve_generation_preferences(self) -> None:
        plan = self._plan()
        with self.assertRaisesRegex(ValueError, "current question"):
            answer_tailoring_plan(plan, question_id="portfolio", option_id="balanced")

        plan = answer_tailoring_plan(
            plan,
            question_id="copy_strategy",
            option_id="synthesize",
        )

        self.assertEqual(plan.status, "review")
        self.assertIsNone(plan.current_question)
        preferences = tailoring_plan_preferences(plan)
        self.assertEqual(preferences.project_ids, ())
        self.assertTrue(preferences.allow_ai_synthesis)
        self.assertEqual(preferences.emphasis, "balanced")

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
                question_id="copy_strategy",
                option_id="synthesize",
            )
            store.save_tailoring_plan(answered)

            self.assertEqual(store.get_tailoring_plan(plan.id), answered)
            self.assertEqual(store.list_tailoring_plans()[0].id, plan.id)

    def test_legacy_portfolio_choice_migrates_to_automatic_strength_selection(self) -> None:
        plan = self._plan()
        legacy_question = TailoringPlanQuestion(
            id="portfolio",
            prompt="Which project mix?",
            options=(
                TailoringPlanOption(
                    id="balanced",
                    label="Balanced",
                    description="Legacy manual choice.",
                    project_ids=("robotics", "platform", "tooling"),
                    project_titles=("Robotics", "Platform", "Tooling"),
                ),
            ),
        )
        legacy = replace(
            plan,
            questions=(legacy_question, *plan.questions),
            answers=(
                TailoringPlanAnswer("portfolio", "balanced"),
                TailoringPlanAnswer("copy_strategy", "synthesize"),
            ),
            status="ready",
            project_selection_mode="legacy_question",
            project_ids=("robotics", "platform", "tooling"),
            project_titles=("Robotics", "Platform", "Tooling"),
        )

        migrated = migrate_tailoring_plan_project_selection(
            legacy, now=datetime(2026, 8, 12, tzinfo=UTC)
        )

        self.assertEqual(migrated.status, "review")
        self.assertEqual(migrated.project_selection_mode, "automatic_strength")
        self.assertEqual(migrated.project_ids, ())
        self.assertEqual([question.id for question in migrated.questions], ["copy_strategy"])
        self.assertEqual(migrated.answers, (TailoringPlanAnswer("copy_strategy", "synthesize"),))


if __name__ == "__main__":
    unittest.main()
