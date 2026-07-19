from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import Evidence


@dataclass(frozen=True)
class ResumeProposal:
    proposed_tex_path: Path
    diff_path: Path
    claim_report_path: Path


@dataclass(frozen=True)
class LatexValidation:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ResumePackage:
    package_dir: Path
    manifest_path: Path


_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SECTION_HEADING = re.compile(r"^\\section\{(?P<name>[^}]+)\}\s*$", re.MULTILINE)


def replace_section_contents(source: str, section_name: str, replacement: str) -> str:
    """Replace exactly one top-level LaTex section body without touching other sections."""
    matches = [
        match for match in _SECTION_HEADING.finditer(source) if match.group("name") == section_name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one section named {section_name!r}")
    start = matches[0].end()
    following = _SECTION_HEADING.search(source, start)
    end = following.start() if following else len(source)
    return source[:start].rstrip() + "\n" + replacement.strip() + "\n" + source[end:]


def append_section_contents(source: str, section_name: str, addition: str) -> str:
    """Append to one section body without removing existing résumé content."""
    matches = [
        match for match in _SECTION_HEADING.finditer(source) if match.group("name") == section_name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one section named {section_name!r}")
    following = _SECTION_HEADING.search(source, matches[0].end())
    insertion = following.start() if following else len(source)
    prefix = source[:insertion].rstrip() + "\n"
    return prefix + addition.strip() + "\n" + source[insertion:]


def _safe_path_component(value: str) -> str:
    if not _SAFE_PATH_COMPONENT.fullmatch(value):
        raise ValueError("cycle and application slug must be safe path component values")
    return value


def create_job_package(
    *, output_root: Path, cycle: str, application_slug: str, job_url: str
) -> ResumePackage:
    """Create a generic, isolated workspace before a template adapter is selected."""
    if not job_url.startswith(("http://", "https://")):
        raise ValueError("job_url must be an HTTP(S) URL")
    output_root.mkdir(parents=True, exist_ok=True)
    cycle_dir = output_root / _safe_path_component(cycle)
    if cycle_dir.is_symlink():
        raise ValueError("resume package directories must not be a symlink")
    cycle_dir.mkdir(exist_ok=True)
    package_dir = cycle_dir / _safe_path_component(application_slug)
    if package_dir.is_symlink():
        raise ValueError("resume package directories must not be a symlink")
    if package_dir.exists():
        raise FileExistsError(f"resume package already exists: {package_dir}")
    package_dir.mkdir()
    (package_dir / "source").mkdir()
    (package_dir / "artifacts").mkdir()
    (package_dir / "research").mkdir()
    manifest_path = package_dir / "package.json"
    manifest_path.write_text(
        json.dumps(
            {
                "application_slug": application_slug,
                "created_at": datetime.now(UTC).isoformat(),
                "cycle": cycle,
                "job_url": job_url,
                "template_status": "not_copied",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ResumePackage(package_dir=package_dir, manifest_path=manifest_path)


_DISALLOWED_LATEX = ("\\input", "\\include", "\\write18", "\\immediate\\write")


def create_section_resume_proposal(
    *,
    resume_path: Path,
    output_dir: Path,
    section_name: str,
    latex_content: str,
    evidence: list[Evidence],
) -> ResumeProposal:
    """Create a section-only proposal. The source template is never modified."""
    if resume_path.suffix.lower() != ".tex":
        raise ValueError("resume_path must point to a .tex file")
    if not evidence or any(not item.approved for item in evidence):
        raise ValueError("a resume proposal requires approved evidence")
    if any(marker in latex_content for marker in _DISALLOWED_LATEX):
        raise ValueError("latex_content contains a disallowed file or shell command")
    original = resume_path.read_text(encoding="utf-8")
    proposed = append_section_contents(original, section_name, latex_content)
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"
    proposed_tex_path.write_text(proposed, encoding="utf-8")
    diff_path.write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=str(resume_path),
                tofile=str(proposed_tex_path),
            )
        ),
        encoding="utf-8",
    )
    claim_report_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "edited_section": section_name,
                "external_sync": "not performed",
                "source_modified": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ResumeProposal(proposed_tex_path, diff_path, claim_report_path)


def create_keyword_prioritized_resume_proposal(
    *, resume_path: Path, output_dir: Path, job_description: str, evidence: list[Evidence]
) -> ResumeProposal:
    """Create a safe skills-only proposal by reordering existing language skills for a job."""
    if not evidence or any(not item.approved for item in evidence):
        raise ValueError("a resume proposal requires approved evidence")
    original = resume_path.read_text(encoding="utf-8")
    language_line = next(
        (line for line in original.splitlines() if "\\textbf{Languages:}" in line), None
    )
    if language_line is None:
        raise ValueError("resume has no supported Languages line to prioritize")
    prefix, values = language_line.split("Languages:}", 1)
    suffix = "\\\\" if values.rstrip().endswith("\\\\") else ""
    names = values.removesuffix(suffix).strip().split(", ")
    terms = job_description.casefold()
    prioritized = [
        name
        for _, name in sorted(enumerate(names), key=lambda item: not (item[1].casefold() in terms))
    ]
    replacement = (
        prefix + "Languages:} " + ", ".join(prioritized) + (" " + suffix if suffix else "")
    )
    proposed = original.replace(language_line, replacement, 1)
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"
    proposed_tex_path.write_text(proposed, encoding="utf-8")
    diff_path.write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=str(resume_path),
                tofile=str(proposed_tex_path),
            )
        ),
        encoding="utf-8",
    )
    claim_report_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "tailoring": (
                    "Reordered only existing language skills by exact job-description mentions."
                ),
                "source_modified": False,
                "external_sync": "not performed",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ResumeProposal(proposed_tex_path, diff_path, claim_report_path)


def create_resume_proposal(
    *, resume_path: Path, output_dir: Path, latex_snippet: str, evidence: list[Evidence]
) -> ResumeProposal:
    """Create review artifacts beside local state; never modify or sync the resume source."""
    if resume_path.suffix.lower() != ".tex":
        raise ValueError("resume_path must point to a .tex file")
    if not evidence or any(not item.approved for item in evidence):
        raise ValueError("a resume proposal requires approved evidence")
    if not latex_snippet.strip():
        raise ValueError("latex_snippet cannot be empty")
    if any(marker in latex_snippet for marker in _DISALLOWED_LATEX):
        raise ValueError("latex_snippet contains a disallowed file or shell command")

    original = resume_path.read_text(encoding="utf-8")
    proposed = f"{original.rstrip()}\n\n% Recruiting Pipeline proposal\n{latex_snippet.strip()}\n"
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"

    proposed_tex_path.write_text(proposed, encoding="utf-8")
    diff_path.write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=str(resume_path),
                tofile=str(proposed_tex_path),
            )
        ),
        encoding="utf-8",
    )
    claim_report_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "external_sync": "not performed",
                "source_modified": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return ResumeProposal(
        proposed_tex_path=proposed_tex_path,
        diff_path=diff_path,
        claim_report_path=claim_report_path,
    )


def validate_latex_proposal(
    proposal_path: Path, *, latexmk: Path = Path("latexmk")
) -> LatexValidation:
    """Compile a selected local proposal without touching the resume source or remote."""
    if proposal_path.suffix.lower() != ".tex" or not proposal_path.is_file():
        raise ValueError("proposal_path must point to an existing .tex proposal")
    command = (str(latexmk), "-pdf", "-interaction=nonstopmode", proposal_path.name)
    completed = subprocess.run(
        command,
        cwd=proposal_path.parent,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    return LatexValidation(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
