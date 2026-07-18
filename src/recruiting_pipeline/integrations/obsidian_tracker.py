from __future__ import annotations

from pathlib import Path


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in " -_" else "" for char in value).strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("company and role must contain a safe display name")
    return cleaned


def write_job_tracker_note(
    *,
    vault_path: Path,
    cycle: str,
    company: str,
    role: str,
    job_url: str,
    package_dir: Path,
    resume_pdf: Path | None = None,
) -> Path:
    """Create a local Obsidian tracker note; it never changes external job systems."""
    if not job_url.startswith(("https://", "http://")):
        raise ValueError("job URL must use HTTP(S)")
    safe_company, safe_role = _safe_name(company), _safe_name(role)
    notes_dir = vault_path / "02 Projects" / "Job Applications"
    notes_dir.mkdir(parents=True, exist_ok=True)
    note_path = notes_dir / f"{safe_company} — {safe_role}.md"
    if note_path.exists():
        return note_path
    relative_package = package_dir.expanduser().resolve().as_posix()
    pdf_line = f"- Resume PDF: `{resume_pdf.expanduser().resolve()}`\n" if resume_pdf else ""
    note_path.write_text(
        f"# {safe_company} — {safe_role}\n\n"
        f"- Cycle: [[{cycle} Applications]]\n"
        "- Status: Ready to apply\n"
        f"- Job URL: {job_url}\n"
        f"- Package: `{relative_package}`\n"
        f"{pdf_line}"
        "- Next action: Review tailored résumé and job requirements.\n\n"
        "## Resume / portfolio emphasis\n\n"
        "Generated from approved career evidence; see the package claim report.\n",
        encoding="utf-8",
    )
    return note_path
