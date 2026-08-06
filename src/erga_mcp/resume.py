from __future__ import annotations

import difflib
import errno
import json
import os
import re
import shutil
import subprocess
import sys
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
_TERM_CYCLE = re.compile(
    r"^(?:(?P<season_a>spring|summer|fall|winter)[-_ ]*(?P<year_a>20\d{2})|"
    r"(?P<year_b>20\d{2})[-_ ]*(?P<season_b>spring|summer|fall|winter))$",
    re.IGNORECASE,
)
_SECTION_HEADING = re.compile(r"^\\section\{(?P<name>[^}]+)\}\s*$", re.MULTILINE)
_MACOS_TEXBIN = Path("/Library/TeX/texbin")
_LATEX_COMMAND_WITH_ARGUMENT = re.compile(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?\{([^{}]*)\}")
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?")
_SPACE = re.compile(r"\s+")


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def resolve_section_name(source: str, requested_name: str) -> str:
    """Resolve configured section spellings without depending on case or separators."""
    requested_key = _section_key(requested_name)
    matches = [
        match.group("name")
        for match in _SECTION_HEADING.finditer(source)
        if _section_key(match.group("name")) == requested_key
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one section matching {requested_name!r}")
    return matches[0]


def resolve_latexmk_executable(latexmk: Path = Path("latexmk")) -> Path:
    """Resolve latexmk even when a macOS launch agent omits MacTeX from PATH."""
    configured = latexmk.expanduser()
    discovered = shutil.which(str(configured))
    if discovered is not None:
        return Path(discovered).absolute()

    if sys.platform == "darwin" and configured.parent == Path("."):
        mactex_executable = _MACOS_TEXBIN / configured.name
        if mactex_executable.is_file() and os.access(mactex_executable, os.X_OK):
            return mactex_executable

    raise FileNotFoundError(
        errno.ENOENT,
        (
            f"LaTeX compiler {str(configured)!r} was not found on PATH"
            + (
                f" or in {_MACOS_TEXBIN}"
                if sys.platform == "darwin" and configured.parent == Path(".")
                else ""
            )
        ),
        str(configured),
    )


def normalize_cycle(cycle: str) -> str:
    """Use one stable directory spelling for recognizable recruiting terms."""
    match = _TERM_CYCLE.fullmatch(cycle.strip())
    if match is None:
        return cycle
    season = match.group("season_a") or match.group("season_b")
    year = match.group("year_a") or match.group("year_b")
    assert season is not None and year is not None
    return f"{season.casefold()}-{year}"


def replace_section_contents(source: str, section_name: str, replacement: str) -> str:
    """Replace exactly one top-level LaTex section body without touching other sections."""
    resolved_name = resolve_section_name(source, section_name)
    matches = [
        match for match in _SECTION_HEADING.finditer(source) if match.group("name") == resolved_name
    ]
    start = matches[0].end()
    following = _SECTION_HEADING.search(source, start)
    end = following.start() if following else len(source)
    return source[:start].rstrip() + "\n" + replacement.strip() + "\n" + source[end:]


def append_section_contents(source: str, section_name: str, addition: str) -> str:
    """Append to one section body without removing existing résumé content."""
    resolved_name = resolve_section_name(source, section_name)
    matches = [
        match for match in _SECTION_HEADING.finditer(source) if match.group("name") == resolved_name
    ]
    following = _SECTION_HEADING.search(source, matches[0].end())
    insertion = following.start() if following else len(source)
    prefix = source[:insertion].rstrip() + "\n"
    return prefix + addition.strip() + "\n" + source[insertion:]


def latex_to_text(value: str) -> str:
    """Render the text-bearing subset of LaTeX used by resume bullets."""
    rendered = value
    previous = None
    while rendered != previous:
        previous = rendered
        rendered = _LATEX_COMMAND_WITH_ARGUMENT.sub(r"\1", rendered)
    rendered = re.sub(r"\\([%&#_$])", r"\1", rendered)
    rendered = _LATEX_COMMAND.sub(" ", rendered)
    rendered = rendered.replace("{", " ").replace("}", " ")
    rendered = rendered.replace("~", " ")
    return _SPACE.sub(" ", rendered).strip()


def _command_arguments(source: str, command: str) -> tuple[str, ...]:
    """Extract balanced required arguments for a LaTeX command."""
    needle = f"\\{command}"
    arguments: list[str] = []
    position = 0
    while True:
        command_start = source.find(needle, position)
        if command_start < 0:
            return tuple(arguments)
        argument_start = command_start + len(needle)
        while argument_start < len(source) and source[argument_start].isspace():
            argument_start += 1
        if argument_start >= len(source) or source[argument_start] != "{":
            position = command_start + len(needle)
            continue
        depth = 0
        argument_end = argument_start
        while argument_end < len(source):
            character = source[argument_end]
            escaped = argument_end > 0 and source[argument_end - 1] == "\\"
            if character == "{" and not escaped:
                depth += 1
            elif character == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    arguments.append(source[argument_start + 1 : argument_end])
                    position = argument_end + 1
                    break
            argument_end += 1
        else:
            raise ValueError(f"unterminated \\{command} argument")


def resume_bullet_length_report(
    latex_content: str,
    *,
    minimum: int,
    target: int,
    maximum: int,
) -> dict[str, object]:
    """Validate newly authored resumeItem bullets against configured character limits."""
    configured = any((minimum, target, maximum))
    if configured and not 0 < minimum <= target <= maximum:
        raise ValueError("resume bullet character lengths must be zero or ordered positive values")
    bullets = resume_item_texts(latex_content)
    soft_deviations = [
        {"length": len(text), "text": text}
        for text in bullets
        if configured and len(text) < minimum
    ]
    violations = [
        {"length": len(text), "text": text}
        for text in bullets
        if configured and len(text) > maximum
    ]
    return {
        "configured": configured,
        "maximum": maximum,
        "minimum": minimum,
        "passed": not violations,
        "soft_deviations": soft_deviations,
        "target": target,
        "validated_bullets": len(bullets),
        "violations": violations,
    }


def resume_item_texts(latex_content: str) -> tuple[str, ...]:
    """Return rendered text for every resume bullet in source order."""
    return tuple(latex_to_text(value) for value in _command_arguments(latex_content, "resumeItem"))


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
    normalized_cycle = normalize_cycle(cycle)
    cycle_dir = output_root / _safe_path_component(normalized_cycle)
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
                "cycle": normalized_cycle,
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
    bullet_min_chars: int = 0,
    bullet_target_chars: int = 0,
    bullet_max_chars: int = 0,
) -> ResumeProposal:
    """Create a section-only proposal. The source template is never modified."""
    if resume_path.suffix.lower() != ".tex":
        raise ValueError("resume_path must point to a .tex file")
    if not evidence or any(not item.approved for item in evidence):
        raise ValueError("a resume proposal requires approved evidence")
    if any(marker in latex_content for marker in _DISALLOWED_LATEX):
        raise ValueError("latex_content contains a disallowed file or shell command")
    bullet_report = resume_bullet_length_report(
        latex_content,
        minimum=bullet_min_chars,
        target=bullet_target_chars,
        maximum=bullet_max_chars,
    )
    violations = bullet_report["violations"]
    if isinstance(violations, list) and violations:
        rendered = ", ".join(str(item["length"]) for item in violations)
        raise ValueError(
            "new resume bullet lengths must not exceed "
            f"{bullet_max_chars} characters; received {rendered}"
        )
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
                "constraints": {"bullet_characters": bullet_report},
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
        for _, name in sorted(enumerate(names), key=lambda item: item[1].casefold() not in terms)
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


def create_baseline_resume_proposal(
    *, resume_path: Path, output_dir: Path, evidence: list[Evidence], reason: str
) -> ResumeProposal:
    """Copy a résumé into a reviewable proposal when no truthful edit is available."""
    if resume_path.suffix.lower() != ".tex" or not resume_path.is_file():
        raise ValueError("resume_path must point to an existing .tex file")
    if any(not item.approved for item in evidence):
        raise ValueError("baseline proposal evidence must be approved")
    if not reason.strip():
        raise ValueError("reason cannot be empty")
    original = resume_path.read_text(encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"
    proposed_tex_path.write_text(original, encoding="utf-8")
    diff_path.write_text("", encoding="utf-8")
    claim_report_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "external_sync": "not performed",
                "reason": reason,
                "source_modified": False,
                "tailoring": "baseline copy; no unsupported claims added",
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
    proposed = f"{original.rstrip()}\n\n% Erga MCP proposal\n{latex_snippet.strip()}\n"
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
    latexmk_executable = resolve_latexmk_executable(latexmk)
    command = (
        str(latexmk_executable),
        "-pdf",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        proposal_path.name,
    )
    environment = os.environ.copy()
    executable_directory = str(latexmk_executable.parent)
    path_entries = environment.get("PATH", "").split(os.pathsep)
    if executable_directory not in path_entries:
        environment["PATH"] = os.pathsep.join(
            [executable_directory, *[entry for entry in path_entries if entry]]
        )
    completed = subprocess.run(
        command,
        cwd=proposal_path.parent,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=120,
    )
    return LatexValidation(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
