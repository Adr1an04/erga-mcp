from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from erga_mcp.resumes.artifacts import (
    LatexValidation,
    ResumeItemLayoutValidation,
    inspect_compiled_resume_item_layout,
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
    reject_wrapped_items: bool = True,
    maximum_item_lines: int = 0,
    compiler: Callable[..., LatexValidation] = validate_latex_proposal,
    layout_checker: Callable[..., ResumeItemLayoutValidation] = validate_single_line_resume_items,
    compiled_layout_checker: Callable[
        [Path, Path], ResumeItemLayoutValidation
    ] = inspect_compiled_resume_item_layout,
    page_counter: Callable[[Path], int] = pdf_page_count,
    fill_reader: Callable[[Path], PdfPageFill] = pdf_page_fill,
) -> ResumeRenderValidation:
    """Compile and reject structurally broken, overflowing, sparse, or overlong resumes."""
    if maximum_item_lines < 0:
        raise ValueError("maximum_item_lines must be zero or positive")
    effective_maximum_item_lines = 1 if reject_wrapped_items else maximum_item_lines
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
        used_physical_layout = False
        try:
            layout = compiled_layout_checker(proposal_path, proposal_pdf)
        except ValueError:
            # Some uncommon PDF producers omit bullet glyphs from their text layer. Preserve the
            # exact TeX-instrumented validator as a compatibility fallback, not the common path.
            layout = layout_checker(proposal_path, latexmk=latexmk)
            used_physical_layout = True
        # A PDF text layer can prove that visible words wrapped, but it cannot expose a physical
        # line containing only template glue. When a line limit is configured, also inspect TeX's
        # actual paragraph line count for custom resume-item macros and union both measurements.
        if effective_maximum_item_lines and r"\resumeItem" in source and not used_physical_layout:
            physical_layout = layout_checker(proposal_path, latexmk=latexmk)
            if physical_layout.returncode != 0:
                proposal_pdf.unlink(missing_ok=True)
                return ResumeRenderValidation(
                    passed=False,
                    returncode=1,
                    pdf=None,
                    page_count=page_count,
                    reason="Resume physical-line measurement failed.",
                )
            visible_line_counts = layout.item_line_counts or tuple(
                2 if index in layout.wrapped_item_indices else 1
                for index in range(layout.item_count)
            )
            physical_line_counts = physical_layout.item_line_counts or tuple(
                2 if index in physical_layout.wrapped_item_indices else 1
                for index in range(physical_layout.item_count)
            )
            line_counts = (
                tuple(
                    max(visible, physical)
                    for visible, physical in zip(
                        visible_line_counts,
                        physical_line_counts,
                        strict=True,
                    )
                )
                if len(visible_line_counts) == len(physical_line_counts)
                else physical_line_counts or visible_line_counts
            )
            layout = ResumeItemLayoutValidation(
                command=physical_layout.command,
                returncode=0,
                item_count=max(layout.item_count, physical_layout.item_count),
                wrapped_item_indices=tuple(
                    sorted(
                        set(layout.wrapped_item_indices) | set(physical_layout.wrapped_item_indices)
                    )
                ),
                orphan_item_indices=tuple(
                    sorted(
                        set(layout.orphan_item_indices) | set(physical_layout.orphan_item_indices)
                    )
                ),
                stdout=physical_layout.stdout,
                stderr=physical_layout.stderr,
                item_line_counts=line_counts,
            )
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
        over_line_limit: tuple[int, ...]
        if not effective_maximum_item_lines:
            over_line_limit = ()
        elif len(layout.item_line_counts) == layout.item_count:
            over_line_limit = tuple(
                index
                for index, count in enumerate(layout.item_line_counts)
                if count > effective_maximum_item_lines
            )
        elif effective_maximum_item_lines == 1:
            over_line_limit = layout.wrapped_item_indices
        else:
            proposal_pdf.unlink(missing_ok=True)
            return ResumeRenderValidation(
                passed=False,
                returncode=1,
                pdf=None,
                page_count=page_count,
                reason="Resume bullet line-count measurement was incomplete.",
            )
        if (effective_maximum_item_lines and over_line_limit) or orphans:
            proposal_pdf.unlink(missing_ok=True)
            details = []
            if effective_maximum_item_lines and over_line_limit:
                if effective_maximum_item_lines == 1:
                    details.append(f"wrapped bullets {list(over_line_limit)}")
                else:
                    details.append(
                        f"bullets exceeding {effective_maximum_item_lines} rendered line(s) "
                        f"{list(over_line_limit)}"
                    )
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
