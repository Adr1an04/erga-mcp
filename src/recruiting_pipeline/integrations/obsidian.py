from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvidenceCandidate:
    source_ref: str
    text: str


def _inside_vault(vault: Path, note_path: Path) -> Path:
    resolved_vault = vault.expanduser().resolve()
    candidate = note_path if note_path.is_absolute() else resolved_vault / note_path
    resolved_note = candidate.expanduser().resolve()
    try:
        resolved_note.relative_to(resolved_vault)
    except ValueError as error:
        raise ValueError("note path must be inside the configured vault") from error
    if resolved_note.suffix.lower() != ".md":
        raise ValueError("only Markdown notes may be imported")
    return resolved_note


def import_markdown_evidence(vault: Path, note_path: Path) -> list[EvidenceCandidate]:
    """Read heading-scoped evidence from one configured Markdown note without modifying it."""
    resolved_vault = vault.expanduser().resolve()
    resolved_note = _inside_vault(resolved_vault, note_path)
    relative_note = resolved_note.relative_to(resolved_vault).as_posix()
    text = resolved_note.read_text(encoding="utf-8")

    candidates: list[EvidenceCandidate] = []
    heading: str | None = None
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                body = "\n".join(lines).strip()
                if body:
                    candidates.append(
                        EvidenceCandidate(source_ref=f"{relative_note}#{heading}", text=body)
                    )
            heading = line[3:].strip()
            lines = []
        elif line.startswith("#"):
            if heading is not None:
                body = "\n".join(lines).strip()
                if body:
                    candidates.append(
                        EvidenceCandidate(source_ref=f"{relative_note}#{heading}", text=body)
                    )
            heading = None
            lines = []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        body = "\n".join(lines).strip()
        if body:
            candidates.append(EvidenceCandidate(source_ref=f"{relative_note}#{heading}", text=body))
    return candidates
