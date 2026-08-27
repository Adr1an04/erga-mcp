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
    def test_seeded_master_bullet_accepts_standard_latex_apostrophe_escape(self) -> None:
        master = r"""\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{President}{Jan 2025 -- Present}{Knight Hacks}{Orlando, FL}
\resumeItemListStart
\resumeItem{Directed UCF\'s largest hackathon for 1,000+ attendees.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        evidence = Evidence("ev_master", "master.tex", master, True, datetime.now(UTC))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"
            sync_experience_inventory_from_master(
                path, master_latex=master, evidence_id=evidence.id
            )

            candidates = load_experience_inventory(path, [evidence])

        self.assertIn(r"UCF\'s largest hackathon", candidates[0].bullets[0].latex)

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

    def test_sync_keeps_repeated_role_dates_as_distinct_entries(self) -> None:
        master = r"""\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Software Engineer Intern}{Jan 2026 -- Apr 2026}{Example}{Remote}
\resumeItemListStart
\resumeItem{Built the winter platform with 12 services.}
\resumeItemListEnd
\resumeSubheading{Software Engineer Intern}{May 2025 -- Aug 2025}{Example}{Remote}
\resumeItemListStart
\resumeItem{Built the summer platform with 8 services.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        evidence = Evidence("ev_master", "master.tex", master, True, datetime.now(UTC))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "experience.json"
            sync_experience_inventory_from_master(
                path, master_latex=master, evidence_id=evidence.id
            )
            candidates = load_experience_inventory(path, [evidence])

        self.assertEqual(len(candidates), 2)
        self.assertEqual(
            [candidate.entry_terms for candidate in candidates],
            [("Jan 2026 -- Apr 2026",), ("May 2025 -- Aug 2025",)],
        )

    def test_tailoring_uses_dates_to_separate_repeated_roles(self) -> None:
        section = r"""\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Software Engineer Intern}{Jan 2026 -- Apr 2026}{Example}{Remote}
\resumeItemListStart
\resumeItem{Original winter bullet.}
\resumeItemListEnd
\resumeSubheading{Software Engineer Intern}{May 2025 -- Aug 2025}{Example}{Remote}
\resumeItemListStart
\resumeItem{Original summer bullet.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        candidates = (
            ExperienceCandidate(
                id="example-winter",
                title="Software Engineer Intern",
                company="Example",
                match_terms=("Software Engineer Intern", "Example"),
                bullets=(ExperienceBullet("Built winter CUDA systems.", ("ev-w",)),),
                entry_terms=("Jan 2026 -- Apr 2026",),
            ),
            ExperienceCandidate(
                id="example-summer",
                title="Software Engineer Intern",
                company="Example",
                match_terms=("Software Engineer Intern", "Example"),
                bullets=(ExperienceBullet("Built summer API systems.", ("ev-s",)),),
                entry_terms=("May 2025 -- Aug 2025",),
            ),
        )

        tailored, _, _, _, report = _tailor_experience_from_inventory(
            section,
            "CUDA API systems",
            candidates,
            minimum_bullets=1,
            maximum_bullets=1,
        )

        self.assertIn("Built winter CUDA systems", tailored)
        self.assertIn("Built summer API systems", tailored)
        self.assertEqual(
            [row["candidate_ids"] for row in report["selected"]],
            [["example-winter"], ["example-summer"]],
        )

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

    def test_role_relevance_earns_extra_experience_bullets_above_the_floor(self) -> None:
        section = r"""
\resumeSubHeadingListStart
\resumeSubheading{President}{2025 -- Present}{Student Group}{Remote}
\resumeItemListStart
\resumeItem{Led a student organization.}
\resumeItem{Managed its annual budget.}
\resumeItemListEnd
\resumeSubheading{Systems Engineer}{2026}{Example Labs}{Remote}
\resumeItemListStart
\resumeItem{Built a CUDA runtime diagnostic.}
\resumeItem{Optimized Python inference latency.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        candidates = (
            ExperienceCandidate(
                id="leadership",
                title="President",
                company="Student Group",
                match_terms=("President", "Student Group"),
                bullets=(
                    ExperienceBullet("Led a 40-person student organization.", ("ev-user",)),
                    ExperienceBullet("Managed a verified annual program budget.", ("ev-user",)),
                ),
            ),
            ExperienceCandidate(
                id="systems",
                title="Systems Engineer",
                company="Example Labs",
                match_terms=("Systems Engineer", "Example Labs"),
                bullets=(
                    ExperienceBullet(
                        "Built a CUDA runtime diagnostic.", ("ev-user",), tags=("cuda",)
                    ),
                    ExperienceBullet(
                        "Optimized Python inference latency.",
                        ("ev-user",),
                        tags=("python", "inference", "latency"),
                    ),
                ),
            ),
        )

        tailored, _, _, _, selection = _tailor_experience_from_inventory(
            section,
            "Required: CUDA and Python inference performance.",
            candidates,
            minimum_bullets=1,
            maximum_bullets=2,
        )

        entries = selection["selected"]
        self.assertEqual(len(entries[0]["selected_bullets"]), 1)
        self.assertEqual(len(entries[1]["selected_bullets"]), 2)
        self.assertEqual(tailored.count(r"\resumeItem{"), 3)

    def test_source_limited_role_uses_the_better_single_inventory_bullet(self) -> None:
        section = r"""
\resumeSubHeadingListStart
\resumeSubheading{Engineer}{2026}{Example Labs}{Remote}
\resumeItemListStart
\resumeItem{Built a Python tool saving 10 labor hours weekly for higher-value work.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""
        candidate = ExperienceCandidate(
            id="example-engineer",
            title="Engineer",
            company="Example Labs",
            match_terms=("Engineer", "Example Labs"),
            bullets=(
                ExperienceBullet(
                    "Built a Python tool saving 10 aggregate labor hours weekly.",
                    ("ev-user",),
                    tags=("python",),
                ),
            ),
        )

        tailored, _, _, claims, _ = _tailor_experience_from_inventory(
            section,
            "Python engineering role",
            (candidate,),
            minimum_bullets=2,
            maximum_bullets=2,
        )

        self.assertIn("10 aggregate labor hours weekly", tailored)
        self.assertNotIn("higher-value work", tailored)
        self.assertEqual(len(claims), 1)


if __name__ == "__main__":
    unittest.main()
