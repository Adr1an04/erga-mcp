from __future__ import annotations

from pathlib import Path

from .contracts import IntakeValidationResult


def validation_from_manifest(
    *, package_dir: Path, manifest: dict[str, object], reused: bool
) -> IntakeValidationResult:
    """Translate a persisted package manifest into the MCP validation contract."""
    raw_validation = manifest.get("validation")
    if not isinstance(raw_validation, dict):
        proposal_pdf = package_dir / "artifacts" / "proposal.pdf"
        return IntakeValidationResult(
            returncode=0 if proposal_pdf.is_file() else None,
            pdf=str(proposal_pdf) if proposal_pdf.is_file() else None,
            skipped=(
                "Legacy package reused; the original validation outcome was not recorded."
                if reused
                else None
            ),
        )

    raw_returncode = raw_validation.get("returncode")
    returncode = (
        raw_returncode
        if isinstance(raw_returncode, int) and not isinstance(raw_returncode, bool)
        else None
    )
    raw_skipped = raw_validation.get("skipped")
    skipped = raw_skipped if isinstance(raw_skipped, str) else None
    raw_page_count = raw_validation.get("page_count")
    page_count = (
        raw_page_count
        if isinstance(raw_page_count, int) and not isinstance(raw_page_count, bool)
        else None
    )
    raw_page_fill_ratio = raw_validation.get("page_fill_ratio")
    page_fill_ratio = (
        float(raw_page_fill_ratio)
        if isinstance(raw_page_fill_ratio, (int, float))
        and not isinstance(raw_page_fill_ratio, bool)
        else None
    )
    raw_minimum_page_fill_ratio = raw_validation.get("minimum_page_fill_ratio")
    minimum_page_fill_ratio = (
        float(raw_minimum_page_fill_ratio)
        if isinstance(raw_minimum_page_fill_ratio, (int, float))
        and not isinstance(raw_minimum_page_fill_ratio, bool)
        else None
    )
    raw_pdf = raw_validation.get("pdf")
    pdf: str | None = None
    if isinstance(raw_pdf, str):
        relative_pdf = Path(raw_pdf)
        safe_pdf = (
            not relative_pdf.is_absolute()
            and len(relative_pdf.parts) == 2
            and relative_pdf.parts[0] == "artifacts"
            and relative_pdf.suffix.casefold() == ".pdf"
        )
        recorded_pdf = package_dir / relative_pdf if safe_pdf else None
        if recorded_pdf is not None and recorded_pdf.is_file():
            pdf = str(recorded_pdf)
        else:
            missing = "Recorded validation PDF is missing from the package."
            skipped = f"{skipped} {missing}" if skipped else missing
    if reused:
        reuse_note = "Existing complete package reused; no job-page network request ran."
        skipped = f"{skipped} {reuse_note}" if skipped else reuse_note
    return IntakeValidationResult(
        returncode=returncode,
        pdf=pdf,
        page_count=page_count,
        page_fill_ratio=page_fill_ratio,
        minimum_page_fill_ratio=minimum_page_fill_ratio,
        skipped=skipped,
    )
