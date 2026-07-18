from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import Evidence
from .resume import ResumePackage, create_job_package


@dataclass(frozen=True)
class JobWorkspace:
    package: ResumePackage
    job_snapshot_path: Path
    selected_evidence_path: Path
    template_copy_path: Path


def create_job_workspace(
    *,
    output_root: Path,
    cycle: str,
    application_slug: str,
    job_url: str,
    job_snapshot: str,
    template_path: Path,
    selected_evidence: list[Evidence],
) -> JobWorkspace:
    """Make an isolated, Finder-visible job workspace from local approved evidence."""
    if not selected_evidence or any(not item.approved for item in selected_evidence):
        raise ValueError("job workspace requires approved selected evidence")
    if not template_path.is_file() or template_path.suffix.lower() != ".tex":
        raise ValueError("template_path must be an existing .tex file")
    package = create_job_package(
        output_root=output_root, cycle=cycle, application_slug=application_slug, job_url=job_url
    )
    snapshot_path = package.package_dir / "research" / "job-description.txt"
    snapshot_path.write_text(job_snapshot + "\n", encoding="utf-8")
    evidence_path = package.package_dir / "research" / "selected-evidence.json"
    evidence_path.write_text(
        json.dumps(
            [
                {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                for item in selected_evidence
            ],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    template_copy_path = package.package_dir / "source" / "resume.tex"
    shutil.copy2(template_path, template_copy_path)
    return JobWorkspace(package, snapshot_path, evidence_path, template_copy_path)
