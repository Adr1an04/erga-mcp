from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .models import Evidence

_BODY_MARKER = "{{BODY}}"
_SUPPORTED_TEMPLATE_SUFFIXES = frozenset({".md", ".txt", ".tex"})


@dataclass(frozen=True)
class WritingStyleContext:
    """User-owned writing sample used for style reference, never as career evidence."""

    source_path: Path
    text: str
    sha256: str


@dataclass(frozen=True)
class CoverLetterProposal:
    """A local, reviewable cover-letter proposal that never changes its sources."""

    proposed_path: Path
    diff_path: Path
    provenance_path: Path


def _read_text(path: Path, label: str) -> str:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} must point to an existing file")
    try:
        return resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must be UTF-8 text") from error


def load_style_context(writing_sample_path: Path) -> WritingStyleContext:
    """Read an explicit local writing sample, including an Obsidian Markdown note."""
    source_path = writing_sample_path.expanduser().resolve()
    text = _read_text(source_path, "writing_sample_path").strip()
    if not text:
        raise ValueError("writing_sample_path must not be empty")
    return WritingStyleContext(
        source_path=source_path,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def create_cover_letter_proposal(
    *,
    template_path: Path,
    writing_sample_path: Path,
    output_dir: Path,
    body: str,
    evidence: list[Evidence],
) -> CoverLetterProposal:
    """Render a reviewed draft into a user-owned template without modifying source files.

    The writing sample is provenance for tone only. The proposal deliberately marks its
    content for review because style similarity does not make factual statements true.
    """
    template_path = template_path.expanduser().resolve()
    if template_path.suffix.casefold() not in _SUPPORTED_TEMPLATE_SUFFIXES:
        raise ValueError("cover letter templates must use .md, .txt, or .tex")
    template = _read_text(template_path, "template_path")
    if template.count(_BODY_MARKER) != 1:
        raise ValueError("cover letter template must contain exactly one {{BODY}} marker")
    if not body.strip():
        raise ValueError("cover letter body must not be empty")
    if any(not item.approved for item in evidence):
        raise ValueError("a cover letter proposal may only reference approved evidence")

    style = load_style_context(writing_sample_path)
    proposed = template.replace(_BODY_MARKER, body.strip())
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_path = output_dir / f"cover-letter-proposal{template_path.suffix.casefold()}"
    diff_path = output_dir / "cover-letter-proposal.diff"
    provenance_path = output_dir / "cover-letter-provenance.json"
    proposed_path.write_text(proposed, encoding="utf-8")
    diff_path.write_text(
        "".join(
            difflib.unified_diff(
                template.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=str(template_path),
                tofile=str(proposed_path),
            )
        ),
        encoding="utf-8",
    )
    provenance_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "content_review_required": True,
                "external_sync": "not performed",
                "source_modified": False,
                "template": {"source_path": str(template_path)},
                "writing_sample": {
                    "sha256": style.sha256,
                    "source_path": str(style.source_path),
                    "style_only": True,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return CoverLetterProposal(proposed_path, diff_path, provenance_path)
