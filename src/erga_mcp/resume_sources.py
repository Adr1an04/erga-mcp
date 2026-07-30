"""User-selected master resume and optional style-reference ingestion."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .models import Evidence
from .store import ErgaStore

SUPPORTED_RESUME_SUFFIXES = frozenset({".docx", ".pdf", ".tex"})
_MAX_SOURCE_BYTES = 25 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 500_000
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
    pages = [
        f"[Page {index}]\n{(page.extract_text() or '').strip()}"
        for index, page in enumerate(reader.pages, start=1)
    ]
    return "\n\n".join(pages).strip(), len(reader.pages)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
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
    return ResumeSource(
        path=source,
        format=suffix.removeprefix("."),
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        page_count=page_count,
        text=text,
    )


def master_source_ref(source: ResumeSource) -> str:
    return f"master-resume:{source.sha256}:{source.path.name}"


def import_master_resume(store: ErgaStore, source: ResumeSource) -> Evidence:
    """Register the user-selected master as approved evidence, idempotently."""
    source_ref = master_source_ref(source)
    existing = next(
        (
            evidence
            for evidence in store.list_evidence()
            if evidence.source_ref == source_ref and evidence.text == source.text
        ),
        None,
    )
    if existing is not None:
        return existing
    return store.add_evidence(source_ref=source_ref, text=source.text, approved=True)


def resume_source_context(
    *,
    master_path: Path,
    reference_path: Path | None,
) -> dict[str, object]:
    """Return factual master text and style-only reference metadata for a coding client."""
    master = load_resume_source(master_path)
    reference = load_resume_source(reference_path) if reference_path is not None else None
    preferences = dict(_DEFAULT_STYLE)
    preferences["source"] = "user-reference" if reference is not None else "erga-default"
    preferences["style_override_confirmed"] = reference is not None
    if reference is not None:
        preferences["adjust_from_reference"] = [
            "page count",
            "section order",
            "content density",
        ]
        if reference.page_count:
            preferences["max_pages"] = reference.page_count
    return {
        "master": {**asdict(master), "path": str(master.path), "user_approved_source": True},
        "style_reference": (
            {
                **asdict(reference),
                "path": str(reference.path),
                "style_only": True,
                "may_introduce_claims": False,
            }
            if reference is not None
            else None
        ),
        "preferences": preferences,
    }
