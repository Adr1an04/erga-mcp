from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.resumes.artifacts import LatexValidation, ResumeItemLayoutValidation
from erga_mcp.resumes.render_validation import validate_resume_render
from erga_mcp.resumes.tailoring import PdfPageFill


class ResumeRenderValidationTests(unittest.TestCase):
    def test_strict_mode_unions_pdf_text_and_physical_tex_line_measurements(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text(
                "\\newcommand{\\resumeItem}[1]{\\item #1}"
                "\\begin{document}\\begin{itemize}"
                "\\resumeItem{Visible text fits but template glue does not.}"
                "\\end{itemize}\\end{document}\n",
                encoding="utf-8",
            )

            def compile_success(path: Path, *, latexmk: Path) -> LatexValidation:
                del latexmk
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation(("synthetic",), 0, "", "")

            result = validate_resume_render(
                proposal,
                latexmk=Path("synthetic"),
                compiler=compile_success,
                compiled_layout_checker=lambda _tex, _pdf: ResumeItemLayoutValidation(
                    (), 0, 1, (), "", "", ()
                ),
                layout_checker=lambda _tex, *, latexmk: ResumeItemLayoutValidation(
                    (str(latexmk),), 0, 1, (0,), "", "", ()
                ),
                reject_wrapped_items=True,
                max_pages=0,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.wrapped_item_indices, (0,))

    def test_reuses_compiled_pdf_for_layout_and_allows_healthy_wrapping(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text(
                "\\begin{document}\\begin{itemize}\\item Synthetic bullet."
                "\\end{itemize}\\end{document}\n",
                encoding="utf-8",
            )
            compile_calls = 0

            def compile_success(path: Path, *, latexmk: Path) -> LatexValidation:
                nonlocal compile_calls
                del latexmk
                compile_calls += 1
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation(("synthetic",), 0, "", "")

            result = validate_resume_render(
                proposal,
                latexmk=Path("synthetic"),
                compiler=compile_success,
                compiled_layout_checker=lambda _tex, _pdf: ResumeItemLayoutValidation(
                    (), 0, 1, (0,), "", "", ()
                ),
                reject_wrapped_items=False,
                max_pages=0,
            )

            self.assertTrue(result.passed)
            self.assertEqual(compile_calls, 1)
            self.assertEqual(result.wrapped_item_indices, (0,))

    def test_single_line_mode_rejects_any_rendered_wrap(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text(
                "\\begin{document}\\begin{itemize}\\item Synthetic bullet."
                "\\end{itemize}\\end{document}\n",
                encoding="utf-8",
            )

            def compile_success(path: Path, *, latexmk: Path) -> LatexValidation:
                del latexmk
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation(("synthetic",), 0, "", "")

            result = validate_resume_render(
                proposal,
                latexmk=Path("synthetic"),
                compiler=compile_success,
                compiled_layout_checker=lambda _tex, _pdf: ResumeItemLayoutValidation(
                    (), 0, 1, (0,), "", "", ()
                ),
                reject_wrapped_items=True,
                max_pages=0,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.wrapped_item_indices, (0,))
            self.assertIn("wrapped bullets [0]", result.reason or "")
            self.assertFalse(proposal.with_suffix(".pdf").exists())

    def test_configurable_line_limit_accepts_two_lines_and_rejects_three(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text(
                "\\begin{document}\\begin{itemize}\\item Synthetic bullet."
                "\\end{itemize}\\end{document}\n",
                encoding="utf-8",
            )

            def compile_success(path: Path, *, latexmk: Path) -> LatexValidation:
                del latexmk
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation(("synthetic",), 0, "", "")

            for line_count, expected_passed in ((2, True), (3, False)):
                with self.subTest(line_count=line_count):
                    result = validate_resume_render(
                        proposal,
                        latexmk=Path("synthetic"),
                        compiler=compile_success,
                        compiled_layout_checker=lambda _tex, _pdf, count=line_count: (
                            ResumeItemLayoutValidation((), 0, 1, (0,), "", "", (), (count,))
                        ),
                        reject_wrapped_items=False,
                        maximum_item_lines=2,
                        max_pages=0,
                    )

                    self.assertEqual(result.passed, expected_passed)
                    if expected_passed:
                        proposal.with_suffix(".pdf").unlink(missing_ok=True)
                    else:
                        self.assertIn("exceeding 2 rendered line", result.reason or "")

    def test_rejects_overfull_boxes_even_when_compilation_returns_success(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")

            def compile_with_overflow(path: Path, *, latexmk: Path) -> LatexValidation:
                del latexmk
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation((), 0, "Overfull \\hbox detected", "")

            result = validate_resume_render(
                proposal,
                latexmk=Path("synthetic"),
                compiler=compile_with_overflow,
                max_pages=0,
                check_item_layout=False,
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.overfull_box_count, 1)
            self.assertFalse(proposal.with_suffix(".pdf").exists())

    def test_returns_a_bounded_human_safe_success_summary(self) -> None:
        with TemporaryDirectory() as directory:
            proposal = Path(directory) / "proposal.tex"
            proposal.write_text("\\begin{document}ok\\end{document}\n", encoding="utf-8")

            def compile_success(path: Path, *, latexmk: Path) -> LatexValidation:
                del latexmk
                path.with_suffix(".pdf").write_bytes(b"synthetic")
                return LatexValidation(("synthetic",), 0, "compiler internals", "")

            result = validate_resume_render(
                proposal,
                latexmk=Path("synthetic"),
                output_pdf_name="Candidate_Resume.pdf",
                compiler=compile_success,
                page_counter=lambda _path: 1,
                fill_reader=lambda _path: PdfPageFill(100, 90, 5, 0.85, 20),
                minimum_page_fill_ratio=0.82,
                check_item_layout=False,
            )

            self.assertTrue(result.passed)
            self.assertEqual(Path(str(result.pdf)).name, "Candidate_Resume.pdf")
            self.assertEqual(result.page_fill_ratio, 0.85)
            self.assertNotIn("compiler internals", str(result.as_dict()))


if __name__ == "__main__":
    unittest.main()
