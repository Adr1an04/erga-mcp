from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from erga_mcp.models import Evidence
from erga_mcp.project_inventory import ProjectCandidate
from erga_mcp.resume import validate_single_line_resume_items
from erga_mcp.resume_tailoring import (
    TAILORING_VERSION,
    _compact_generated_entry_section,
    _record_lead_verb_rewrites,
    _relevance,
    _separate_legacy_project_technology_stacks,
    _tailor_projects,
    apply_adaptive_single_page_fill,
    classify_wrapped_resume_items,
    create_automatic_resume_proposal,
    pdf_page_count,
    pdf_page_fill,
    semantic_resume_structure_issues,
)

_TEMPLATE = r"""
\documentclass[10pt]{article}
\usepackage[margin=0.55in]{geometry}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
\newcommand{\resumeSubheading}[4]{\item \textbf{#1} \hfill #2\\#3 \hfill #4}
\newcommand{\resumeProjectHeading}[2]{\item #1 \hfill #2}
\newcommand{\resumeItem}[1]{\item #1}
\begin{document}
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Engineer}{2026}{Example}{Remote}
\resumeItemListStart
\resumeItem{Created visual website content and marketing pages for a student organization.}
\resumeItem{Built Python real-time APIs with FastAPI and Docker for low-latency services.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textbf{Design Site} $|$ \textit{JavaScript, React}}{}
\resumeItemListStart
\resumeItem{Designed a responsive website and reusable visual content system.}
\resumeItemListEnd
\resumeProjectHeading{\textbf{Stream Engine} $|$ \textit{Python, PyTorch, Docker}}{}
\resumeItemListStart
\resumeItem{Implemented a real-time inference engine with low-latency Python services.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\section{Technical Skills}
\textbf{Languages:} JavaScript, Python \\
\textbf{Frameworks:} React, FastAPI \\
\textbf{Libraries:} Pandas, PyTorch \\
\textbf{Tools / Platforms:} Figma, Docker \\
\end{document}
""".lstrip()

_SPARSE_TEMPLATE = r"""
\documentclass[10pt]{article}
\usepackage[margin=0.8in]{geometry}
\pagestyle{empty}
\raggedbottom
\setlength{\parindent}{0pt}
\newcommand{\resumeItemListStart}{\begin{itemize}\setlength{\itemsep}{0pt}\setlength{\topsep}{0pt}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
\newcommand{\resumeItem}[1]{\item \small #1}
\begin{document}
\begin{center}\Large Synthetic Candidate\\\small candidate@example.test\end{center}
\section{Education}
Synthetic University \hfill Example, CO\\
B.S. Computer Science; GPA: 4.0 \hfill Expected May 2028\\
Relevant Coursework: Algorithms, Operating Systems, Machine Learning
\section{Experience}
Synthetic Engineering Laboratory \hfill Example, CO\\
Software Engineering Intern \hfill May 2026 -- Present
\resumeItemListStart
\resumeItem{Engineered a verified service for a synthetic team and documented its operating model.}
\resumeItem{Validated release behavior with approved test evidence and reproducible checks.}
\resumeItem{Coordinated delivery milestones across a documented synthetic engineering group.}
\resumeItem{Reduced a measured processing delay through an approved implementation change.}
\resumeItemListEnd
\section{Projects}
\resumeItemListStart
\item[] \textbf{Verified Service} $|$ \textit{Python, FastAPI} \hfill Jul 2026
\resumeItem{Developed a role-relevant application from approved synthetic project evidence.}
\resumeItem{Optimized a measured workflow while preserving its documented correctness checks.}
\item[] \textbf{Accessible Interface} $|$ \textit{TypeScript, React} \hfill Jun 2026
\resumeItem{Designed an accessible interface backed by approved usability observations.}
\resumeItem{Deployed a containerized service through a reproducible release workflow.}
\item[] \textbf{Workflow Automator} $|$ \textit{Python, Docker} \hfill Apr 2026
\resumeItem{Automated a verified manual process while retaining its audit trail.}
\resumeItem{Tested failure paths with deterministic fixtures and documented expected results.}
\resumeItemListEnd
\section{Technical Skills}
\textbf{Languages:} Python, C++, SQL, TypeScript\\
\textbf{Frameworks:} FastAPI, React, PyTorch\\
\textbf{Systems:} Linux, Docker, Kubernetes\\
\textbf{Tools:} Git, GitHub Actions, PostgreSQL
\end{document}
""".lstrip()


class AutomaticResumeTailoringTests(unittest.TestCase):
    def test_legacy_inventory_project_technology_stack_uses_the_structural_heading_row(
        self,
    ) -> None:
        legacy = (
            r"\resumeProjectHeading{\href{https://example.test/project}{\textbf{Project}} "
            r"$|$ \textit{Python, CUDA, Distributed Systems}}{Summer 2027}"
        )

        upgraded = _separate_legacy_project_technology_stacks(legacy)

        self.assertEqual(
            upgraded,
            r"\resumeProjectHeading{\href{https://example.test/project}{\textbf{Project}}}"
            r"{\textit{Python, CUDA, Distributed Systems}}{Summer 2027}",
        )
        self.assertEqual(_separate_legacy_project_technology_stacks(upgraded), upgraded)

    def test_entry_bullet_pattern_ignores_structural_category_headings(self) -> None:
        section = r"""
\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textit{Research Projects}}{}
\resumeProjectHeading{\textbf{Alpha}}{}
\resumeItemListStart
\resumeItem{Alpha one}
\resumeItem{Alpha two}
\resumeItemListEnd
\resumeProjectHeading{\textbf{Beta}}{}
\resumeItemListStart
\resumeItem{Beta one}
\resumeItem{Beta two}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""

        compacted, _ = _compact_generated_entry_section(
            section,
            heading_command="resumeProjectHeading",
            maximum_items=3,
            maximum_items_per_entry=(1, 2),
        )

        self.assertIn(r"\resumeProjectHeading{\textit{Research Projects}}", compacted)
        alpha_start = compacted.index(r"\resumeProjectHeading{\textbf{Alpha}}")
        beta_start = compacted.index(r"\resumeProjectHeading{\textbf{Beta}}")
        self.assertEqual(compacted[alpha_start:beta_start].count(r"\resumeItem{"), 1)
        self.assertEqual(compacted[beta_start:].count(r"\resumeItem{"), 2)

    def test_project_categories_stay_attached_when_projects_are_ranked(self) -> None:
        section = r"""
\resumeSubHeadingListStart
\resumeProjectHeading{\textit{Product Systems}}{}
\resumeProjectHeading{\textbf{Visual Client}}{2026}
\resumeItemListStart
\resumeItem{Built an approved visual interface.}
\resumeItemListEnd
\resumeProjectHeading{\textit{Research Systems}}{}
\resumeProjectHeading{\textbf{Inference Engine}}{2026}
\resumeItemListStart
\resumeItem{Built an approved Python inference engine.}
\resumeItemListEnd
\resumeSubHeadingListEnd
"""

        tailored, _, changed = _tailor_projects(section, "Python inference")

        research = tailored.index(r"\resumeProjectHeading{\textit{Research Systems}}{}")
        inference = tailored.index(r"\resumeProjectHeading{\textbf{Inference Engine}}{2026}")
        product = tailored.index(r"\resumeProjectHeading{\textit{Product Systems}}{}")
        visual = tailored.index(r"\resumeProjectHeading{\textbf{Visual Client}}{2026}")
        self.assertTrue(changed)
        self.assertLess(research, inference)
        self.assertLess(inference, product)
        self.assertLess(product, visual)

    def test_tailoring_version_invalidates_cached_proposals_after_constraint_enforcement(
        self,
    ) -> None:
        self.assertEqual(TAILORING_VERSION, 27)

    def test_semantic_layout_gate_rejects_flattened_generated_resume(self) -> None:
        flattened = r"""
% Erga semantic resume template version: 8
\begin{document}
\begin{center}\Large Synthetic Candidate\end{center}
\section{Education}
Synthetic University
\section{Experience}
\resumeItem{Built a verified synthetic service.}
\section{Projects}
\resumeItem{Created project one.}
\resumeItem{Created project two.}
\resumeItem{Created project three.}
\resumeItem{Created project four.}
\section{Technical Skills}
% No approved skills were extracted from the master resume.
\end{document}
"""

        issues = semantic_resume_structure_issues(flattened)

        self.assertIn("education heading hierarchy is missing", issues)
        self.assertIn("experience subheadings are missing", issues)
        self.assertIn("projects subheadings are missing", issues)
        self.assertIn("projects were flattened into too few semantic groups", issues)
        self.assertIn("projects bullet structure is missing", issues)
        self.assertIn("technical skills rows are missing", issues)

    def test_semantic_layout_gate_accepts_structured_generated_resume(self) -> None:
        structured = r"""
% Erga semantic resume template version: 8
\begin{document}
\begin{center}\LARGE Synthetic Candidate\end{center}
\section{Education}
\resumeEducationHeading{Synthetic University}{Example, CO}
\section{Experience}
\resumeSubheading{Engineer}{Remote}{Synthetic Lab}{2026}
\resumeItemListStart
\resumeItem{Built a verified synthetic service.}
\resumeItemListEnd
\section{Projects}
\resumeProjectHeading{\textbf{One}}{}
\resumeItemListStart
\resumeItem{Created project one.}
\resumeItem{Created project two.}
\resumeItemListEnd
\resumeProjectHeading{\textbf{Two}}{}
\resumeItemListStart
\resumeItem{Created project three.}
\resumeItem{Created project four.}
\resumeItemListEnd
\section{Technical Skills}
\textbf{Languages:} Python, C++ \\
\end{document}
"""

        self.assertEqual(semantic_resume_structure_issues(structured), ())

    def test_adaptive_page_fill_is_template_agnostic_and_idempotent(self) -> None:
        compact = _SPARSE_TEMPLATE.replace("[10pt]", "[9pt]")

        filled = apply_adaptive_single_page_fill(compact)

        self.assertIn(r"\flushbottom", filled)
        self.assertNotIn(r"\renewcommand{\resumeItem}[1]", filled)
        self.assertEqual(filled.count(r"\vspace{0pt plus 1fill}"), 5)
        self.assertNotIn(r"\ergaPageFillOriginalResumeItem", filled)
        self.assertTrue(
            all(
                "ERGA-ADAPTIVE-PAGE-FILL" not in line
                for line in filled.splitlines()
                if r"\resumeItem{" in line
            )
        )
        self.assertEqual(apply_adaptive_single_page_fill(filled), filled)

    def test_page_fill_does_not_create_giant_gaps_for_too_little_content(self) -> None:
        tiny = _SPARSE_TEMPLATE.replace(
            _SPARSE_TEMPLATE[_SPARSE_TEMPLATE.index(r"\section{Projects}") :],
            "\\end{document}\n",
        )

        self.assertEqual(apply_adaptive_single_page_fill(tiny), tiny)

    def test_page_fill_preserves_base_visual_style_spacing_and_uses_spare_height(self) -> None:
        controlled = "% Erga visual spacing is template-controlled.\n" + _SPARSE_TEMPLATE

        filled = apply_adaptive_single_page_fill(controlled)

        self.assertIn(r"\flushbottom", filled)
        self.assertEqual(filled.count(r"\vspace{0pt plus 1fill}"), 5)
        self.assertEqual(apply_adaptive_single_page_fill(filled), filled)

    def test_page_fill_can_use_a_measured_bounded_amount_of_spare_height(self) -> None:
        filled = apply_adaptive_single_page_fill(_SPARSE_TEMPLATE, additional_space_pt=50)

        self.assertIn("% ERGA-ADAPTIVE-PAGE-FILL", filled)
        self.assertNotIn(r"\flushbottom", filled)
        self.assertEqual(filled.count(r"\vspace{10.00pt}"), 5)

    def test_page_fill_uses_rendered_page_relative_coordinates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sparse = root / "sparse.pdf"
            filled = root / "filled.pdf"
            self._write_positioned_text_pdf(sparse, bottom_y=320)
            self._write_positioned_text_pdf(filled, bottom_y=55)

            sparse_fill = pdf_page_fill(sparse)
            filled_fill = pdf_page_fill(filled)

            self.assertLess(sparse_fill.fill_ratio, 0.82)
            self.assertGreater(filled_fill.fill_ratio, 0.82)
            self.assertGreater(filled_fill.fill_ratio, sparse_fill.fill_ratio)

    def test_sparse_small_font_template_fills_without_rewriting_content(self) -> None:
        latexmk = shutil.which("latexmk")
        if latexmk is None:
            self.skipTest("latexmk is not installed")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.tex"
            adaptive = root / "adaptive.tex"
            original.write_text(_SPARSE_TEMPLATE, encoding="utf-8")
            adaptive.write_text(
                apply_adaptive_single_page_fill(_SPARSE_TEMPLATE),
                encoding="utf-8",
            )
            for proposal in (original, adaptive):
                subprocess.run(
                    [
                        latexmk,
                        "-pdf",
                        "-no-shell-escape",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        proposal.name,
                    ],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            original_fill = pdf_page_fill(original.with_suffix(".pdf"))
            adaptive_fill = pdf_page_fill(adaptive.with_suffix(".pdf"))

            self.assertLess(original_fill.fill_ratio, 0.82)
            self.assertGreaterEqual(adaptive_fill.fill_ratio, 0.82)
            item_layout = validate_single_line_resume_items(
                adaptive,
                latexmk=Path(latexmk),
            )
            self.assertEqual(item_layout.returncode, 0)
            self.assertEqual(item_layout.wrapped_item_indices, ())
            self.assertEqual(
                _SPARSE_TEMPLATE.count(r"\resumeItem{"),
                adaptive.read_text(encoding="utf-8").count(r"\resumeItem{"),
            )

    @unittest.skipUnless(shutil.which("latexmk"), "latexmk is required to compile layout fixtures")
    def test_visual_style_marker_still_meets_the_page_fill_target(self) -> None:
        latexmk = shutil.which("latexmk")
        assert latexmk is not None
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text(
                apply_adaptive_single_page_fill(
                    "% Erga visual spacing is template-controlled.\n" + _SPARSE_TEMPLATE
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    latexmk,
                    "-pdf",
                    "-no-shell-escape",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    proposal.name,
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertGreaterEqual(pdf_page_fill(proposal.with_suffix(".pdf")).fill_ratio, 0.82)

    @staticmethod
    def _write_positioned_text_pdf(path: Path, *, bottom_y: int) -> None:
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            }
        )
        content = DecodedStreamObject()
        content.set_data(
            (
                f"BT /F1 10 Tf 50 742 Td (Synthetic Header) Tj ET\n"
                f"BT /F1 10 Tf 50 {bottom_y} Td (Synthetic Final Section) Tj ET\n"
            ).encode("ascii")
        )
        page[NameObject("/Contents")] = writer._add_object(content)
        with path.open("wb") as stream:
            writer.write(stream)

    def test_classifies_wrapped_bullets_as_project_or_baseline_content(self) -> None:
        candidates = (
            ProjectCandidate(
                id="design-site",
                title="Design Site",
                latex="",
                evidence_ids=(),
                bullet_evidence_ids=(),
            ),
            ProjectCandidate(
                id="stream-engine",
                title="Stream Engine",
                latex="",
                evidence_ids=(),
                bullet_evidence_ids=(),
            ),
        )

        project_ids, baseline_indices = classify_wrapped_resume_items(
            _TEMPLATE,
            candidates,
            (0, 2, 3),
        )

        self.assertEqual(project_ids, ("design-site", "stream-engine"))
        self.assertEqual(baseline_indices, (0,))

    def test_relevance_requires_term_boundaries_and_rejects_substring_collisions(self) -> None:
        for skill, unrelated in (
            ("Java", "JavaScript"),
            ("Rust", "high-trust collaborator"),
            ("AWS", "applicable laws"),
            ("Express", "preference expressed"),
            ("scikit-learn", "drive to learn"),
        ):
            score, matched = _relevance(skill, unrelated)
            self.assertEqual((score, matched), (0, ()), (skill, unrelated))

        self.assertGreater(_relevance("Java", "Production Java services")[0], 0)
        self.assertGreater(_relevance("AWS", "Deploy on AWS")[0], 0)

    def test_embedded_hardware_signals_outweigh_generic_test_vocabulary(self) -> None:
        role = "Embedded software test engineer using MCU, DSP, C++, Python, and test automation"
        embedded = _relevance("C++ sensor pipeline with Arduino, IMU, and EMG hardware", role)[0]
        generic_testing = _relevance("Python Pytest regression tests for CLI parsing", role)[0]
        self.assertGreater(embedded, generic_testing)

    def test_reorders_existing_experience_projects_and_every_skill_category(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")
            evidence = Evidence(
                id="ev_python",
                source_ref="Career.md#API",
                text=(
                    "Built Python real-time APIs with FastAPI and Docker for low-latency services."
                ),
                approved=True,
                created_at=datetime.now(UTC),
            )

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description=(
                    "Python real-time low-latency inference with FastAPI, PyTorch, and Docker"
                ),
                evidence=[evidence],
                editable_sections=("experience", "projects", "technical-skills"),
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertTrue(result.meaningful_change)
            self.assertEqual(
                result.changed_sections,
                ("Experience", "Projects", "Technical Skills"),
            )
            self.assertLess(proposed.index("Built Python"), proposed.index("Created visual"))
            self.assertLess(proposed.index("Stream Engine"), proposed.index("Design Site"))
            for expected in (
                r"\textbf{Languages:} Python, JavaScript",
                r"\textbf{Frameworks:} FastAPI, React",
                r"\textbf{Libraries:} PyTorch, Pandas",
                r"\textbf{Tools / Platforms:} Docker, Figma",
            ):
                self.assertIn(expected, proposed)

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            python_claim = next(
                claim for claim in report["claims"] if claim["text"].startswith("Built Python")
            )
            self.assertEqual(python_claim["evidence_ids"], ["ev_python"])
            self.assertEqual(python_claim["source_kind"], "approved_evidence")
            self.assertFalse(python_claim["text_changed"])
            self.assertEqual(len(report["skills"]), 8)
            self.assertGreater(result.proposal.diff_path.stat().st_size, 0)
            self.assertEqual(source.read_text(encoding="utf-8"), _TEMPLATE)

    def test_replaces_template_projects_with_approved_role_specific_inventory(self) -> None:
        inventory = (
            ProjectCandidate(
                id="embedded-controller",
                title="Embedded Controller",
                latex=(
                    r"\resumeProjectHeading{\textbf{Embedded Controller} $|$ \textit{C++, MCU}}{}"
                    "\n\\resumeItemListStart\n"
                    r"\resumeItem{Built C++ firmware for MCU sensor control.}"
                    "\n\\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_embedded",),
                bullet_evidence_ids=(("ev_embedded",),),
                tags=("c++", "mcu", "embedded", "sensors"),
            ),
            ProjectCandidate(
                id="web-portal",
                title="Web Portal",
                latex=(
                    r"\resumeProjectHeading{\textbf{Web Portal} $|$ \textit{React}}{}"
                    "\n\\resumeItemListStart\n"
                    r"\resumeItem{Built a React portal for member operations.}"
                    "\n\\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_web",),
                bullet_evidence_ids=(("ev_web",),),
                tags=("react", "frontend"),
            ),
        )
        evidence = [
            Evidence(
                "ev_embedded",
                "Career#Embedded",
                "Built C++ firmware for MCU sensor control.",
                True,
                datetime.now(UTC),
            ),
            Evidence(
                "ev_web",
                "Career#Web",
                "Built a React portal for member operations.",
                True,
                datetime.now(UTC),
            ),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Embedded software engineer building C++ MCU sensor systems",
                evidence=evidence,
                editable_sections=("projects",),
                project_candidates=inventory,
                project_count=1,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn("Embedded Controller", proposed)
            self.assertNotIn("Design Site", proposed)
            self.assertNotIn("Stream Engine", proposed)
            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["project_selection"]["selected_ids"], ["embedded-controller"])
            self.assertEqual(result.changed_sections, ("Projects",))

    def test_selected_inventory_project_preserves_matching_master_block_formatting(self) -> None:
        source_text = _TEMPLATE.replace(
            r"\resumeProjectHeading{\textbf{Stream Engine}",
            r"\resumeProjectHeading{\href{https://example.test/stream}{\textbf{Stream Engine}}",
        ).replace(
            "low-latency Python services.",
            r"low-latency \textbf{Python} services.",
        )
        inventory = (
            ProjectCandidate(
                id="stream-engine",
                title="Stream Engine",
                latex=(
                    r"\resumeProjectHeading{\textbf{Stream Engine} $|$ "
                    r"\textit{Python, PyTorch, Docker}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Implemented a real-time inference engine with low-latency "
                    r"Python services.}\n"
                    r"\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_stream",),
                bullet_evidence_ids=(("ev_stream",),),
                tags=("python", "pytorch", "inference"),
            ),
        )
        evidence = [
            Evidence(
                "ev_stream",
                "Career#Stream",
                "Implemented a real-time inference engine with low-latency Python services.",
                True,
                datetime.now(UTC),
            )
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(source_text, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required: Python PyTorch inference",
                evidence=evidence,
                editable_sections=("projects",),
                project_candidates=inventory,
                project_count=1,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn(r"\href{https://example.test/stream}{\textbf{Stream Engine}}", proposed)
            self.assertIn(r"low-latency \textbf{Python} services", proposed)

    def test_duplicate_lead_rewrite_selects_an_alternative_that_fits_the_layout(self) -> None:
        base = "Created a Python platform for deterministic project testing "
        bullet = base + ("x" * (114 - len(base))) + "."
        self.assertEqual(len(bullet), 115)
        inventory = (
            ProjectCandidate(
                id="python-platform",
                title="Python Platform",
                latex=(
                    r"\resumeProjectHeading{\textbf{Python Platform}}{}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    rf"\resumeItem{{{bullet}}}"
                    "\n"
                    r"\resumeItemListEnd"
                    "\n"
                ),
                evidence_ids=("ev_platform",),
                bullet_evidence_ids=(("ev_platform",),),
                tags=("python", "platform", "testing"),
            ),
        )
        evidence = [
            Evidence(
                "ev_platform",
                "Career#Platform",
                bullet,
                True,
                datetime.now(UTC),
            )
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required: Python platform testing",
                evidence=evidence,
                editable_sections=("projects",),
                bullet_min_chars=1,
                bullet_target_chars=105,
                bullet_max_chars=116,
                project_candidates=inventory,
                project_count=1,
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn("Produced a Python platform", proposed)
            self.assertEqual(result.constraint_violations, ())

    def test_uses_an_explicit_baseline_only_when_no_relevant_ordering_change_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="unrelated quasar geology",
                evidence=[],
                editable_sections=("experience", "projects", "technical-skills"),
            )

            self.assertFalse(result.meaningful_change)
            self.assertEqual(result.proposal.diff_path.read_text(encoding="utf-8"), "")
            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["tailoring"]["baseline_fallback"])
            self.assertIn("No meaningful", report["tailoring"]["reason"])

    def test_length_constraints_report_legacy_underflows_as_soft_deviations(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker",
                evidence=[],
                editable_sections=("experience",),
                bullet_min_chars=99,
                bullet_target_chars=105,
                bullet_max_chars=116,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            lengths = report["constraints"]["bullet_characters"]
            self.assertTrue(lengths["passed"])
            self.assertEqual(lengths["legacy_violations"], [])
            self.assertEqual(lengths["new_violations"], [])
            self.assertGreater(len(lengths["soft_deviations"]), 0)
            self.assertEqual(result.constraint_violations, ())

    def test_under_minimum_length_is_a_soft_deviation(self) -> None:
        bullet = "Built a Python API with deterministic tests and reviewed Git provenance."
        self.assertLess(len(bullet), 98)
        evidence = Evidence(
            "ev_soft_length",
            "git-derived:soft-length",
            bullet,
            True,
            datetime.now(UTC),
        )
        project = ProjectCandidate(
            id="soft-length",
            title="Soft Length",
            latex=(
                r"\resumeProjectHeading{\textbf{Soft Length} $|$ \textit{Python}}{}"
                "\n"
                r"\resumeItemListStart"
                "\n"
                rf"\resumeItem{{{bullet}}}"
                "\n"
                r"\resumeItemListEnd"
                "\n"
            ),
            evidence_ids=(evidence.id,),
            bullet_evidence_ids=((evidence.id,),),
            tags=("python", "api"),
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")
            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required Python API",
                evidence=[evidence],
                editable_sections=("projects",),
                bullet_min_chars=99,
                bullet_target_chars=105,
                bullet_max_chars=116,
                project_candidates=(project,),
                project_count=1,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            lengths = report["constraints"]["bullet_characters"]
            self.assertTrue(result.meaningful_change)
            self.assertEqual(result.constraint_violations, ())
            self.assertEqual(lengths["new_violations"], [])
            self.assertIn({"length": len(bullet), "text": bullet}, lengths["soft_deviations"])

    def test_duplicate_lead_verbs_are_resolved_before_constraint_validation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(
                _TEMPLATE.replace(
                    "Implemented a real-time inference engine", "Built a real-time inference engine"
                ),
                encoding="utf-8",
            )

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker",
                evidence=[],
                editable_sections=("experience", "projects"),
                bullet_min_chars=99,
                bullet_target_chars=105,
                bullet_max_chars=116,
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertTrue(result.meaningful_change)
            self.assertEqual(result.constraint_violations, ())
            self.assertTrue(report["constraints"]["lead_verbs"]["passed"])
            self.assertNotEqual(proposed.count(r"\resumeItem{Built "), 2)
            self.assertEqual(
                report["constraints"]["lead_verbs"]["rewrites"],
                [
                    {
                        "from": "Built",
                        "original_text": (
                            "Built a real-time inference engine with low-latency Python services."
                        ),
                        "rewritten_text": (
                            "Developed a real-time inference engine with low-latency "
                            "Python services."
                        ),
                        "section": "Projects",
                        "section_bullet_index": 0,
                        "to": "Developed",
                    }
                ],
            )
            rewritten_claim = next(
                claim for claim in report["claims"] if claim["text"].startswith("Developed ")
            )
            self.assertTrue(rewritten_claim["text_changed"])
            self.assertEqual(
                rewritten_claim["original_text"],
                "Built a real-time inference engine with low-latency Python services.",
            )

    def test_replacement_projects_can_reuse_a_lead_from_the_removed_project_section(self) -> None:
        project = ProjectCandidate(
            id="api-platform",
            title="API Platform",
            latex=(
                r"\resumeProjectHeading{\textbf{API Platform} $|$ \textit{Python, API}}{}"
                "\n"
                r"\resumeItemListStart"
                "\n"
                r"\resumeItem{Engineered a Python API platform with authenticated requests.}"
                "\n"
                r"\resumeItem{Validated API failures through approved integration tests.}"
                "\n"
                r"\resumeItemListEnd"
            ),
            evidence_ids=("ev_api",),
            bullet_evidence_ids=(("ev_api",), ("ev_api",)),
            tags=("python", "api", "testing"),
        )
        evidence = Evidence(
            "ev_api",
            "approved:api-platform",
            "Approved API Platform evidence.",
            True,
            datetime.now(UTC),
        )
        source_text = _TEMPLATE.replace(
            "Designed a responsive website", "Engineered a responsive website"
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(source_text, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required: Python API testing",
                evidence=[evidence],
                editable_sections=("projects",),
                bullet_max_chars=116,
                project_candidates=(project,),
                project_count=1,
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertEqual(result.constraint_violations, ())
            self.assertEqual(result.project_selection["selected_ids"], ["api-platform"])
            self.assertIn("Engineered a Python API platform", proposed)
            self.assertNotIn("Engineered a responsive website", proposed)

    def test_duplicate_award_lead_verbs_use_an_award_specific_replacement(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(
                _TEMPLATE.replace(
                    "Designed a responsive website", "Won a responsive website"
                ).replace(
                    "Implemented a real-time inference engine",
                    "Won a real-time inference engine",
                ),
                encoding="utf-8",
            )

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker",
                evidence=[],
                editable_sections=("projects",),
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn("Earned a responsive website", proposed)
            self.assertEqual(result.constraint_violations, ())

    def test_git_enrichment_can_resolve_six_implemented_project_bullets(self) -> None:
        evidence = [
            Evidence(
                id=f"ev_{index}",
                source_ref=f"git-derived:project-{index}",
                text=f"Implemented verified workstream {index} from Git history.",
                approved=True,
                created_at=datetime.now(UTC),
            )
            for index in range(6)
        ]
        projects = tuple(
            ProjectCandidate(
                id=f"project-{project}",
                title=f"Project {project}",
                latex=(
                    rf"\resumeProjectHeading{{\textbf{{Project {project}}}}}{{}}"
                    "\n"
                    r"\resumeItemListStart"
                    "\n"
                    rf"\resumeItem{{Implemented Python API workstream {project * 2} across "
                    "reviewed commits and files with deterministic Git provenance.}"
                    "\n"
                    rf"\resumeItem{{Implemented Python API workstream {project * 2 + 1} across "
                    "reviewed commits and files with deterministic Git provenance.}"
                    "\n"
                    r"\resumeItemListEnd"
                    "\n"
                ),
                evidence_ids=(f"ev_{project * 2}", f"ev_{project * 2 + 1}"),
                bullet_evidence_ids=((f"ev_{project * 2}",), (f"ev_{project * 2 + 1}",)),
                tags=("python", "api", "testing"),
            )
            for project in range(3)
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required Python API testing",
                evidence=evidence,
                editable_sections=("projects",),
                project_candidates=projects,
                project_count=3,
                bullet_min_chars=99,
                bullet_target_chars=105,
                bullet_max_chars=116,
                require_unique_lead_verbs=True,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertEqual(result.constraint_violations, ())
            self.assertTrue(result.meaningful_change)
            self.assertTrue(report["constraints"]["lead_verbs"]["passed"])
            self.assertEqual(len(report["constraints"]["lead_verbs"]["rewrites"]), 5)
            self.assertEqual(report["constraints"]["lead_verbs"]["duplicates"], {})

    def test_unknown_duplicate_verbs_remain_a_hard_constraint_violation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(
                _TEMPLATE.replace("Created visual", "Mentored visual").replace(
                    "Implemented a real-time", "Mentored a real-time"
                ),
                encoding="utf-8",
            )

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker",
                evidence=[],
                editable_sections=("experience", "projects"),
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertEqual(proposed.count("Mentored "), 2)
            self.assertEqual(result.constraint_violations, ("duplicate lead verb 'mentored'",))
            self.assertFalse(result.meaningful_change)
            self.assertFalse(report["constraints"]["lead_verbs"]["passed"])
            self.assertEqual(report["constraints"]["lead_verbs"]["rewrites"], [])

    def test_duplicate_verbs_in_uneditable_sections_are_not_rewritten(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            original = _TEMPLATE.replace(
                "Implemented a real-time inference engine", "Built a real-time inference engine"
            )
            source.write_text(original, encoding="utf-8")

            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker",
                evidence=[],
                editable_sections=("experience",),
                require_unique_lead_verbs=True,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn("Built a real-time inference engine", proposed)
            self.assertNotIn("Developed a real-time inference engine", proposed)
            self.assertEqual(result.constraint_violations, ("duplicate lead verb 'built'",))
            self.assertFalse(result.meaningful_change)

    def test_rewrite_provenance_uses_output_position_for_identical_claims(self) -> None:
        records: list[dict[str, object]] = [
            {
                "output_group_index": index,
                "output_index": 0,
                "section": "Projects",
                "source_ref": f"project/{index}",
                "text": "Built the same system.",
                "text_changed": False,
            }
            for index in range(2)
        ]
        rewrites: list[dict[str, object]] = [
            {
                "original_text": "Built the same system.",
                "rewritten_text": "Developed the same system.",
                "section": "Projects",
                "section_bullet_index": 1,
            }
        ]

        _record_lead_verb_rewrites(records, rewrites)

        self.assertFalse(records[0]["text_changed"])
        self.assertEqual(records[0]["text"], "Built the same system.")
        self.assertTrue(records[1]["text_changed"])
        self.assertEqual(records[1]["text"], "Developed the same system.")

    def test_inventory_constraint_fallback_resets_selection_and_provenance(self) -> None:
        inventory = (
            ProjectCandidate(
                id="too-long",
                title="Too Long",
                latex=(
                    r"\resumeProjectHeading{\textbf{Too Long} $|$ \textit{Python}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Built a deliberately oversized Python service bullet "
                    r"that exceeds the configured character limit for a regression test.}\n"
                    r"\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_long",),
                bullet_evidence_ids=(("ev_long",),),
                tags=("python",),
            ),
        )
        evidence = [
            Evidence("ev_long", "Career#Long", "Verified long project", True, datetime.now(UTC))
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            duplicate_template = _TEMPLATE.replace(
                "Implemented a real-time inference engine", "Built a real-time inference engine"
            )
            source.write_text(duplicate_template, encoding="utf-8")
            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required: Python service engineering",
                evidence=evidence,
                editable_sections=("projects",),
                bullet_min_chars=1,
                bullet_target_chars=40,
                bullet_max_chars=50,
                project_candidates=inventory,
                project_count=1,
                require_unique_lead_verbs=True,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertFalse(result.meaningful_change)
            self.assertEqual(
                result.proposal.proposed_tex_path.read_text(encoding="utf-8"), duplicate_template
            )
            self.assertEqual(report["project_selection"]["mode"], "inventory_constraint_fallback")
            self.assertEqual(report["project_selection"]["selected_ids"], [])
            self.assertEqual(report["project_claims"], [])
            self.assertFalse(report["constraints"]["lead_verbs"]["passed"])
            self.assertEqual(report["constraints"]["lead_verbs"]["rewrites"], [])

    def test_inventory_project_bullets_record_explicit_approved_provenance(self) -> None:
        inventory = (
            ProjectCandidate(
                id="api-platform",
                title="API Platform",
                latex=(
                    r"\resumeProjectHeading{\textbf{API Platform} $|$ \textit{Python, FastAPI}}{}\n"
                    r"\resumeItemListStart\n"
                    r"\resumeItem{Built a Python FastAPI platform for service integration.}\n"
                    r"\resumeItem{Validated API contracts with deterministic integration tests.}\n"
                    r"\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_api", "ev_tests"),
                bullet_evidence_ids=(("ev_api",), ("ev_tests",)),
                tags=("python", "fastapi", "api"),
            ),
        )
        evidence = [
            Evidence("ev_api", "Career#API", "Verified API platform", True, datetime.now(UTC)),
            Evidence(
                "ev_tests", "Career#Tests", "Verified contract tests", True, datetime.now(UTC)
            ),
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")
            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description="Required qualifications: Python and FastAPI",
                evidence=evidence,
                editable_sections=("projects",),
                project_candidates=inventory,
                project_count=1,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["project_claims"],
                [
                    {
                        "evidence_ids": ["ev_api"],
                        "output_group_index": 0,
                        "output_index": 0,
                        "project_id": "api-platform",
                        "project_title": "API Platform",
                        "section": "Projects",
                        "source_kind": "project_inventory",
                        "source_ref": "project_inventory/api-platform/1",
                        "text": "Built a Python FastAPI platform for service integration.",
                        "text_changed": False,
                    },
                    {
                        "evidence_ids": ["ev_tests"],
                        "output_group_index": 0,
                        "output_index": 1,
                        "project_id": "api-platform",
                        "project_title": "API Platform",
                        "section": "Projects",
                        "source_kind": "project_inventory",
                        "source_ref": "project_inventory/api-platform/2",
                        "text": "Validated API contracts with deterministic integration tests.",
                        "text_changed": False,
                    },
                ],
            )

    def test_inventory_no_match_preserves_template_projects_without_reordering(self) -> None:
        inventory = (
            ProjectCandidate(
                id="python-api",
                title="Python API",
                latex=(
                    r"\resumeProjectHeading{\textbf{Python API}}{}\n"
                    r"\resumeItemListStart\n\resumeItem{Built a Python API.}\n\resumeItemListEnd\n"
                ),
                evidence_ids=("ev_api",),
                tags=("python", "api"),
            ),
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")
            result = create_automatic_resume_proposal(
                resume_path=source,
                output_dir=root / "artifacts",
                job_description=(
                    "Required qualifications: actuarial reserving and insurance pricing"
                ),
                evidence=[
                    Evidence("ev_api", "Career#API", "Verified API", True, datetime.now(UTC))
                ],
                editable_sections=("projects",),
                project_candidates=inventory,
                project_count=1,
            )

            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertFalse(result.meaningful_change)
            self.assertEqual(
                result.proposal.proposed_tex_path.read_text(encoding="utf-8"), _TEMPLATE
            )
            self.assertEqual(report["project_selection"]["mode"], "inventory_no_match")
            self.assertEqual(report["project_selection"]["selected_ids"], [])

    def test_five_synthetic_jds_generate_distinct_one_page_inventory_resumes(self) -> None:
        tracks = {
            "ml": ("ML Inference", "python pytorch machine learning inference"),
            "backend": ("Backend Platform", "python fastapi kubernetes distributed systems"),
            "embedded": ("Embedded Controls", "c++ mcu sensors firmware"),
            "frontend": ("Product Frontend", "react typescript frontend accessibility"),
            "devtools": ("Developer Tools", "python cli developer tooling testing"),
        }
        inventory = tuple(
            ProjectCandidate(
                id=identifier,
                title=title,
                latex=(
                    rf"\resumeProjectHeading{{\textbf{{{title}}} $|$ \textit{{{tags}}}}}{{}}\n"
                    r"\resumeItemListStart\n"
                    rf"\resumeItem{{Built {title.lower()} using {tags}.}}\n"
                    r"\resumeItemListEnd\n"
                ).replace("\\n", "\n"),
                evidence_ids=(f"ev_{identifier}",),
                bullet_evidence_ids=((f"ev_{identifier}",),),
                tags=tuple(tags.split()),
            )
            for identifier, (title, tags) in tracks.items()
        )
        evidence = [
            Evidence(
                f"ev_{identifier}", f"Career#{title}", f"Verified {title}", True, datetime.now(UTC)
            )
            for identifier, (title, _) in tracks.items()
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "resume.tex"
            source.write_text(_TEMPLATE, encoding="utf-8")
            selected: dict[str, list[str]] = {}
            latexmk = shutil.which("latexmk")
            for identifier, (_, tags) in tracks.items():
                output_dir = root / identifier
                result = create_automatic_resume_proposal(
                    resume_path=source,
                    output_dir=output_dir,
                    job_description=f"Required qualifications: {tags}.",
                    evidence=evidence,
                    editable_sections=("projects",),
                    project_candidates=inventory,
                    project_count=1,
                )
                if latexmk is not None:
                    subprocess.run(
                        [
                            latexmk,
                            "-pdf",
                            "-interaction=nonstopmode",
                            "-halt-on-error",
                            "proposal.tex",
                        ],
                        cwd=output_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(pdf_page_count(output_dir / "proposal.pdf"), 1)
                report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
                selected[identifier] = report["project_selection"]["selected_ids"]
                self.assertTrue(result.meaningful_change)

            self.assertEqual(selected, {identifier: [identifier] for identifier in tracks})

    def test_pdf_page_count_is_portable_and_counts_only_page_objects(self) -> None:
        with TemporaryDirectory() as directory:
            pdf = Path(directory) / "resume.pdf"
            pdf.write_bytes(
                b"%PDF-1.4\n1 0 obj<</Type /Pages /Count 2>>endobj\n"
                b"2 0 obj<</Type /Page>>endobj\n3 0 obj<</Type /Page >>endobj\n%%EOF"
            )
            self.assertEqual(pdf_page_count(pdf), 2)


if __name__ == "__main__":
    unittest.main()
