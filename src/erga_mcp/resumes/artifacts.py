from __future__ import annotations

import difflib
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from erga_mcp.models import Evidence
from erga_mcp.operations.private_files import restrict_private_directory, restrict_private_file
from erga_mcp.resumes.claims import manual_claim_support_report

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows CI
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised by POSIX CI
    msvcrt = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ResumeProposal:
    proposed_tex_path: Path
    diff_path: Path
    claim_report_path: Path
    decision_report_path: Path | None = None


@dataclass(frozen=True)
class LatexValidation:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ResumeItemLayoutValidation:
    """Exact TeX measurement of wrapped bullets and visually stranded short tails."""

    command: tuple[str, ...]
    returncode: int
    item_count: int
    wrapped_item_indices: tuple[int, ...]
    stdout: str
    stderr: str
    orphan_item_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class ResumePackage:
    package_dir: Path
    manifest_path: Path


@dataclass(frozen=True)
class ResumeUseRecord:
    """One validated generated artifact; generation is distinct from explicit use."""

    id: str
    application_id: str
    generated_at: str
    used_at: str | None
    source_sha256: str
    proposal_sha256: str
    pdf_sha256: str
    decision_sha256: str
    project_ids: tuple[str, ...]
    bullet_evidence_ids: tuple[tuple[str, ...], ...]
    validation: dict[str, object]
    quality: dict[str, object]
    decision_version: int = 1

    @property
    def used(self) -> bool:
        return self.used_at is not None

    @classmethod
    def create(
        cls,
        *,
        application_id: str,
        source_sha256: str,
        proposal_sha256: str,
        pdf_sha256: str,
        decision_sha256: str,
        project_ids: tuple[str, ...],
        bullet_evidence_ids: tuple[tuple[str, ...], ...],
        validation: dict[str, object],
        quality: dict[str, object],
        generated_at: datetime | None = None,
    ) -> ResumeUseRecord:
        if validation.get("returncode") != 0:
            raise ValueError("a resume version requires successful validation")
        if quality.get("passed") is not True:
            raise ValueError("a resume version requires a passing master-parity decision")
        if not application_id.strip():
            raise ValueError("application_id must be non-empty")
        hashes = (source_sha256, proposal_sha256, pdf_sha256, decision_sha256)
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes):
            raise ValueError("resume version hashes must be lowercase SHA-256 values")
        identity = hashlib.sha256(
            json.dumps(
                {
                    "application_id": application_id,
                    "source_sha256": source_sha256,
                    "proposal_sha256": proposal_sha256,
                    "pdf_sha256": pdf_sha256,
                    "decision_sha256": decision_sha256,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            id=f"resume_{identity}",
            application_id=application_id,
            generated_at=(generated_at or datetime.now(UTC)).isoformat(),
            used_at=None,
            source_sha256=source_sha256,
            proposal_sha256=proposal_sha256,
            pdf_sha256=pdf_sha256,
            decision_sha256=decision_sha256,
            project_ids=project_ids,
            bullet_evidence_ids=bullet_evidence_ids,
            validation=dict(validation),
            quality=dict(quality),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_RESUME_MANIFEST_LOCK = RLock()
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
_LAYOUT_MARKER = re.compile(r"ERGA-RESUME-ITEM-(?P<state>FIT|WRAP|ORPHAN):(?P<index>\d+)")
_PDF_BULLET_LINE = re.compile(r"^(?P<indent>\s*)[•●▪◦‣⁃]\s+(?P<text>.*\S)\s*$")
_STANDARD_ITEM = re.compile(r"\\item(?![A-Za-z\[])", re.MULTILINE)
_SINGLE_LINE_LAYOUT_INSTRUMENT = r"""
\newlength{\ergaResumeItemWidth}
\newcounter{ergaResumeItemCounter}
\let\ergaOriginalResumeItem\resumeItem
\renewcommand{\resumeItem}[1]{%
  \stepcounter{ergaResumeItemCounter}%
  \settowidth{\ergaResumeItemWidth}{\small #1}%
  \ifdim\ergaResumeItemWidth>\linewidth
    \typeout{ERGA-RESUME-ITEM-WRAP:\arabic{ergaResumeItemCounter}}%
  \else
    \typeout{ERGA-RESUME-ITEM-FIT:\arabic{ergaResumeItemCounter}}%
  \fi
  \ergaOriginalResumeItem{#1}%
}
"""


def _pdf_resume_item_lines(pdf_path: Path) -> tuple[tuple[str, ...], ...] | None:
    """Extract rendered line groupings for bullets from a compiled validation PDF."""
    try:
        pages = PdfReader(pdf_path).pages
        rendered = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in pages)
    except (OSError, PdfReadError, TypeError, ValueError):
        return None

    items: list[tuple[str, ...]] = []
    current: list[str] = []
    bullet_indent = 0

    def finish() -> None:
        nonlocal current
        if current:
            items.append(tuple(current))
            current = []

    for line in rendered.splitlines():
        bullet = _PDF_BULLET_LINE.match(line)
        if bullet:
            finish()
            bullet_indent = len(bullet.group("indent"))
            current = [bullet.group("text")]
            continue
        if not current:
            continue
        stripped = line.strip()
        indentation = len(line) - len(line.lstrip())
        if stripped and indentation > bullet_indent:
            current.append(stripped)
        else:
            finish()
    finish()
    return tuple(items)


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
    """Resolve the configured compiler, with MacTeX and Tectonic fallbacks."""
    configured = latexmk.expanduser()
    discovered = shutil.which(str(configured))
    if discovered is not None:
        return Path(discovered).absolute()

    if sys.platform == "darwin" and configured.parent == Path("."):
        mactex_executable = _MACOS_TEXBIN / configured.name
        if mactex_executable.is_file() and os.access(mactex_executable, os.X_OK):
            return mactex_executable

    if configured == Path("latexmk"):
        tectonic = shutil.which("tectonic")
        if tectonic is not None:
            return Path(tectonic).absolute()

    raise FileNotFoundError(
        errno.ENOENT,
        (
            f"LaTeX compiler {str(configured)!r} was not found on PATH"
            + (
                f" or in {_MACOS_TEXBIN}"
                if sys.platform == "darwin" and configured.parent == Path(".")
                else ""
            )
            + (" and Tectonic was unavailable" if configured == Path("latexmk") else "")
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

        def text_argument(match: re.Match[str]) -> str:
            content = match.group(1)
            before = rendered[match.start() - 1] if match.start() else ""
            after = rendered[match.end()] if match.end() < len(rendered) else ""
            leading = " " if before.isalnum() and content[:1].isalnum() else ""
            trailing = " " if content[-1:].isalnum() and after.isalnum() else ""
            return leading + content + trailing

        rendered = _LATEX_COMMAND_WITH_ARGUMENT.sub(text_argument, rendered)
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
    """Return rendered text for custom-macro or standard LaTeX bullets in source order."""
    # Ignore macro definitions in the preamble. Standard templates commonly use bare
    # ``\item`` entries instead of defining Erga's historical ``\resumeItem`` helper.
    document = latex_content.split(r"\begin{document}", 1)[-1]
    custom: list[tuple[int, int, str]] = []
    needle = r"\resumeItem"
    position = 0
    while (start := document.find(needle, position)) >= 0:
        cursor = start + len(needle)
        while cursor < len(document) and document[cursor].isspace():
            cursor += 1
        if cursor >= len(document) or document[cursor] != "{":
            position = cursor
            continue
        depth = 0
        end = cursor
        while end < len(document):
            character = document[end]
            escaped = end > 0 and document[end - 1] == "\\"
            if character == "{" and not escaped:
                depth += 1
            elif character == "}" and not escaped:
                depth -= 1
                if depth == 0:
                    custom.append((start, end + 1, document[cursor + 1 : end]))
                    position = end + 1
                    break
            end += 1
        else:
            raise ValueError("unterminated \\resumeItem argument")

    standard_matches = tuple(
        match
        for match in _STANDARD_ITEM.finditer(document)
        if not any(start <= match.start() < end for start, end, _ in custom)
        and "skill"
        not in re.sub(
            r"[^a-z0-9]+",
            "",
            next(
                (
                    section.group(1).casefold()
                    for section in reversed(
                        tuple(re.finditer(r"(?m)^\\section\{([^}]+)\}", document[: match.start()]))
                    )
                ),
                "",
            ),
        )
    )
    bullets: list[tuple[int, str]] = [(start, value) for start, _, value in custom]
    boundaries = sorted(
        [start for start, _, _ in custom]
        + [match.start() for match in standard_matches]
        + [len(document)]
    )
    for match in standard_matches:
        next_item = next(boundary for boundary in boundaries if boundary > match.start())
        list_end = re.search(r"\\end\{(?:itemize|enumerate|description)\}", document[match.end() :])
        end = min(
            next_item,
            match.end() + list_end.start() if list_end is not None else len(document),
        )
        bullets.append((match.start(), document[match.end() : end]))
    return tuple(rendered for _, value in sorted(bullets) if (rendered := latex_to_text(value)))


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
    restrict_private_directory(cycle_dir)
    package_dir = cycle_dir / _safe_path_component(application_slug)
    if package_dir.is_symlink():
        raise ValueError("resume package directories must not be a symlink")
    if package_dir.exists():
        raise FileExistsError(f"resume package already exists: {package_dir}")
    package_dir.mkdir()
    restrict_private_directory(package_dir)
    for directory_name in ("source", "artifacts", "research"):
        directory = package_dir / directory_name
        directory.mkdir()
        restrict_private_directory(directory)
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
    restrict_private_file(manifest_path)
    return ResumePackage(package_dir=package_dir, manifest_path=manifest_path)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_private_manifest(manifest_path: Path) -> dict[str, object]:
    if manifest_path.is_symlink():
        raise ValueError("package manifest must not be a symlink")
    if manifest_path.parent.is_symlink():
        raise ValueError("package directory must not be a symlink")
    if manifest_path.name != "package.json" or not manifest_path.is_file():
        raise ValueError("manifest_path must point to an existing package.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("package manifest must be a JSON object")
    return payload


def _write_private_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=manifest_path.parent,
        prefix=".package-",
        suffix=".json",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(manifest_path)
        restrict_private_file(manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def _resume_manifest_transaction(manifest_path: Path):
    """Serialize package-manifest read-modify-writes across threads and processes."""
    lock_path = manifest_path.with_name(".package.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    windows_lock_acquired = False
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - exercised by Windows CI
            if os.fstat(descriptor).st_size == 0:
                try:
                    os.write(descriptor, b"\0")
                except PermissionError:
                    # Another process can initialize and lock the byte after our
                    # size check. In that race, wait on the byte it created.
                    pass
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            windows_lock_acquired = True
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif msvcrt is not None and windows_lock_acquired:  # pragma: no cover
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def update_private_manifest(
    manifest_path: Path,
    update: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Apply one interprocess-safe package manifest mutation and return the persisted value."""
    with _RESUME_MANIFEST_LOCK, _resume_manifest_transaction(manifest_path):
        manifest = _load_private_manifest(manifest_path)
        update(manifest)
        _write_private_manifest(manifest_path, manifest)
        return manifest


def _resume_use_record_from_dict(value: object) -> ResumeUseRecord:
    if not isinstance(value, dict):
        raise ValueError("stored resume version must be an object")

    def required_string(name: str) -> str:
        item = value.get(name)
        if not isinstance(item, str) or not item:
            raise ValueError(f"stored resume version {name} must be a non-empty string")
        return item

    identifier = required_string("id")
    application_id = required_string("application_id")
    generated_at = required_string("generated_at")
    raw_used_at = value.get("used_at")
    if raw_used_at is not None and not isinstance(raw_used_at, str):
        raise ValueError("stored resume version used_at must be a timestamp or null")
    try:
        datetime.fromisoformat(generated_at)
        if raw_used_at is not None:
            datetime.fromisoformat(raw_used_at)
    except ValueError as error:
        raise ValueError("stored resume version timestamps must be ISO-8601") from error
    hashes = tuple(
        required_string(name)
        for name in ("source_sha256", "proposal_sha256", "pdf_sha256", "decision_sha256")
    )
    if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in hashes):
        raise ValueError("stored resume version hashes must be lowercase SHA-256 values")
    raw_projects = value.get("project_ids", [])
    raw_bullets = value.get("bullet_evidence_ids", [])
    validation = value.get("validation")
    quality = value.get("quality")
    decision_version = value.get("decision_version", 1)
    if not isinstance(raw_projects, list) or any(
        not isinstance(item, str) for item in raw_projects
    ):
        raise ValueError("stored resume version project_ids must be strings")
    if not isinstance(raw_bullets, list) or any(
        not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
        for ids in raw_bullets
    ):
        raise ValueError("stored resume version bullet provenance must be string lists")
    if not isinstance(validation, dict) or not isinstance(quality, dict):
        raise ValueError("stored resume version validation and quality must be objects")
    if not isinstance(decision_version, int) or isinstance(decision_version, bool):
        raise ValueError("stored resume version decision_version must be an integer")
    return ResumeUseRecord(
        id=identifier,
        application_id=application_id,
        generated_at=generated_at,
        used_at=raw_used_at,
        source_sha256=hashes[0],
        proposal_sha256=hashes[1],
        pdf_sha256=hashes[2],
        decision_sha256=hashes[3],
        project_ids=tuple(raw_projects),
        bullet_evidence_ids=tuple(tuple(ids) for ids in raw_bullets),
        validation=dict(validation),
        quality=dict(quality),
        decision_version=decision_version,
    )


def record_validated_resume_version(
    *,
    manifest_path: Path,
    application_id: str,
    source_path: Path,
    proposal_path: Path,
    pdf_path: Path,
    decision_path: Path,
    validation: dict[str, object],
) -> ResumeUseRecord:
    """Append one idempotent validated version, deriving claims from its decision artifact."""
    package_root = manifest_path.parent.resolve()
    if manifest_path.parent.is_symlink():
        raise ValueError("package directory must not be a symlink")
    for path in (source_path, proposal_path, pdf_path, decision_path):
        resolved = path.resolve()
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("resume version artifacts must not use symlinks")
        if not resolved.is_file() or not resolved.is_relative_to(package_root):
            raise ValueError("resume version artifacts must be files inside the package")
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("resume decision artifact must be readable JSON") from error
    if not isinstance(decision, dict):
        raise ValueError("resume decision artifact must contain an object")
    quality = decision.get("master_parity")
    catalogue = decision.get("catalogue")
    raw_bullet_ids = decision.get("selected_bullet_evidence_ids")
    if not isinstance(quality, dict) or quality.get("passed") is not True:
        raise ValueError("resume decision artifact must record passing master parity")
    if not isinstance(catalogue, dict) or not isinstance(catalogue.get("selected"), list):
        raise ValueError("resume decision artifact must record selected projects")
    project_ids = tuple(
        item["project_id"]
        for item in catalogue["selected"]
        if isinstance(item, dict) and isinstance(item.get("project_id"), str)
    )
    if not isinstance(raw_bullet_ids, list) or any(
        not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)
        for ids in raw_bullet_ids
    ):
        raise ValueError("resume decision artifact must record bullet evidence provenance")
    bullet_evidence_ids = tuple(tuple(ids) for ids in raw_bullet_ids)
    record = ResumeUseRecord.create(
        application_id=application_id,
        source_sha256=_sha256_path(source_path),
        proposal_sha256=_sha256_path(proposal_path),
        pdf_sha256=_sha256_path(pdf_path),
        decision_sha256=_sha256_path(decision_path),
        project_ids=project_ids,
        bullet_evidence_ids=bullet_evidence_ids,
        validation=validation,
        quality=dict(quality),
    )
    with _RESUME_MANIFEST_LOCK, _resume_manifest_transaction(manifest_path):
        manifest = _load_private_manifest(manifest_path)
        raw_versions = manifest.get("resume_versions", [])
        if not isinstance(raw_versions, list):
            raise ValueError("package resume_versions must be a list")
        versions = [_resume_use_record_from_dict(item) for item in raw_versions]
        existing = next((item for item in versions if item.id == record.id), None)
        if existing is not None:
            if manifest.get("generated_resume_version_id") != existing.id:
                manifest["generated_resume_version_id"] = existing.id
                manifest.setdefault("used_resume_version_id", None)
                _write_private_manifest(manifest_path, manifest)
            return existing
        versions.append(record)
        manifest["resume_versions"] = [item.as_dict() for item in versions]
        manifest["generated_resume_version_id"] = record.id
        manifest.setdefault("used_resume_version_id", None)
        _write_private_manifest(manifest_path, manifest)
        return record


def mark_resume_version_used(
    *,
    manifest_path: Path,
    application_id: str,
    version_id: str,
    used_at: datetime | None = None,
) -> ResumeUseRecord:
    """Record explicit application use without erasing immutable generation history."""
    with _RESUME_MANIFEST_LOCK, _resume_manifest_transaction(manifest_path):
        manifest = _load_private_manifest(manifest_path)
        raw_versions = manifest.get("resume_versions", [])
        if not isinstance(raw_versions, list):
            raise ValueError("package resume_versions must be a list")
        versions = [_resume_use_record_from_dict(item) for item in raw_versions]
        matched = next((item for item in versions if item.id == version_id), None)
        if matched is None:
            raise ValueError("resume version does not exist in this package")
        if matched.application_id != application_id:
            raise ValueError("resume version belongs to a different application")
        if matched.validation.get("returncode") != 0 or matched.quality.get("passed") is not True:
            raise ValueError("only a successfully validated resume version can be marked used")
        current_id = manifest.get("used_resume_version_id")
        if matched.used_at is not None and current_id == version_id:
            return matched
        timestamp = (used_at or datetime.now(UTC)).isoformat()
        updated = replace(matched, used_at=matched.used_at or timestamp)
        manifest["resume_versions"] = [
            (updated if item.id == version_id else item).as_dict() for item in versions
        ]
        manifest["used_resume_version_id"] = version_id
        _write_private_manifest(manifest_path, manifest)
        return updated


def find_resume_version_manifest(
    *, output_root: Path, application_id: str, version_id: str
) -> Path:
    """Resolve one generated version inside the configured package root without path input."""
    if not application_id.strip() or not version_id.startswith("resume_"):
        raise ValueError("application_id and resume version ID are required")
    root = output_root.expanduser().resolve()
    matches: list[Path] = []
    if root.is_dir() and not root.is_symlink():
        for manifest_path in root.glob("*/*/package.json"):
            if manifest_path.is_symlink() or not manifest_path.is_file():
                continue
            manifest_path.resolve().relative_to(root)
            manifest = _load_private_manifest(manifest_path)
            raw_versions = manifest.get("resume_versions", [])
            if not isinstance(raw_versions, list):
                continue
            versions = [_resume_use_record_from_dict(item) for item in raw_versions]
            if any(
                item.id == version_id and item.application_id == application_id for item in versions
            ):
                matches.append(manifest_path)
    if not matches:
        raise ValueError("resume version does not exist for this application")
    if len(matches) > 1:
        raise ValueError("resume version is ambiguous across local packages")
    return matches[0]


_DISALLOWED_LATEX = ("\\input", "\\include", "\\write18", "\\immediate\\write")


def _manual_claims_from_latex(latex_content: str) -> tuple[str, ...]:
    """Extract factual content so non-bullet snippets cannot bypass evidence checks."""
    bullets = resume_item_texts(latex_content)
    if bullets:
        return bullets
    rendered = latex_to_text(latex_content)
    return (rendered,) if rendered else ()


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
    manual_claims = _manual_claims_from_latex(latex_content)
    if not manual_claims:
        raise ValueError("latex_content must contain reviewable resume content")
    support_report = manual_claim_support_report(manual_claims, evidence)
    unsupported = [item for item in support_report if item["passed"] is not True]
    if unsupported:
        raise ValueError(
            "every manually authored resume bullet must be supported by one supplied approved "
            "evidence record; unsupported bullet: "
            f"{unsupported[0]['claim']}"
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
                "claim_evidence_support": list(support_report),
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
    manual_claims = _manual_claims_from_latex(latex_snippet)
    if not manual_claims:
        raise ValueError("latex_snippet must contain reviewable resume content")
    support_report = manual_claim_support_report(manual_claims, evidence)
    unsupported = [item for item in support_report if item["passed"] is not True]
    if unsupported:
        raise ValueError(
            "every manually authored resume claim must be supported by one supplied approved "
            "evidence record; unsupported claim: "
            f"{unsupported[0]['claim']}"
        )

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
                "claim_evidence_support": list(support_report),
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
    command: tuple[str, ...]
    if latexmk_executable.name.casefold() == "tectonic":
        command = (
            str(latexmk_executable),
            "--untrusted",
            "--keep-logs",
            proposal_path.name,
        )
    else:
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


def validate_single_line_resume_items(
    proposal_path: Path,
    *,
    latexmk: Path = Path("latexmk"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ResumeItemLayoutValidation:
    """Compile an instrumented sibling and measure bullets against their exact line width."""
    if proposal_path.suffix.lower() != ".tex" or not proposal_path.is_file():
        raise ValueError("proposal_path must point to an existing .tex proposal")
    source = proposal_path.read_text(encoding="utf-8")
    document_marker = r"\begin{document}"
    document_start = source.find(document_marker)
    if document_start < 0:
        raise ValueError("resume proposal must contain \\begin{document}")
    document = source[document_start:]
    custom_item_count = len(_command_arguments(document, "resumeItem"))
    item_count = len(resume_item_texts(document))
    has_standard_items = item_count > custom_item_count
    instrumented = (
        source
        if custom_item_count == 0
        else source.replace(
            document_marker,
            document_marker + _SINGLE_LINE_LAYOUT_INSTRUMENT,
            1,
        )
    )
    latexmk_executable = resolve_latexmk_executable(latexmk)
    environment = os.environ.copy()
    executable_directory = str(latexmk_executable.parent)
    path_entries = environment.get("PATH", "").split(os.pathsep)
    if executable_directory not in path_entries:
        environment["PATH"] = os.pathsep.join(
            [executable_directory, *[entry for entry in path_entries if entry]]
        )

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=proposal_path.parent,
            prefix="erga-layout-",
            suffix=".tex",
            delete=False,
        ) as temporary:
            temporary.write(instrumented)
            temporary_path = Path(temporary.name)
        command = (
            (str(latexmk_executable), "--untrusted", "--keep-logs", temporary_path.name)
            if latexmk_executable.name.casefold() == "tectonic"
            else (
                str(latexmk_executable),
                "-pdf",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                temporary_path.name,
            )
        )
        completed = runner(
            command,
            cwd=proposal_path.parent,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=120,
        )
        log_path = temporary_path.with_suffix(".log")
        log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
        observed: dict[int, str] = {}
        for match in _LAYOUT_MARKER.finditer(f"{completed.stdout}\n{log}"):
            observed[int(match.group("index"))] = match.group("state")
        if completed.returncode == 0 and set(observed) != set(range(1, custom_item_count + 1)):
            raise ValueError(
                "single-line layout validation did not observe every rendered resume bullet"
            )
        pdf_items = _pdf_resume_item_lines(temporary_path.with_suffix(".pdf"))
        if (
            completed.returncode == 0
            and has_standard_items
            and (pdf_items is None or len(pdf_items) != item_count)
        ):
            raise ValueError(
                "single-line layout validation could not observe every standard LaTeX bullet"
            )
        wrapped_items = (
            tuple(index for index, lines in enumerate(pdf_items) if len(lines) > 1)
            if has_standard_items and pdf_items is not None
            else tuple(index - 1 for index, state in sorted(observed.items()) if state != "FIT")
        )
        rendered_orphans = (
            tuple(
                index
                for index, lines in enumerate(pdf_items)
                if len(lines) > 1 and len(lines[-1].split()) <= 2
            )
            if pdf_items is not None and len(pdf_items) == item_count
            else tuple(index - 1 for index, state in sorted(observed.items()) if state == "ORPHAN")
        )
        return ResumeItemLayoutValidation(
            command=command,
            returncode=completed.returncode,
            item_count=item_count,
            wrapped_item_indices=wrapped_items,
            stdout=completed.stdout,
            stderr=completed.stderr,
            orphan_item_indices=rendered_orphans,
        )
    finally:
        if temporary_path is not None:
            for generated in temporary_path.parent.glob(f"{temporary_path.stem}.*"):
                if generated.is_file() or generated.is_symlink():
                    generated.unlink(missing_ok=True)
