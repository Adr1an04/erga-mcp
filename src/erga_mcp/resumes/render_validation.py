from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from erga_mcp.resumes.artifacts import (
    LatexValidation,
    ResumeItemLayoutValidation,
    validate_latex_proposal,
    validate_single_line_resume_items,
)
from erga_mcp.resumes.tailoring import (
    PdfPageFill,
    pdf_page_count,
    pdf_page_fill,
    semantic_resume_structure_issues,
)


@dataclass(frozen=True)
class ResumeRenderValidation:
    """Human-safe, reusable validation for one generated resume artifact."""

    passed: bool
    returncode: int | None
    pdf: str | None
    page_count: int | None = None
    page_fill_ratio: float | None = None
    minimum_page_fill_ratio: float | None = None
    wrapped_item_indices: tuple[int, ...] = ()
    orphan_item_indices: tuple[int, ...] = ()
    overfull_box_count: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_resume_render(
    proposal_path: Path,
    *,
    latexmk: Path,
    output_pdf_name: str | None = None,
    max_pages: int = 1,
    minimum_page_fill_ratio: float = 0,
    check_item_layout: bool = True,
    compiler: Callable[..., LatexValidation] = validate_latex_proposal,
    layout_checker: Callable[..., ResumeItemLayoutValidation] = validate_single_line_resume_items,
    page_counter: Callable[[Path], int] = pdf_page_count,
    fill_reader: Callable[[Path], PdfPageFill] = pdf_page_fill,
) -> ResumeRenderValidation:
    """Compile and reject structurally broken, overflowing, sparse, or wrapped resumes."""
    source = proposal_path.read_text(encoding="utf-8")
    structure_issues = semantic_resume_structure_issues(source)
    if structure_issues:
        return ResumeRenderValidation(
            passed=False,
            returncode=1,
            pdf=None,
            reason="Semantic resume structure failed: " + "; ".join(structure_issues),
        )
    try:
        checked = compiler(proposal_path, latexmk=latexmk)
    except (OSError, subprocess.TimeoutExpired) as error:
        return ResumeRenderValidation(
            passed=False,
            returncode=None,
            pdf=None,
            reason=f"LaTeX validation did not complete: {error}",
        )
    compile_output = "\n".join((checked.stdout, checked.stderr))
    overfull_count = compile_output.count("Overfull \\hbox") + compile_output.count(
        "Overfull \\vbox"
    )
    proposal_pdf = proposal_path.with_suffix(".pdf")
    if checked.returncode != 0:
        proposal_pdf.unlink(missing_ok=True)
        return ResumeRenderValidation(
            passed=False,
            returncode=checked.returncode,
            pdf=None,
            overfull_box_count=overfull_count,
            reason="LaTeX compilation failed.",
        )
    # Test doubles and unusual compilers can report success without writing a PDF. Keep the
    # compile result truthful, but never claim the application-ready artifact exists.
    if not proposal_pdf.is_file():
        return ResumeRenderValidation(
            passed=False,
            returncode=0,
            pdf=None,
            overfull_box_count=overfull_count,
            reason="LaTeX returned success but did not produce a PDF.",
        )
    if overfull_count:
        proposal_pdf.unlink(missing_ok=True)
        return ResumeRenderValidation(
            passed=False,
            returncode=1,
            pdf=None,
            overfull_box_count=overfull_count,
            reason=f"Rendered resume contains {overfull_count} overflowing TeX box(es).",
        )

    page_count: int | None = None
    if max_pages or minimum_page_fill_ratio:
        try:
            page_count = page_counter(proposal_pdf)
        except ValueError as error:
            proposal_pdf.unlink(missing_ok=True)
            return ResumeRenderValidation(
                passed=False,
                returncode=1,
                pdf=None,
                reason=f"PDF page validation failed: {error}",
            )
    if max_pages and page_count is not None and page_count > max_pages:
        proposal_pdf.unlink(missing_ok=True)
        return ResumeRenderValidation(
            passed=False,
            returncode=1,
            pdf=None,
            page_count=page_count,
            reason=f"Resume has {page_count} pages; configured maximum is {max_pages}.",
        )

    wrapped: tuple[int, ...] = ()
    orphans: tuple[int, ...] = ()
    if check_item_layout:
        layout = layout_checker(proposal_path, latexmk=latexmk)
        if layout.returncode != 0:
            proposal_pdf.unlink(missing_ok=True)
            return ResumeRenderValidation(
                passed=False,
                returncode=1,
                pdf=None,
                page_count=page_count,
                reason="Resume bullet layout measurement failed.",
            )
        wrapped = layout.wrapped_item_indices
        orphans = layout.orphan_item_indices
        if wrapped or orphans:
            proposal_pdf.unlink(missing_ok=True)
            details = []
            if wrapped:
                details.append(f"wrapped bullets {list(wrapped)}")
            if orphans:
                details.append(f"stranded short tails {list(orphans)}")
            return ResumeRenderValidation(
                passed=False,
                returncode=1,
                pdf=None,
                page_count=page_count,
                wrapped_item_indices=wrapped,
                orphan_item_indices=orphans,
                reason="Resume item layout failed: " + ", ".join(details) + ".",
            )

    effective_fill = 0 if page_count != 1 else minimum_page_fill_ratio
    fill_ratio: float | None = None
    if effective_fill:
        fill_ratio = fill_reader(proposal_pdf).fill_ratio
        if fill_ratio < effective_fill:
            proposal_pdf.unlink(missing_ok=True)
            return ResumeRenderValidation(
                passed=False,
                returncode=1,
                pdf=None,
                page_count=page_count,
                page_fill_ratio=fill_ratio,
                minimum_page_fill_ratio=effective_fill,
                reason=(
                    f"Resume fills {fill_ratio:.1%} of the page; configured minimum is "
                    f"{effective_fill:.1%}."
                ),
            )

    output_pdf = proposal_pdf
    if output_pdf_name:
        output_pdf = proposal_pdf.with_name(output_pdf_name)
        if output_pdf != proposal_pdf:
            proposal_pdf.replace(output_pdf)
    return ResumeRenderValidation(
        passed=True,
        returncode=0,
        pdf=str(output_pdf),
        page_count=page_count,
        page_fill_ratio=fill_ratio,
        minimum_page_fill_ratio=(effective_fill or None),
        wrapped_item_indices=wrapped,
        orphan_item_indices=orphans,
    )
