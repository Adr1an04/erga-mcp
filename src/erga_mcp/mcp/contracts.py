from __future__ import annotations

from pydantic import BaseModel, Field


class IntakeValidationResult(BaseModel):
    """Structured local LaTeX validation status returned by job intake."""

    returncode: int | None
    pdf: str | None
    page_count: int | None = None
    page_fill_ratio: float | None = None
    minimum_page_fill_ratio: float | None = None
    skipped: str | None = None


class IntakeProjectSelection(BaseModel):
    """One selected project and the role terms that deterministically justified it."""

    id: str
    title: str
    matched_terms: list[str] = Field(default_factory=list)
    matched_signals: list[str] = Field(default_factory=list)


class IntakeJobResult(BaseModel):
    """Structured paths and status returned by the primary job-link intake tool."""

    package_dir: str
    job_snapshot: str
    selected_evidence: str
    selection_strategy: str
    project_selections: list[IntakeProjectSelection] = Field(default_factory=list)
    git_project_research: list[dict[str, object]] = Field(default_factory=list)
    proposal_tex: str
    diff: str
    claim_report: str
    validation: IntakeValidationResult
    decision_report: str | None = None
    tailoring_meaningful_change: bool = False
    tailoring_changed_sections: list[str] = Field(default_factory=list)
    tailoring_version: int | None = None
    research_note: str | None = None
    application_id: str | None = None
    generated_resume_version_id: str | None = None
    used_resume_version_id: str | None = None
    tracker_notes: list[str] = Field(default_factory=list)
    tracker_cycles: list[str] = Field(default_factory=list)
    integration_warnings: list[str] = Field(default_factory=list)
    reused: bool = False


class SecondarySearchInput(BaseModel):
    """One bounded host-provided search result captured after primary intake."""

    query: str = Field(min_length=1, max_length=400)
    result: str = Field(min_length=1, max_length=30_000)
