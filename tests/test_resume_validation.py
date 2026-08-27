from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from erga_mcp.resumes.artifacts import (
    _pdf_resume_item_lines,
    inspect_compiled_resume_item_layout,
    resolve_latexmk_executable,
    resume_item_texts,
    validate_latex_proposal,
    validate_single_line_resume_items,
)


class ResumeValidationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tectonic"), "tectonic is not installed")
    def test_rejects_an_invisible_second_line_created_by_template_spacing(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text(
                r"""\documentclass{article}
\newcommand{\resumeItem}[1]{\item\small{{#1 \vspace{-1.6pt}}}}
\begin{document}
\begin{minipage}{220pt}
\begin{itemize}
\resumeItem{\makebox[\linewidth][l]{Visible words remain on one rendered text line.}}
\resumeItem{A normal second bullet.}
\end{itemize}
\end{minipage}
\end{document}
""",
                encoding="utf-8",
            )

            result = validate_single_line_resume_items(
                proposal,
                latexmk=Path("tectonic"),
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.wrapped_item_indices, (0,))
            self.assertEqual(result.item_line_counts, (2, 1))

    @unittest.skipUnless(shutil.which("tectonic"), "tectonic is not installed")
    def test_tectonic_fallback_guards_common_jake_resume_pdftex_hooks(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            source = r"""\documentclass{article}
\input{glyphtounicode}
\pdfgentounicode=1
\begin{document}
Jake-style resume content.
\end{document}
"""
            proposal.write_text(source, encoding="utf-8")

            result = validate_latex_proposal(proposal, latexmk=Path("tectonic"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(proposal.with_suffix(".pdf").is_file())
            self.assertEqual(proposal.read_text(encoding="utf-8"), source)
            self.assertEqual(tuple(root.glob("erga-tectonic-*")), ())

    def test_inspects_an_existing_pdf_without_starting_a_second_compiler(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text(
                "\\begin{document}\n\\begin{itemize}\n"
                "\\item First bullet.\n\\item Second bullet.\n"
                "\\end{itemize}\n\\end{document}\n",
                encoding="utf-8",
            )
            pdf = root / "proposal.pdf"
            pdf.write_bytes(b"synthetic")

            with patch(
                "erga_mcp.resumes.artifacts._pdf_resume_item_lines",
                return_value=(("First bullet.",), ("Long first line", "two words")),
            ):
                result = inspect_compiled_resume_item_layout(proposal, pdf)

            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.command, ())
            self.assertEqual(result.wrapped_item_indices, (1,))
            self.assertEqual(result.orphan_item_indices, (1,))

    def test_standard_skills_container_is_not_treated_as_an_achievement_bullet(self) -> None:
        source = r"""\begin{document}
\section{Projects}
\begin{itemize}
\item Built a Python API supporting 5 routes.
\end{itemize}
\section{Technical Skills}
\begin{itemize}
\item
\textbf{Languages:} Python, C++ \\
\textbf{Tools:} Docker, Git
\end{itemize}
\end{document}
"""

        self.assertEqual(
            resume_item_texts(source),
            ("Built a Python API supporting 5 routes.",),
        )

    def test_extracts_rendered_bullet_lines_without_mistaking_headings_for_continuations(
        self,
    ) -> None:
        page = Mock()
        page.extract_text.return_value = (
            "Experience\n"
            " • Built a synthetic service across a deliberately long first line\n"
            "    two words\n"
            "Synthetic Project Heading\n"
            " • Built another synthetic service across its first rendered line\n"
            "    with a healthy and readable continuation line\n"
        )
        reader = Mock(pages=[page])

        with patch("erga_mcp.resumes.artifacts.PdfReader", return_value=reader):
            lines = _pdf_resume_item_lines(Path("synthetic.pdf"))

        self.assertEqual(
            lines,
            (
                (
                    "Built a synthetic service across a deliberately long first line",
                    "two words",
                ),
                (
                    "Built another synthetic service across its first rendered line",
                    "with a healthy and readable continuation line",
                ),
            ),
        )

    def test_measures_exact_resume_item_width_without_modifying_the_proposal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            source = (
                r"\newcommand{\resumeItem}[1]{\item #1}"
                "\n"
                r"\begin{document}"
                "\n"
                r"\begin{itemize}"
                "\n"
                r"\resumeItem{Fits on one line.}"
                "\n"
                r"\resumeItem{Needs another rendered line.}"
                "\n"
                r"\end{itemize}"
                "\n"
                r"\end{document}"
                "\n"
            )
            proposal.write_text(source, encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="ERGA-RESUME-ITEM-FIT:1\nERGA-RESUME-ITEM-ORPHAN:2\n",
                stderr="",
            )

            result = validate_single_line_resume_items(
                proposal,
                latexmk=Path(sys.executable),
                runner=lambda *args, **kwargs: completed,
            )

            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.wrapped_item_indices, (1,))
            self.assertEqual(result.orphan_item_indices, (1,))
            self.assertIn("-no-shell-escape", result.command)
            self.assertEqual(proposal.read_text(encoding="utf-8"), source)
            self.assertEqual(tuple(root.glob("erga-layout-*")), ())

    def test_standard_latex_items_use_rendered_pdf_layout_without_custom_macro(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            source = (
                r"\begin{document}"
                "\n"
                r"\begin{itemize}"
                "\n"
                r"\item Fits on one line."
                "\n"
                r"\item Needs another rendered line."
                "\n"
                r"\end{itemize}"
                "\n"
                r"\end{document}"
                "\n"
            )
            proposal.write_text(source, encoding="utf-8")
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with patch(
                "erga_mcp.resumes.artifacts._pdf_resume_item_lines",
                return_value=(("Fits on one line.",), ("Needs another", "rendered line.")),
            ):
                result = validate_single_line_resume_items(
                    proposal,
                    latexmk=Path(sys.executable),
                    runner=lambda *args, **kwargs: completed,
                )

            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.wrapped_item_indices, (1,))
            self.assertNotIn("ergaOriginalResumeItem", proposal.read_text(encoding="utf-8"))
            self.assertEqual(tuple(root.glob("erga-layout-*")), ())

    def test_compiles_only_a_proposed_tex_file_with_a_user_selected_latexmk(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="validated proposal.tex\n", stderr=""
            )

            with patch("erga_mcp.resumes.artifacts.subprocess.run", return_value=completed) as run:
                result = validate_latex_proposal(proposal, latexmk=Path(sys.executable))

            self.assertEqual(result.returncode, 0)
            self.assertIn("proposal.tex", result.stdout)
            self.assertEqual(run.call_args.args[0][-1], "proposal.tex")
            self.assertIn("-no-shell-escape", run.call_args.args[0])
            self.assertEqual(
                proposal.read_text(encoding="utf-8"), "\\begin{document}ok\\end{document}\n"
            )

    def test_resolves_mactex_when_launchd_path_omits_texbin(self) -> None:
        with TemporaryDirectory() as directory:
            texbin = Path(directory)
            latexmk = texbin / "latexmk"
            latexmk.write_text("synthetic compiler", encoding="utf-8")

            with (
                patch("erga_mcp.resumes.artifacts.sys.platform", "darwin"),
                patch("erga_mcp.resumes.artifacts.shutil.which", return_value=None),
                patch("erga_mcp.resumes.artifacts._MACOS_TEXBIN", texbin),
                patch("erga_mcp.resumes.artifacts.os.access", return_value=True),
            ):
                resolved = resolve_latexmk_executable(Path("latexmk"))

            self.assertEqual(resolved, latexmk)

    def test_falls_back_to_tectonic_when_default_latexmk_is_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            tectonic = str(Path(directory) / "tectonic")

            def which(command: str) -> str | None:
                return tectonic if command == "tectonic" else None

            with (
                patch("erga_mcp.resumes.artifacts.sys.platform", "linux"),
                patch("erga_mcp.resumes.artifacts.shutil.which", side_effect=which),
            ):
                resolved = resolve_latexmk_executable(Path("latexmk"))

            self.assertEqual(resolved, Path(tectonic).absolute())

    def test_tectonic_uses_its_native_non_shell_compilation_arguments(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            tectonic = root / "tectonic"
            tectonic.write_text("synthetic compiler", encoding="utf-8")
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

            with (
                patch("erga_mcp.resumes.artifacts.shutil.which", return_value=str(tectonic)),
                patch("erga_mcp.resumes.artifacts.subprocess.run", return_value=completed) as run,
            ):
                result = validate_latex_proposal(proposal, latexmk=Path("tectonic"))

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                run.call_args.args[0],
                (str(tectonic), "--untrusted", "--keep-logs", "proposal.tex"),
            )

    def test_adds_compiler_directory_to_child_path(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            proposal = root / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")
            compiler_dir = root / "compiler"
            compiler_dir.mkdir()
            latexmk = compiler_dir / "latexmk"
            latexmk.write_text("synthetic compiler", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="child engine found\n", stderr=""
            )

            with (
                patch.dict("os.environ", {"PATH": os.pathsep.join(("one", "two"))}, clear=False),
                patch("erga_mcp.resumes.artifacts.shutil.which", return_value=str(latexmk)),
                patch("erga_mcp.resumes.artifacts.subprocess.run", return_value=completed) as run,
            ):
                result = validate_latex_proposal(proposal, latexmk=latexmk)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "child engine found\n")
            self.assertEqual(
                run.call_args.kwargs["env"]["PATH"].split(os.pathsep)[0], str(compiler_dir)
            )


if __name__ == "__main__":
    unittest.main()
