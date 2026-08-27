from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.applications.intake import select_relevant_evidence
from erga_mcp.applications.role_profile import build_role_profile
from erga_mcp.models import Evidence
from erga_mcp.resumes.claims import index_evidence_claims, supported_role_skills
from erga_mcp.resumes.planning import build_tailoring_plan
from erga_mcp.resumes.tailoring import (
    _compact_generated_entry_section,
    create_automatic_resume_proposal,
)


def _evidence(identifier: str, text: str) -> Evidence:
    return Evidence(identifier, f"synthetic:{identifier}", text, True, datetime.now(UTC))


class RoleProfileTests(unittest.TestCase):
    def test_matches_operational_paraphrases_without_authorizing_new_facts(self) -> None:
        profile = build_role_profile(
            role="Platform Engineer",
            qualifications=(
                "Must demonstrate production ownership and incident management.",
                "React experience is preferred.",
            ),
        )

        match = profile.match(
            "Owned services through on-call production support and operational ownership."
        )

        required = profile.required[0]
        self.assertIn(required.id, match.matched_requirement_ids)
        self.assertGreater(match.score, 0)
        self.assertNotIn(profile.requirements[1].id, match.matched_requirement_ids)

    def test_evidence_selection_uses_requirement_concepts_and_stays_approved_only(self) -> None:
        evidence = (
            _evidence(
                "operations",
                "Handled production support, operational ownership, and monitoring.",
            ),
            _evidence("marketing", "Created visual campaign assets for student events."),
            Evidence(
                "private",
                "synthetic:private",
                "Ran on-call incident management.",
                False,
                datetime.now(UTC),
            ),
        )

        selected = select_relevant_evidence(
            "Required: production ownership and incident response for an on-call service.",
            evidence,
        )

        self.assertEqual([item.id for item in selected], ["operations"])

    def test_claim_index_is_atomic_stable_and_explainable(self) -> None:
        source = _evidence(
            "platform",
            "Built a Python API. Reduced verified latency to 40 ms.",
        )

        first = index_evidence_claims([source], "Required Python API performance work.")
        second = index_evidence_claims([source], "Required Python API performance work.")

        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertEqual(len(first), 2)
        self.assertTrue(any(item.metrics == ("40 ms",) for item in first))
        self.assertTrue(all(item.evidence_id == "platform" for item in first))

    def test_only_adds_a_skill_named_by_both_posting_and_approved_evidence(self) -> None:
        evidence = [
            _evidence("containers", "Deployed a verified service with Kubernetes."),
            _evidence("cloud", "Used AWS for an unrelated approved project."),
        ]
        skills = supported_role_skills(
            evidence,
            "Required: Kubernetes operations. Nice to have Azure.",
        )
        self.assertEqual(
            [(item.name, item.evidence_ids) for item in skills], [("Kubernetes", ("containers",))]
        )

        template = (
            "\\documentclass{article}\n"
            "\\newcommand{\\resumeSkillRow}[2]{#1: #2}\n"
            "\\begin{document}\n"
            "\\section{Technical Skills}\n"
            "\\resumeSkillRow{Platforms}{Docker}\n"
            "\\end{document}\n"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(template, encoding="utf-8")
            proposal = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "proposal",
                job_description="Required Kubernetes platform operations.",
                evidence=evidence,
                editable_sections=("Technical Skills",),
                require_unique_lead_verbs=False,
            )

            generated = proposal.proposal.proposed_tex_path.read_text(encoding="utf-8")
            report = json.loads(proposal.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertIn(r"\resumeSkillRow{Platforms}{Kubernetes, Docker}", generated)
            added = [
                item
                for item in report["skills"]
                if item["action"] == "added_from_approved_evidence"
            ]
            self.assertEqual(added[0]["evidence_ids"], ["containers"])

    def test_gpu_build_skills_require_both_job_and_approved_evidence(self) -> None:
        evidence = [_evidence("gpu", "Built a C++ CUDA diagnostic CLI with CMake.")]

        skills = supported_role_skills(
            evidence,
            "Required: C++, CUDA, and Linux. Nice to have Bazel.",
        )

        names = [(item.name, item.evidence_ids) for item in skills]
        self.assertIn(("CUDA", ("gpu",)), names)
        self.assertNotIn("CMake", [item.name for item in skills])

    def test_plan_exposes_required_gaps_before_generation(self) -> None:
        profile = build_role_profile(
            role="Infrastructure Engineer",
            qualifications=("Kubernetes production experience is required.",),
        )
        plan = build_tailoring_plan(
            job_url="https://jobs.example.test/infrastructure",
            company="Example",
            role="Infrastructure Engineer",
            job_snapshot="synthetic snapshot",
            job_description="Kubernetes production experience is required.",
            candidates=(),
            project_count=2,
            evidence=(_evidence("python", "Built a Python command-line tool."),),
            role_profile=profile,
        )

        self.assertEqual(plan.current_question.id, "evidence_gaps")
        self.assertEqual(plan.role_alignment[0].status, "unsupported")
        public = plan.as_public_dict()
        self.assertNotIn("job_snapshot", public)
        self.assertEqual(public["role_alignment"][0]["status"], "unsupported")

    def test_experience_budget_chooses_the_strongest_role_but_preserves_chronology(self) -> None:
        section = (
            "\\resumeSubheading{Marketing Assistant}{2026}{Example}{Remote}\n"
            "\\resumeItemListStart\n"
            "\\resumeItem{Designed social campaign graphics.}\n"
            "\\resumeItemListEnd\n"
            "\\resumeSubheading{Platform Engineer}{2025}{Example}{Remote}\n"
            "\\resumeItemListStart\n"
            "\\resumeItem{Owned Kubernetes services and incident response.}\n"
            "\\resumeItemListEnd\n"
        )

        compacted, omitted = _compact_generated_entry_section(
            section,
            heading_command="resumeSubheading",
            maximum_items=1,
            job_description="Required Kubernetes production ownership and incident response.",
            optimize_across_entries=True,
        )

        self.assertNotIn("Marketing Assistant", compacted)
        self.assertIn("Platform Engineer", compacted)
        self.assertEqual(omitted, ["Designed social campaign graphics."])


if __name__ == "__main__":
    unittest.main()
