from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.resume_settings import update_settings
from erga_mcp.resume_sources import ResumeSource, load_resume_source
from erga_mcp.resume_tailoring import create_automatic_resume_proposal
from erga_mcp.resume_template import (
    ensure_resume_template,
    generate_latex_template,
    infer_resume_layout_profile,
)


class ResumeTemplateTests(unittest.TestCase):
    def test_layout_profile_supports_project_only_resumes_without_inventing_experience(
        self,
    ) -> None:
        profile = infer_resume_layout_profile(
            r"""
\begin{document}
\section{Education}
\resumeEducationHeading{Example University}{Remote}
\section{Projects}
\resumeProjectHeading{\textbf{Compiler}}{}
\resumeItemListStart
\resumeItem{Built an approved compiler.}
\resumeItemListEnd
\resumeProjectHeading{\textbf{Database}}{}
\resumeItemListStart
\resumeItem{Built an approved database.}
\resumeItemListEnd
\section{Technical Skills}
\textbf{Languages:} Rust, Python
\end{document}
"""
        )

        self.assertEqual(profile.section_order, ("Education", "Projects", "Technical Skills"))
        self.assertEqual(profile.editable_sections, ("Projects", "Technical Skills"))
        self.assertEqual(profile.repeatable_sections, ("Projects",))
        self.assertEqual(
            profile.section_item_counts, {"Education": 0, "Projects": 2, "Technical Skills": 0}
        )
        self.assertEqual(profile.project_count, 2)

    def test_style_resume_controls_section_presence_and_order(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="7" * 64,
                page_count=2,
                text=(
                    "Jane Candidate\nEDUCATION\nExample University\n"
                    "EXPERIENCE\nExample Role\n   Built an approved service.\n"
                    "PROJECTS\nExample Project\n   Built an approved project.\n"
                    "TECHNICAL SKILLS\nLanguages: Python"
                ),
            )
            style = ResumeSource(
                path=root / "style.pdf",
                format="pdf",
                sha256="8" * 64,
                page_count=1,
                text="PROJECTS\nEDUCATION\nTECHNICAL SKILLS",
            )

            generated = generate_latex_template(master, data_dir=root / "state", style=style)
            template = generated.path.read_text(encoding="utf-8")

            self.assertNotIn(r"\section{Experience}", template)
            self.assertEqual(
                generated.profile.section_order,
                ("Projects", "Education", "Technical Skills"),
            )
            self.assertEqual(
                generated.profile.editable_sections,
                ("Projects", "Technical Skills"),
            )

    def test_explicit_style_resume_controls_project_slot_count(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="9" * 64,
                page_count=2,
                text=(
                    "Jane Candidate\nPROJECTS\nMaster One\n   Built approved one.\n"
                    "Master Two\n   Built approved two.\n"
                    "Master Three\n   Built approved three.\n"
                    "TECHNICAL SKILLS\nLanguages: Python"
                ),
            )
            style = ResumeSource(
                path=root / "style.pdf",
                format="pdf",
                sha256="a" * 64,
                page_count=1,
                text=(
                    "PROJECTS\nExample One\n   Example bullet.\n"
                    "Example Two\n   Example bullet.\nTECHNICAL SKILLS\nSkills"
                ),
            )

            generated = generate_latex_template(master, data_dir=root / "state", style=style)

            self.assertEqual(generated.profile.project_count, 2)
            metadata = json.loads(generated.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["layout_profile"]["project_count"], 2)

    def test_pdf_columns_render_as_bold_and_italic_resume_hierarchy(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="1" * 64,
                page_count=1,
                text=(
                    "Jane Candidate\nEDUCATION\n"
                    "Example University                     Orlando, FL\n"
                    "Bachelor of Science                    May 2027\n"
                    "EXPERIENCE\n"
                    "Software Engineer                      Remote\n"
                    "Example Company                        Jan 2025 - Present\n"
                    "   Built an approved production service.\n"
                    "PROJECTS\n"
                    "Compiler Project                       Feb 2026\n"
                    "   Implemented an approved compiler pass.\n"
                    "TECHNICAL SKILLS\nLanguages: Python"
                ),
            )

            generated = generate_latex_template(source, data_dir=root / "state")
            template = generated.path.read_text(encoding="utf-8")

            self.assertIn("% Erga semantic resume template version: 11", template)
            self.assertIn(r"\resumeEducationHeading{Example University}{Orlando, FL}", template)
            self.assertIn(r"\resumeEducationDetail{Bachelor of Science}{May 2027}", template)
            self.assertIn(
                r"\resumeSubheading{Software Engineer}{Remote}{Example Company}"
                r"{Jan 2025 - Present}",
                template,
            )
            self.assertIn(r"\resumeProjectHeading{\textbf{Compiler Project}}{Feb 2026}", template)

    def test_pdf_layout_lines_fold_wrapped_bullets_and_preserve_open_source_section(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="e" * 64,
                page_count=1,
                text=(
                    "[Page 1]\nJane Candidate\nEXPERIENCE\nRole heading\n"
                    "   Built an approved distributed service across\n"
                    "   two regions with deterministic failover.\n"
                    "O PEN                              S OURCE\n"
                    "   Merged an approved compiler correction.\n"
                    "TECHNICAL                         SKILLS\nLanguages: Python,\nRust"
                ),
            )

            generated = generate_latex_template(source, data_dir=root / "state")
            template = generated.path.read_text(encoding="utf-8")

            self.assertIn(
                r"\resumeItem{Built an approved distributed service across two regions "
                r"with deterministic failover.}",
                template,
            )
            self.assertEqual(template.count("Built an approved distributed service"), 1)
            self.assertIn(r"\resumeSubheading{Role heading}", template)
            self.assertIn(r"\section{Open Source}", template)
            self.assertIn("Merged an approved compiler correction", template)
            self.assertIn(r"\textbf{Languages:} Python, Rust", template)
            self.assertNotIn(r"\textbf{Skills:} Rust", template)

    def test_docx_master_generates_the_same_standalone_editable_template(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = root / "master.docx"
            document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Jane Candidate</w:t></w:r></w:p>
    <w:p><w:r><w:t>Experience</w:t></w:r></w:p>
    <w:p><w:r><w:t>Built approved Python services</w:t></w:r></w:p>
    <w:p><w:r><w:t>Projects</w:t></w:r></w:p>
    <w:p><w:r><w:t>Created an approved compiler project</w:t></w:r></w:p>
    <w:p><w:r><w:t>Technical Skills</w:t></w:r></w:p>
    <w:p><w:r><w:t>Languages: Python, Rust</w:t></w:r></w:p>
  </w:body>
</w:document>"""
            with zipfile.ZipFile(master, "w") as archive:
                archive.writestr("word/document.xml", document)

            generated = generate_latex_template(
                load_resume_source(master),
                data_dir=root / "state",
            )

            template = generated.path.read_text(encoding="utf-8")
            self.assertIn("Built approved Python services", template)
            self.assertIn("Created an approved compiler project", template)
            self.assertIn(r"\textbf{Languages:} Python, Rust", template)

    def test_one_page_proposal_selects_relevant_approved_items_and_reports_omissions(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            experience = "\n".join(
                [
                    "Designed visual marketing pages",
                    "Built Python FastAPI production services",
                    *[f"Maintained approved experience item {index}" for index in range(7)],
                ]
            )
            projects = "\n".join(
                [
                    "Created a Docker inference platform",
                    *[f"Completed approved project item {index}" for index in range(8)],
                ]
            )
            source = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="d" * 64,
                page_count=3,
                text=(
                    f"Jane Candidate\nEXPERIENCE\n{experience}\nPROJECTS\n{projects}\n"
                    "TECHNICAL SKILLS\nLanguages: JavaScript, Java, Go, Rust, C++, SQL, Python, "
                    "TypeScript, Bash"
                ),
            )
            generated = generate_latex_template(source, data_dir=root / "state")

            result = create_automatic_resume_proposal(
                resume_path=generated.path,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker inference services",
                evidence=[],
                editable_sections=("Experience", "Projects", "Technical-Skills"),
                max_pages=1,
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            report = json.loads(result.proposal.claim_report_path.read_text(encoding="utf-8"))
            self.assertIn("Built Python FastAPI production services", proposed)
            self.assertIn("Created a Docker inference platform", proposed)
            self.assertLessEqual(proposed.count(r"\resumeItem{"), 11)
            self.assertGreater(len(report["page_target_omissions"]), 0)
            self.assertTrue(
                any(item["action"] == "omitted_for_page_target" for item in report["skills"])
            )
            self.assertTrue(
                all(
                    item["action"] == "omitted_for_page_target"
                    for item in report["page_target_omissions"]
                )
            )

    def test_generated_project_budget_keeps_heading_attached_to_selected_bullet(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="f" * 64,
                page_count=1,
                text=(
                    "Jane Candidate\nPROJECTS\nAlpha Platform\n"
                    "   Built the approved Alpha Python service.\n"
                    "   Tested the approved Alpha deployment.\n"
                    "Beta Website\n"
                    "   Designed the approved Beta marketing site.\n"
                    "TECHNICAL SKILLS\nLanguages: Python"
                ),
            )
            generated = generate_latex_template(source, data_dir=root / "state")

            result = create_automatic_resume_proposal(
                resume_path=generated.path,
                output_dir=root / "artifacts",
                job_description="Alpha Python service",
                evidence=[],
                editable_sections=("Projects",),
                max_pages=1,
                generated_section_item_limits={"Projects": 1},
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertIn(r"\resumeProjectHeading{\textbf{Alpha Platform}}", proposed)
            self.assertIn("Built the approved Alpha Python service", proposed)
            self.assertNotIn("Tested the approved Alpha deployment", proposed)
            self.assertNotIn("Beta Website", proposed)

    def test_pdf_master_generates_standalone_template_without_style_claims(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master = ResumeSource(
                path=root / "master.pdf",
                format="pdf",
                sha256="a" * 64,
                page_count=2,
                text=(
                    "[Page 1]\nJane Candidate\njane@example.test | github.example/jane\n"
                    "EDUCATION\nExample University & Honors\n"
                    "EXPERIENCE\nDesigned visual websites\nBuilt Python APIs with FastAPI\n"
                    "PROJECTS\nCreated an inference engine\n"
                    "TECHNICAL SKILLS\nLanguages: Python, JavaScript\n"
                ),
            )
            style = ResumeSource(
                path=root / "style.pdf",
                format="pdf",
                sha256="b" * 64,
                page_count=1,
                text=(
                    "Style Person\nPROJECTS\nStyle-only secret accomplishment\n"
                    "EXPERIENCE\nStyle-only employer"
                ),
            )

            generated = generate_latex_template(master, data_dir=root / "state", style=style)

            template = generated.path.read_text(encoding="utf-8")
            metadata = json.loads(generated.metadata_path.read_text(encoding="utf-8"))
            self.assertIn(r"\documentclass[letterpaper,10pt]{article}", template)
            self.assertIn(r"\usepackage[margin=0.50in]{geometry}", template)
            self.assertIn(r"\fontsize{9pt}{10.4pt}\selectfont", template)
            self.assertIn("Jane Candidate", template)
            self.assertNotIn(r"\section{Education}", template)
            self.assertNotIn(r"\section{Technical Skills}", template)
            self.assertIn(r"\resumeItem{Built Python APIs with FastAPI}", template)
            self.assertLess(
                template.index(r"\section{Projects}"), template.index(r"\section{Experience}")
            )
            self.assertNotIn("Style-only secret", template)
            self.assertNotIn("Style Person", template)
            self.assertFalse(metadata["style_used_for_facts"])
            self.assertEqual(metadata["master_sha256"], "a" * 64)

    def test_generated_generic_items_are_tailorable_without_legacy_macros(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.txt"
            source = ResumeSource(
                path=master_path,
                format="pdf",
                sha256="c" * 64,
                page_count=1,
                text=(
                    "Jane Candidate\nEXPERIENCE\n"
                    "Designed visual marketing pages\n"
                    "Built Python FastAPI Docker services\n"
                    "PROJECTS\nPortfolio site\nTECHNICAL SKILLS\nLanguages: JavaScript, Python"
                ),
            )
            generated = generate_latex_template(source, data_dir=root / "state")

            result = create_automatic_resume_proposal(
                resume_path=generated.path,
                output_dir=root / "artifacts",
                job_description="Python FastAPI Docker backend",
                evidence=[],
                editable_sections=("Experience", "Projects", "Technical-Skills"),
            )

            proposed = result.proposal.proposed_tex_path.read_text(encoding="utf-8")
            self.assertTrue(result.meaningful_change)
            self.assertLess(proposed.index("Built Python"), proposed.index("Designed visual"))
            self.assertIn(r"\textbf{Languages:} Python, JavaScript", proposed)

    def test_ensure_backfills_missing_template_once_and_updates_private_config(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            master = root / "master.tex"
            master.write_text(
                "Jane Candidate\nExperience\nBuilt a verified service\nProjects\nProject A\n"
                "Technical Skills\nLanguages: Python",
                encoding="utf-8",
            )
            update_settings(config_path, {"master_path": str(master)})

            first = ensure_resume_template(config_path)
            second = ensure_resume_template(config_path)

            self.assertEqual(first, second)
            self.assertEqual(load_config(config_path).resume.template_path, first)
            self.assertTrue(first.is_relative_to(load_config(config_path).data_dir))
            self.assertEqual(load_resume_source(master).text.splitlines()[0], "Jane Candidate")


if __name__ == "__main__":
    unittest.main()
