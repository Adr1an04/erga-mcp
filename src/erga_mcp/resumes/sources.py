"""User-selected master resume and optional style-reference ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

from defusedxml import ElementTree as ET
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from erga_mcp.models import Evidence
from erga_mcp.operations.private_files import restrict_private_directory, restrict_private_file
from erga_mcp.store import ErgaStore

SUPPORTED_RESUME_SUFFIXES = frozenset({".docx", ".pdf", ".tex"})
ResumeSourceRole = Literal["master", "style"]
_MAX_SOURCE_BYTES = 25 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 500_000
_MAX_DOCX_DOCUMENT_BYTES = 8 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_MAX_SPACING_ALIGNMENT_CHARACTERS = 25_000
_WORD_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DEFAULT_STYLE: dict[str, object] = {
    "page_size": "US Letter",
    "max_pages": 1,
    "layout": "single-column ATS-friendly",
    "section_order": ["Education", "Experience", "Projects", "Technical Skills"],
    "density": "compact but readable",
}


@dataclass(frozen=True)
class ResumeSource:
    path: Path
    format: str
    sha256: str
    page_count: int | None
    text: str


def _restore_pdf_layout_spacing(layout_text: str, reading_order_text: str) -> str:
    """Restore word boundaries lost where PDF font spans touch.

    ``pypdf``'s layout mode is valuable for headings, columns, and bullets, but some
    résumé generators split bold metrics and surrounding words into adjacent glyph
    runs without a space.  The ordinary reading-order extractor usually retains
    those boundaries.  Transfer only its whitespace boundaries between characters
    that align exactly, leaving every existing layout newline and column gap intact.
    """
    if not layout_text or not reading_order_text or layout_text == reading_order_text:
        return layout_text
    layout_characters = [
        (index, character) for index, character in enumerate(layout_text) if not character.isspace()
    ]
    reading_characters = [
        (index, character)
        for index, character in enumerate(reading_order_text)
        if not character.isspace()
    ]
    if len(layout_characters) < 2 or len(reading_characters) < 2:
        return layout_text
    if max(len(layout_characters), len(reading_characters)) > _MAX_SPACING_ALIGNMENT_CHARACTERS:
        return layout_text
    matcher = SequenceMatcher(
        None,
        "".join(character for _, character in layout_characters),
        "".join(character for _, character in reading_characters),
        autojunk=True,
    )
    aligned: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            aligned[block.a + offset] = block.b + offset

    insertions: set[int] = set()
    for left in range(len(layout_characters) - 1):
        left_offset, _ = layout_characters[left]
        right_offset, _ = layout_characters[left + 1]
        if right_offset != left_offset + 1:
            continue
        reading_left = aligned.get(left)
        reading_right = aligned.get(left + 1)
        if reading_left is None or reading_right != reading_left + 1:
            continue
        reading_left_offset = reading_characters[reading_left][0]
        reading_right_offset = reading_characters[reading_right][0]
        gap = reading_order_text[reading_left_offset + 1 : reading_right_offset]
        if gap and gap.isspace():
            insertions.add(right_offset)

    # Refuse pathological reading-order output that separates nearly every glyph.
    if len(insertions) > max(8, len(layout_characters) // 4):
        return layout_text
    return "".join(
        (" " if index in insertions else "") + character
        for index, character in enumerate(layout_text)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_expected_sha256(path: Path) -> str | None:
    candidate = path.parent.name.casefold()
    if (
        path.parent.parent.name != "resume-sources"
        or len(candidate) != 64
        or any(character not in "0123456789abcdef" for character in candidate)
    ):
        return None
    return candidate


def _validate_source(path: Path) -> Path:
    source = path.expanduser().absolute()
    if not source.is_file():
        raise FileNotFoundError(f"Resume source does not exist: {source}")
    if source.suffix.casefold() not in SUPPORTED_RESUME_SUFFIXES:
        raise ValueError("resume source must be a PDF, DOCX, or LaTeX (.tex) file")
    if source.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("resume source exceeds the 25 MB local import limit")
    return source


def _pdf_text(path: Path) -> tuple[str, int]:
    try:
        reader = PdfReader(path)
    except PdfReadError as error:
        raise ValueError(f"could not read resume PDF: {path}") from error
    if reader.is_encrypted:
        raise ValueError("encrypted resume PDFs are not supported")
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            # Layout extraction preserves visual line boundaries. Resume PDFs frequently encode
            # an entire project or job as one plain extraction line, which prevents deterministic
            # bullet selection and page packing.
            extracted = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            # Lightweight test doubles and older pypdf-compatible readers may expose only the
            # no-argument API; keeping this fallback does not weaken production extraction.
            extracted = page.extract_text() or ""
        else:
            try:
                reading_order = page.extract_text() or ""
            except TypeError:
                reading_order = extracted
            extracted = _restore_pdf_layout_spacing(extracted, reading_order)
        pages.append(f"[Page {index}]\n{extracted.rstrip()}")
    return "\n\n".join(pages).strip(), len(reader.pages)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document_info = archive.getinfo("word/document.xml")
            if document_info.file_size > _MAX_DOCX_DOCUMENT_BYTES:
                raise ValueError("resume DOCX document.xml exceeds the 8 MB decompression limit")
            document = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError(f"could not read resume DOCX: {path}") from error
    root = ET.fromstring(document)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NAMESPACE}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_WORD_NAMESPACE}t")).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def load_resume_source(path: Path) -> ResumeSource:
    """Extract bounded text and provenance from one explicitly selected local resume."""
    source = _validate_source(path)
    source_sha256 = _sha256_file(source)
    expected_sha256 = _managed_expected_sha256(source)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(f"managed resume snapshot failed integrity verification: {source}")
    suffix = source.suffix.casefold()
    if suffix == ".pdf":
        text, page_count = _pdf_text(source)
    elif suffix == ".docx":
        text = _docx_text(source)
        page_count = None
    else:
        text = source.read_text(encoding="utf-8")
        page_count = None
    text = text.strip()
    if not text:
        raise ValueError(
            "resume source contains no extractable text; export a text-based PDF or DOCX first"
        )
    if len(text) > _MAX_EXTRACTED_CHARS:
        raise ValueError("resume source exceeds the 500,000-character extraction limit")
    if _sha256_file(source) != source_sha256:
        raise ValueError("resume source changed while Erga was extracting its content")
    return ResumeSource(
        path=source,
        format=suffix.removeprefix("."),
        sha256=source_sha256,
        page_count=page_count,
        text=text,
    )


def _write_snapshot_metadata(
    *,
    metadata_path: Path,
    source: ResumeSource,
    managed_path: Path,
    role: ResumeSourceRole,
) -> None:
    original_paths = [str(source.path)]
    if metadata_path.exists():
        try:
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise ValueError(f"managed resume metadata is invalid: {metadata_path}") from error
        if (
            not isinstance(existing, dict)
            or existing.get("role") != role
            or existing.get("sha256") != source.sha256
        ):
            raise ValueError(
                f"managed resume metadata does not match its snapshot: {metadata_path}"
            )
        prior_paths = existing.get("original_paths", [])
        if isinstance(prior_paths, list):
            original_paths = [
                *[value for value in prior_paths if isinstance(value, str)],
                *original_paths,
            ]

    payload = {
        "format": source.format,
        "managed_path": str(managed_path),
        "original_paths": list(dict.fromkeys(original_paths)),
        "role": role,
        "sha256": source.sha256,
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=metadata_path.parent,
        prefix=f".{role}-metadata-",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        restrict_private_file(temporary_path)
        temporary_path.replace(metadata_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def snapshot_resume_source(
    source: ResumeSource,
    *,
    data_dir: Path,
    role: ResumeSourceRole,
) -> ResumeSource:
    """Create or reuse an atomic, hash-verified private snapshot of one resume source."""
    if _sha256_file(source.path) != source.sha256:
        raise ValueError("resume source changed before Erga could create its private copy")
    snapshot_dir = data_dir.expanduser().absolute() / "resume-sources" / source.sha256
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(snapshot_dir)
    managed_path = snapshot_dir / f"{role}.{source.path.suffix.casefold().removeprefix('.')}"
    metadata_path = snapshot_dir / f"{role}.json"

    if managed_path.exists():
        if _sha256_file(managed_path) != source.sha256:
            raise ValueError(
                f"managed resume snapshot failed integrity verification: {managed_path}"
            )
    else:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=snapshot_dir,
            prefix=f".{role}-source-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            try:
                with source.path.open("rb") as original:
                    while chunk := original.read(_COPY_CHUNK_BYTES):
                        temporary.write(chunk)
                temporary.flush()
                os.fsync(temporary.fileno())
            except Exception:
                temporary_path.unlink(missing_ok=True)
                raise
        try:
            if _sha256_file(temporary_path) != source.sha256:
                raise ValueError("resume source changed while Erga was creating its private copy")
            restrict_private_file(temporary_path)
            temporary_path.replace(managed_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    restrict_private_file(managed_path)
    _write_snapshot_metadata(
        metadata_path=metadata_path,
        source=source,
        managed_path=managed_path,
        role=role,
    )
    return replace(source, path=managed_path)


def master_source_ref(source: ResumeSource, *, source_name: str | None = None) -> str:
    return f"master-resume:{source.sha256}:{source_name or source.path.name}"


def import_master_resume(
    store: ErgaStore,
    source: ResumeSource,
    *,
    source_name: str | None = None,
) -> Evidence:
    """Make the selected master the sole active approved master source."""
    source_ref = master_source_ref(source, source_name=source_name)
    return store.set_active_master_resume_evidence(source_ref=source_ref, text=source.text)


def _style_profile(source: ResumeSource) -> dict[str, object]:
    lines = [line.strip() for line in source.text.splitlines() if line.strip()]
    section_names = (
        "Education",
        "Experience",
        "Projects",
        "Research",
        "Publications",
        "Leadership",
        "Activities",
        "Technical Skills",
        "Skills",
    )
    section_order = [
        section
        for section in section_names
        if any(line.casefold() == section.casefold() for line in lines)
    ]
    return {
        "format": source.format,
        "sha256": source.sha256,
        "page_count": source.page_count,
        "style_only": True,
        "may_introduce_claims": False,
        "raw_text_exposed": False,
        "observed_section_order": section_order,
        "line_count": len(lines),
        "character_count": len(source.text),
    }


def resume_source_context(
    *,
    master_path: Path,
    reference_path: Path | None,
    template_path: Path | None = None,
) -> dict[str, object]:
    """Return factual master text and non-factual style metadata for an MCP host."""
    master = load_resume_source(master_path)
    reference = load_resume_source(reference_path) if reference_path is not None else None
    preferences = dict(_DEFAULT_STYLE)
    preferences["source"] = "user-reference" if reference is not None else "erga-default"
    preferences["style_override_confirmed"] = reference is not None
    if reference is not None:
        preferences["reference_metadata"] = [
            "page count",
            "section order",
            "content density",
            "typography, rules, spacing, and margins",
            "bullets per entry",
        ]
        preferences["rendered_layout_control"] = "editable-latex-template"
        preferences["not_automatically_transformed"] = []
        automatically_applied = ["section presence", "section order"]
        if reference.page_count:
            preferences["max_pages"] = reference.page_count
            automatically_applied.insert(0, "maximum page count")
        preferences["automatically_applied"] = automatically_applied
    layout_profile: dict[str, object] | None = None
    style_layout_profile: dict[str, object] | None = None
    visual_style_profile: dict[str, object] | None = None
    template_metadata: dict[str, object] | None = None
    if template_path is not None:
        metadata_path = template_path.with_name("template.json")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = None
        if isinstance(metadata, dict):
            template_metadata = metadata
        if isinstance(metadata, dict) and isinstance(metadata.get("layout_profile"), dict):
            layout_profile = metadata["layout_profile"]
            preferences["section_order"] = layout_profile.get("section_order", [])
            preferences["layout_profile_source"] = "inferred-from-approved-template"
            profile_applied = preferences.setdefault("automatically_applied", [])
            if isinstance(profile_applied, list):
                profile_applied.extend(
                    value
                    for value in (
                        "section presence",
                        "section order",
                        "project count",
                        "bullets per entry",
                    )
                    if value not in profile_applied
                )
        if isinstance(metadata, dict) and isinstance(metadata.get("style_layout_profile"), dict):
            style_layout_profile = metadata["style_layout_profile"]
        if isinstance(metadata, dict) and isinstance(metadata.get("visual_style_profile"), dict):
            visual_style_profile = metadata["visual_style_profile"]
            profile_applied = preferences.setdefault("automatically_applied", [])
            if isinstance(profile_applied, list):
                profile_applied.extend(
                    value
                    for value in (
                        "content density",
                        "typography, rules, spacing, and margins",
                    )
                    if value not in profile_applied
                )
    direct_master_match = False
    if template_path is not None and master.format == "tex":
        try:
            direct_master_match = template_path.read_text(encoding="utf-8").strip() == master.text
        except OSError:
            direct_master_match = False
    exact_master_latex = bool(
        master.format == "tex"
        and (
            direct_master_match
            or (
                template_metadata is not None
                and template_metadata.get("master_latex_preserved") is True
            )
        )
    )
    template_fidelity = {
        "mode": "exact-master-latex" if exact_master_latex else "reconstructed",
        "preserves_master_layout": exact_master_latex,
        "preserves_source_hyperlinks": exact_master_latex,
        "reason": (
            "The approved LaTeX master is the visual template; the rendered reference is "
            "used only for visual validation."
            if exact_master_latex and reference is not None
            else "The approved LaTeX master is the visual template."
            if exact_master_latex
            else "PDF/DOCX sources provide facts, not recoverable LaTeX structure."
            if master.format != "tex"
            else "The configured template is not verified as the approved LaTeX master."
        ),
    }
    preferences["template_fidelity"] = template_fidelity
    return {
        "master": {
            "format": master.format,
            "sha256": master.sha256,
            "page_count": master.page_count,
            "text": master.text,
            "user_approved_source": True,
        },
        "style_reference": _style_profile(reference) if reference is not None else None,
        "layout_profile": layout_profile,
        "style_layout_profile": style_layout_profile,
        "visual_style_profile": visual_style_profile,
        "template_fidelity": template_fidelity,
        "preferences": preferences,
    }
