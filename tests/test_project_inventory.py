from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import Evidence
from erga_mcp.project_inventory import (
    ProjectCandidate,
    load_project_inventory,
    project_quality_issues,
    select_project_rationales,
    select_projects,
)


class ProjectInventoryTests(unittest.TestCase):
    def test_inventory_loads_explicit_github_repository_mappings(self) -> None:
        evidence = [
            Evidence("ev_ok", "Career#Project", "Verified project", True, datetime.now(UTC)),
        ]
        payload = [
            {
                "id": "api-platform",
                "title": "API Platform",
                "latex": (
                    r"\resumeProjectHeading{\textbf{API Platform}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    r"\resumeItem{Built a verified API platform.}"
                    "\n"
                    r"\resumeItemListEnd"
                ),
                "evidence_ids": ["ev_ok"],
                "bullet_evidence_ids": [["ev_ok"]],
                "tags": ["python", "api"],
                "git_repositories": ["example/api-platform"],
            }
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            projects = load_project_inventory(path, evidence)

        self.assertEqual(projects[0].git_repositories, ("example/api-platform",))

    def test_inventory_rejects_non_github_repository_mappings(self) -> None:
        evidence = [
            Evidence("ev_ok", "Career#Project", "Verified project", True, datetime.now(UTC)),
        ]
        payload = [
            {
                "id": "api-platform",
                "title": "API Platform",
                "latex": (
                    r"\resumeProjectHeading{\textbf{API Platform}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    r"\resumeItem{Built a verified API platform.}"
                    "\n"
                    r"\resumeItemListEnd"
                ),
                "evidence_ids": ["ev_ok"],
                "bullet_evidence_ids": [["ev_ok"]],
                "tags": ["python", "api"],
                "git_repositories": ["/private/local/path"],
            }
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "GitHub owner/repo"):
                load_project_inventory(path, evidence)

    def test_select_projects_prefers_role_specific_arsenal_entries_over_template_order(
        self,
    ) -> None:
        candidates = (
            ProjectCandidate(
                id="web-portal",
                title="Web Portal",
                latex=(
                    r"\resumeProjectHeading{\textbf{Web Portal} $|$ \textit{React, TypeScript}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    r"\resumeItem{Built a responsive member portal with React.}"
                    "\n"
                    r"\resumeItemListEnd"
                    "\n"
                ),
                evidence_ids=("ev_web",),
                tags=("react", "typescript", "frontend"),
            ),
            ProjectCandidate(
                id="ml-research",
                title="ML Research",
                latex=(
                    r"\resumeProjectHeading{\textbf{ML Research} $|$ \textit{Python, PyTorch}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    r"\resumeItem{Trained PyTorch models for signal classification.}"
                    "\n"
                    r"\resumeItemListEnd"
                    "\n"
                ),
                evidence_ids=("ev_ml",),
                tags=("python", "pytorch", "machine learning"),
            ),
            ProjectCandidate(
                id="service-platform",
                title="Service Platform",
                latex=(
                    r"\resumeProjectHeading{\textbf{Service Platform} $|$ "
                    r"\textit{Python, Kubernetes}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    r"\resumeItem{Built Python APIs deployed with Kubernetes for backend services.}"
                    "\n"
                    r"\resumeItemListEnd"
                    "\n"
                ),
                evidence_ids=("ev_backend",),
                tags=("python", "kubernetes", "backend", "infrastructure"),
            ),
        )

        selected = select_projects(
            candidates,
            "Required: Python, Kubernetes, backend infrastructure, and distributed systems.",
            max_projects=2,
        )

        self.assertEqual(
            [candidate.id for candidate in selected], ["service-platform", "ml-research"]
        )

    def test_required_qualifications_outweigh_incidental_responsibility_overlap(self) -> None:
        candidates = (
            ProjectCandidate(
                id="ml-workflow",
                title="ML Workflow",
                latex=(
                    r"\resumeProjectHeading{\textbf{ML Workflow}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Built Python machine learning workflows.}\n"
                    r"\resumeItemListEnd"
                ),
                evidence_ids=("ev_ml",),
                tags=("python", "machine", "learning"),
            ),
            ProjectCandidate(
                id="kubernetes-platform",
                title="Kubernetes Platform",
                latex=(
                    r"\resumeProjectHeading{\textbf{Kubernetes Platform}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Deployed containerized services on Kubernetes.}\n"
                    r"\resumeItemListEnd"
                ),
                evidence_ids=("ev_k8s",),
                tags=("kubernetes", "infrastructure"),
            ),
        )

        selected = select_projects(
            candidates,
            "Responsibilities: build Python machine learning workflows.\n"
            "Required qualifications: Kubernetes.",
            max_projects=1,
        )

        self.assertEqual([candidate.id for candidate in selected], ["kubernetes-platform"])

    def test_select_projects_excludes_candidates_below_configured_bullet_minimum(self) -> None:
        candidates = (
            ProjectCandidate(
                id="one-bullet",
                title="One Bullet",
                latex=(
                    r"\resumeProjectHeading{\textbf{One Bullet}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Built a Python system for testing.}\n"
                    r"\resumeItemListEnd"
                ),
                evidence_ids=("ev_one",),
                bullet_evidence_ids=(("ev_one",),),
                tags=("python", "testing"),
            ),
            ProjectCandidate(
                id="two-bullets",
                title="Two Bullets",
                latex=(
                    r"\resumeProjectHeading{\textbf{Two Bullets}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Built a Python system for testing.}\n"
                    r"\resumeItem{Validated deterministic project selection behavior.}\n"
                    r"\resumeItemListEnd"
                ),
                evidence_ids=("ev_two",),
                bullet_evidence_ids=(("ev_two",), ("ev_two",)),
                tags=("python", "testing"),
            ),
        )

        selected = select_projects(
            candidates,
            "Python testing systems",
            max_projects=2,
            minimum_bullets=2,
        )

        self.assertEqual([candidate.id for candidate in selected], ["two-bullets"])

    def test_select_projects_rejects_internal_git_research_prose(self) -> None:
        raw_research = ProjectCandidate(
            id="raw-research",
            title="Raw Research",
            latex=(
                r"\resumeProjectHeading{\textbf{Raw Research}}{}\n"
                r"\resumeItemListStart\n"
                r"\resumeItem{Implemented API/UI work across 2 commits and 16 files "
                r"(+477/-136 lines), covering app and apps in Git diffs.}\n"
                r"\resumeItemListEnd"
            ),
            evidence_ids=("ev_raw",),
            bullet_evidence_ids=(("ev_raw",),),
            tags=("python", "api"),
        )
        polished = ProjectCandidate(
            id="polished",
            title="Polished",
            latex=(
                r"\resumeProjectHeading{\textbf{Polished}}{}\n"
                r"\resumeItemListStart\n"
                r"\resumeItem{Built a Python API that processed 2,000+ verified submissions.}\n"
                r"\resumeItemListEnd"
            ),
            evidence_ids=("ev_polished",),
            bullet_evidence_ids=(("ev_polished",),),
            tags=("python", "api"),
        )

        selected = select_projects((raw_research, polished), "Required: Python API", max_projects=2)

        self.assertTrue(project_quality_issues(raw_research))
        self.assertEqual(project_quality_issues(polished), ())
        self.assertEqual([candidate.id for candidate in selected], ["polished"])

    def test_inventory_rejects_unapproved_or_missing_evidence(self) -> None:
        evidence = [
            Evidence("ev_ok", "Career#Project", "Verified project", True, datetime.now(UTC)),
            Evidence("ev_no", "Career#Project", "Unapproved project", False, datetime.now(UTC)),
        ]
        payload = [
            {
                "id": "bad-project",
                "title": "Bad Project",
                "latex": (
                    r"\resumeProjectHeading{\textbf{Bad Project}}{}\n"
                    r"\resumeItemListStart\n\resumeItemListEnd"
                ),
                "evidence_ids": ["ev_no"],
                "bullet_evidence_ids": [["ev_no"]],
                "tags": ["python"],
            }
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "approved evidence"):
                load_project_inventory(path, evidence)

    def test_inventory_rejects_tex_file_write_primitives_and_requires_per_bullet_evidence(
        self,
    ) -> None:
        evidence = [
            Evidence("ev_ok", "Career#Project", "Verified project", True, datetime.now(UTC)),
        ]
        payload = [
            {
                "id": "unsafe-project",
                "title": "Unsafe Project",
                "latex": (
                    r"\resumeProjectHeading{\textbf{Unsafe Project}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Verified project.}\n"
                    r"\resumeItemListEnd\n"
                    r"\openout0=/tmp/erga-proof"
                ),
                "evidence_ids": ["ev_ok"],
                "bullet_evidence_ids": [["ev_ok"]],
                "tags": ["python"],
            }
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disallowed command"):
                load_project_inventory(path, evidence)

    def test_inventory_requires_one_approved_evidence_mapping_for_each_bullet(self) -> None:
        evidence = [
            Evidence("ev_ok", "Career#Project", "Verified project", True, datetime.now(UTC)),
        ]
        payload = [
            {
                "id": "unmapped-project",
                "title": "Unmapped Project",
                "latex": (
                    r"\resumeProjectHeading{\textbf{Unmapped Project}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Verified project.}\n"
                    r"\resumeItemListEnd"
                ),
                "evidence_ids": ["ev_ok"],
                "tags": ["python"],
            }
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bullet_evidence_ids"):
                load_project_inventory(path, evidence)

    def test_selection_rationales_expose_the_matched_job_terms(self) -> None:
        candidate = ProjectCandidate(
            id="realtime-platform",
            title="Realtime Platform",
            latex=(
                r"\resumeProjectHeading{\textbf{Realtime Platform} $|$ \textit{Python, Redis}}{}\n"
                r"\resumeItemListStart\n"
                r"\resumeItem{Built Python services with Redis for real-time messaging.}\n"
                r"\resumeItemListEnd"
            ),
            evidence_ids=("ev_realtime",),
            tags=("python", "redis", "real-time"),
        )

        selections = select_project_rationales(
            (candidate,),
            "Responsibilities: build Python services for real-time communication with Redis.",
            max_projects=1,
        )

        self.assertEqual(len(selections), 1)
        self.assertEqual(selections[0].id, "realtime-platform")
        self.assertEqual(selections[0].title, "Realtime Platform")
        self.assertEqual(
            selections[0].matched_terms,
            ("python", "real", "services", "time"),
        )


if __name__ == "__main__":
    unittest.main()
