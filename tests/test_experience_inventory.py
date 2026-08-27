from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import Evidence
from erga_mcp.resumes.experience_inventory import (
    ExperienceBullet,
    ExperienceCandidate,
    ExperienceMetric,
    add_user_experience_bullet,
    load_experience_inventory,
    sync_experience_inventory_from_master,
)
from erga_mcp.resumes.tailoring import _tailor_experience_from_inventory


def _evidence(identifier: str, *, source_ref: str = "master-resume:synthetic") -> Evidence:
    return Evidence(identifier, source_ref, "Synthetic approved source.", True, datetime.now(UTC))


class ExperienceInventoryTests(unittest.TestCase):
    def test_user_added_bullet_auto_bolds_confirmed_metrics(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"

            candidate_id, count = add_user_experience_bullet(
                path,
                title="Software Engineer",
                company="Example Labs",
                text="Improved 12 services and reduced failures by 40%.",
                evidence_id="ev-user",
                tags=("reliability",),
            )
            candidate = load_experience_inventory(path, [_evidence("ev-user")])[0]

            self.assertEqual(candidate_id, "example-labs-software-engineer")
            self.assertEqual(count, 1)
            self.assertIn(r"\textbf{12}", candidate.bullets[0].latex)
            self.assertIn(r"\textbf{40\%}", candidate.bullets[0].latex)
            self.assertEqual(
                {metric.value for metric in candidate.bullets[0].metrics},
                {"12", "40%"},
            )

    def test_requires_provenance_for_every_numeric_claim(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "example-engineer",
                            "title": "Software Engineer",
                            "company": "Example",
                            "match_terms": ["Software Engineer", "Example"],
                            "bullets": [
                                {
                                    "latex": "Shipped 12 services for the platform.",
                                    "evidence_ids": ["ev-user"],
                                    "tags": ["platform"],
                                    "metrics": [],
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "numeric claims require provenance: 12"):
                load_experience_inventory(path, [_evidence("ev-user")])

    def test_git_cannot_be_used_as_impact_metric_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "example-engineer",
                            "title": "Software Engineer",
                            "company": "Example",
                            "match_terms": ["Software Engineer", "Example"],
                            "bullets": [
                                {
                                    "latex": "Reduced latency by 40%.",
                                    "evidence_ids": ["ev-git"],
                                    "tags": ["performance"],
                                    "metrics": [
                                        {
                                            "value": "40%",
                                            "provenance": "git_observed",
                                            "kind": "performance",
                                            "evidence_ids": ["ev-git"],
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "implementation/test-scope"):
                load_experience_inventory(
                    path,
                    [_evidence("ev-git", source_ref="git:example/repository")],
                )

    def test_sync_seeds_roles_and_user_confirmed_metrics_from_master(self) -> None:
        master = r"""\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Software Engineer}{May 2025 -- Present}{Example Labs}{Remote}
\resumeItemListStart
\resumeItem{Shipped \textbf{12 services} used across \textbf{3 teams}.}
\resumeItem{Built a typed deployment workflow in Python.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"

            created, additions, count = sync_experience_inventory_from_master(
                path, master_latex=master, evidence_id="ev-user"
            )
            candidates = load_experience_inventory(path, [_evidence("ev-user")])

            self.assertTrue(created)
            self.assertEqual(additions, 1)
            self.assertEqual(count, 1)
            self.assertEqual(candidates[0].title, "Software Engineer")
            self.assertEqual(candidates[0].company, "Example Labs")
            self.assertEqual(
                {metric.value for metric in candidates[0].bullets[0].metrics},
                {"12", "3"},
            )
            self.assertIn(r"\textbf{12 services}", candidates[0].bullets[0].latex)

    def test_tailoring_selects_distinct_role_bullets_without_touching_heading(self) -> None:
        section = r"""
\resumeSubHeadingListStart
\resumeSubheading{Software Engineer}{2025 -- Present}{Example Labs}{Remote}
\resumeItemListStart
\resumeItem{Maintained internal Python services and deployment tooling.}
\resumeItem{Documented on-call procedures for the engineering team.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        candidate = ExperienceCandidate(
            id="example-engineer",
            title="Software Engineer",
            company="Example Labs",
            match_terms=("Software Engineer", "Example Labs"),
            bullets=(
                ExperienceBullet(
                    latex=r"Built \textbf{12 typed Python services} for production deployments.",
                    evidence_ids=("ev-user",),
                    tags=("python", "deployment"),
                    metrics=(ExperienceMetric("12", "user_confirmed", "user_claim", ("ev-user",)),),
                ),
                ExperienceBullet(
                    latex=r"Automated Kubernetes releases with validated rollback controls.",
                    evidence_ids=("ev-user",),
                    tags=("kubernetes", "deployment"),
                ),
                ExperienceBullet(
                    latex=r"Delivered 12 Python services for production deployment workflows.",
                    evidence_ids=("ev-user",),
                    tags=("python", "deployment"),
                    metrics=(ExperienceMetric("12", "user_confirmed", "user_claim", ("ev-user",)),),
                ),
            ),
        )

        tailored, _, changed, claims, selection = _tailor_experience_from_inventory(
            section,
            "Python Kubernetes engineer owning deployment automation",
            (candidate,),
            minimum_bullets=2,
            maximum_bullets=2,
        )

        self.assertTrue(changed)
        self.assertIn(
            r"\resumeSubheading{Software Engineer}{2025 -- Present}{Example Labs}{Remote}",
            tailored,
        )
        self.assertIn("Kubernetes releases", tailored)
        self.assertEqual(tailored.count(r"\resumeItem{"), 2)
        self.assertEqual(len(claims), 2)
        self.assertEqual(selection["mode"], "inventory")
        self.assertTrue(all(record["evidence_ids"] == ["ev-user"] for record in claims))


if __name__ == "__main__":
    unittest.main()
