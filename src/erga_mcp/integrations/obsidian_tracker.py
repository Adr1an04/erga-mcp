from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from ..models import MailEvent

_TABLE_HEADER = (
    "| Company | Role | Location / work mode | Source | Status | Applied | "
    "Next action | Contact / link |"
)
_TABLE_DIVIDER = "| --- | --- | --- | --- | --- | --- | --- | --- |"
_MANAGED_START = "<!-- erga-mcp:start -->"
_MANAGED_END = "<!-- erga-mcp:end -->"
_EXPECTED_TABLE_COLUMNS = (
    "company",
    "role",
    "location / work mode",
    "source",
    "status",
    "applied",
    "next action",
    "contact / link",
)
_ACTIVE_CYCLE_PATTERN = re.compile(r"^(Fall|Spring)\s+(\d{4})$", re.IGNORECASE)
_ACKNOWLEDGEMENT_COMPANY_PATTERN = re.compile(
    r"\b(?:thank\s+you|thanks)\s+for\s+(?:your\s+)?"
    r"(?:(?:applying|application)\s+(?:to|at)|interest\s+in)\s+(.+?)(?:[!.,:]|$)",
    re.IGNORECASE,
)


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in " -_&()'" else " " for char in value)
    cleaned = " ".join(cleaned.split())
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("company, role, and cycle must contain a safe display name")
    return cleaned


def _table_cell(value: str | None) -> str:
    return " ".join((value or "").split()).replace("|", r"\|")


def _table_cells(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def _company_matches_acknowledgement(company: str, event: MailEvent) -> bool:
    tokens = re.findall(r"[a-z0-9]+", company.casefold())
    if not tokens:
        return False
    content = f"{event.sender}\n{event.subject}".casefold()
    return all(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", content) for token in tokens
    )


def reconcile_confirmed_application_tracker_rows(
    *, tracker_dir: Path, events: Sequence[MailEvent]
) -> int:
    """Mark researching rows as applied only for an exactly matched acknowledgement."""
    acknowledgements = sorted(
        (event for event in events if event.kind == "application.acknowledgement"),
        key=lambda event: event.received_at,
    )
    if not acknowledgements:
        return 0

    updates = 0
    for tracker_path in sorted(tracker_dir.expanduser().resolve().glob("*.md")):
        text = tracker_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        divider_line = _application_table_divider_line(lines)
        if divider_line is None:
            continue
        changed = False
        for index in range(divider_line + 1, len(lines)):
            cells = _table_cells(lines[index])
            if len(cells) != len(_EXPECTED_TABLE_COLUMNS):
                continue
            if cells[4].casefold() not in {"researching", "draft"}:
                continue
            match = next(
                (
                    event
                    for event in acknowledgements
                    if _company_matches_acknowledgement(cells[0], event)
                ),
                None,
            )
            if match is None:
                continue
            updated_cells = list(cells)
            updated_cells[4] = "Applied"
            updated_cells[5] = match.received_at.date().isoformat()
            updated_cells[6] = "Await acknowledgement or recruiting update."
            lines[index] = "| " + " | ".join(_table_cell(cell) for cell in updated_cells) + " |"
            updates += 1
            changed = True
        if changed:
            tracker_path.write_text(
                "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
            )
    return updates


def _active_cycle_for_received_at(
    received_at_year: int, received_at_month: int, active_cycles: Sequence[str]
) -> str | None:
    season = "Fall" if received_at_month >= 7 else "Spring"
    candidate = f"{season} {received_at_year}"
    return next(
        (cycle for cycle in active_cycles if cycle.casefold() == candidate.casefold()), None
    )


def _company_from_acknowledgement(event: MailEvent) -> str | None:
    match = _ACKNOWLEDGEMENT_COMPANY_PATTERN.search(event.subject)
    if match is None:
        return None
    company = " ".join(match.group(1).split())
    try:
        return _safe_name(company)
    except ValueError:
        return None


def _application_table_end(lines: list[str], divider_line: int) -> int:
    end = divider_line + 1
    while end < len(lines) and _table_cells(lines[end]):
        end += 1
    return end


def import_confirmed_application_tracker_rows(
    *, tracker_dir: Path, active_cycles: Sequence[str], events: Sequence[MailEvent]
) -> int:
    """Add explicit acknowledgement-only rows for configured current recruiting cycles."""
    active_cycles = tuple(" ".join(cycle.split()) for cycle in active_cycles if cycle.strip())
    if not active_cycles:
        return 0
    if any(_ACTIVE_CYCLE_PATTERN.fullmatch(cycle) is None for cycle in active_cycles):
        raise ValueError("active tracker cycles must be Fall YYYY or Spring YYYY")

    tracker_dir = tracker_dir.expanduser().resolve()
    existing_companies: set[tuple[str, str]] = set()
    for tracker_path in tracker_dir.glob("*.md"):
        text = tracker_path.read_text(encoding="utf-8")
        tracker_cycle = tracker_path.stem.removesuffix(" Application Tracker").removesuffix(
            " Applications"
        )
        divider_line = _application_table_divider_line(text.splitlines())
        if divider_line is None:
            continue
        for line in text.splitlines()[divider_line + 1 :]:
            cells = _table_cells(line)
            if len(cells) == len(_EXPECTED_TABLE_COLUMNS) and cells[0]:
                existing_companies.add((tracker_cycle.casefold(), cells[0].casefold()))

    created = 0
    for event in sorted(events, key=lambda item: item.received_at):
        if event.kind != "application.acknowledgement":
            continue
        cycle = _active_cycle_for_received_at(
            event.received_at.year, event.received_at.month, active_cycles
        )
        company = _company_from_acknowledgement(event)
        if cycle is None or company is None:
            continue
        key = (cycle.casefold(), company.casefold())
        if key in existing_companies:
            continue
        tracker_path = _tracker_path(tracker_dir, cycle)
        text = tracker_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        divider_line = _application_table_divider_line(lines)
        if divider_line is None:
            continue
        row = [
            company,
            "Application confirmed by email",
            "",
            "Email acknowledgement",
            "Applied",
            event.received_at.date().isoformat(),
            "Await recruiting update.",
            "",
        ]
        lines.insert(
            _application_table_end(lines, divider_line),
            "| " + " | ".join(_table_cell(cell) for cell in row) + " |",
        )
        tracker_path.write_text(
            "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
        )
        existing_companies.add(key)
        created += 1
    return created


def _application_table_divider_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines[:-1]):
        header = tuple(cell.casefold() for cell in _table_cells(line))
        if header != _EXPECTED_TABLE_COLUMNS:
            continue
        divider = _table_cells(lines[index + 1])
        if len(divider) != len(_EXPECTED_TABLE_COLUMNS):
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in divider):
            return index + 1
    return None


def _tracker_path(tracker_dir: Path, cycle: str) -> Path:
    candidates = (
        tracker_dir / f"{cycle} Application Tracker.md",
        tracker_dir / f"{cycle} Applications.md",
    )
    existing = [path for path in candidates if path.is_file()]
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        raise ValueError(f"multiple cycle trackers exist for {cycle}")
    target = candidates[0]
    if target.exists():
        raise ValueError(f"cycle tracker must be a regular file: {target.name}")
    target.write_text(
        f"# {cycle} Applications\n\n"
        "Local application tracking managed by Erga MCP. "
        "Rows remain reviewable and may be edited in Obsidian.\n\n"
        "## Application tracker\n\n"
        f"{_TABLE_HEADER}\n{_TABLE_DIVIDER}\n",
        encoding="utf-8",
    )
    return target


def _notes_dir(tracker_dir: Path, cycle: str, tracker_path: Path) -> Path:
    if tracker_path.stem.endswith("Application Tracker"):
        name = f"{cycle} Application Notes"
    else:
        name = f"{cycle} Applications"
    return tracker_dir / name


def _render_tracker_update(
    *,
    tracker_path: Path,
    company: str,
    role: str,
    location: str | None,
    job_url: str,
    note_name: str,
) -> str:
    text = tracker_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    divider_line = _application_table_divider_line(lines)
    if divider_line is None:
        raise ValueError(f"cycle tracker has no application table: {tracker_path.name}")
    marker = f"[[{note_name}]]"
    if marker in text:
        return text
    row = (
        f"| {_table_cell(company)} | {_table_cell(role)} | {_table_cell(location)} | "
        f"[Posting]({job_url}) | Researching |  | Review role requirements and decide "
        f"whether to apply. | {marker} |"
    )
    lines.insert(divider_line + 1, row)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _managed_note_block(
    *,
    tracker_stems: Sequence[str],
    job_url: str,
    package_dir: Path,
    location: str | None,
    compensation: str | None,
    resume_pdf: Path | None,
    research_path: Path | None,
    research_highlights: Sequence[str],
    research_responsibilities: Sequence[str],
    research_ambiguities: Sequence[str],
    application_constraints: Sequence[str],
    posting_cycles: Sequence[str],
) -> str:
    cycle_links = ", ".join(f"[[{stem}]]" for stem in tracker_stems)
    lines = [
        _MANAGED_START,
        f"- Filed in: {cycle_links}",
        "- Status: Researching",
    ]
    if posting_cycles:
        lines.append(f"- Posting cycle(s): {', '.join(posting_cycles)}")
    if location:
        lines.append(f"- Location / work mode: {location}")
    if compensation:
        lines.append(f"- Compensation: {compensation}")
    lines.extend(
        [
            f"- Job URL: {job_url}",
            f"- Package: `{package_dir.expanduser().resolve()}`",
        ]
    )
    if resume_pdf is not None:
        lines.append(f"- Resume PDF: `{resume_pdf.expanduser().resolve()}`")
    if research_path is not None:
        lines.append(f"- Role research: `{research_path.expanduser().resolve()}`")
    lines.extend(
        [
            "- Next action: Review the tailored résumé, role research, and "
            "application constraints.",
            "",
            "## Resume / portfolio emphasis",
            "",
            "Generated from approved career evidence; see the package claim report.",
            "",
            "## Role research",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in research_highlights)
    if not research_highlights:
        lines.append("- Review the preserved official job description in the local package.")
    lines.extend(["", "## Responsibilities", ""])
    lines.extend(f"- {item}" for item in research_responsibilities)
    if not research_responsibilities:
        lines.append("- No distinct responsibilities section was extracted.")
    lines.extend(["", "## Ambiguities to verify", ""])
    lines.extend(f"- {item}" for item in research_ambiguities)
    if not research_ambiguities:
        lines.append("- No internal contradiction was detected in the captured posting.")
    lines.extend(["", "## Application constraints", ""])
    lines.extend(f"- {item}" for item in application_constraints)
    if not application_constraints:
        lines.append("- No application-frequency or deadline constraint was found in the posting.")
    lines.append(_MANAGED_END)
    return "\n".join(lines)


def _upsert_managed_note(note_path: Path, *, title: str, managed_block: str) -> None:
    if not note_path.exists():
        note_path.write_text(f"# {title}\n\n{managed_block}\n", encoding="utf-8")
        return
    text = note_path.read_text(encoding="utf-8")
    start = text.find(_MANAGED_START)
    end = text.find(_MANAGED_END)
    if start >= 0 and end >= start:
        end += len(_MANAGED_END)
        rendered = text[:start] + managed_block + text[end:]
    else:
        rendered = text.rstrip() + "\n\n" + managed_block + "\n"
    if rendered != text:
        note_path.write_text(rendered, encoding="utf-8")


def write_job_tracker_note(
    *,
    tracker_dir: Path,
    cycle: str,
    company: str,
    role: str,
    job_url: str,
    package_dir: Path,
    resume_pdf: Path | None = None,
    additional_cycles: Sequence[str] = (),
    location: str | None = None,
    compensation: str | None = None,
    research_path: Path | None = None,
    research_highlights: Sequence[str] = (),
    research_responsibilities: Sequence[str] = (),
    research_ambiguities: Sequence[str] = (),
    application_constraints: Sequence[str] = (),
    posting_cycles: Sequence[str] = (),
) -> Path:
    """Upsert a detailed note and create/update every named local cycle tracker."""
    if not job_url.startswith(("https://", "http://")):
        raise ValueError("job URL must use HTTP(S)")
    safe_company, safe_role = _safe_name(company), _safe_name(role)
    cycles: list[str] = []
    for raw_cycle in (cycle, *additional_cycles):
        safe_cycle = _safe_name(raw_cycle)
        if safe_cycle not in cycles:
            cycles.append(safe_cycle)

    notes_root = tracker_dir.expanduser().resolve()
    notes_root.mkdir(parents=True, exist_ok=True)
    tracker_paths = [_tracker_path(notes_root, item) for item in cycles]
    note_name = f"{safe_company} — {safe_role}"
    rendered_trackers = [
        (
            path,
            _render_tracker_update(
                tracker_path=path,
                company=safe_company,
                role=safe_role,
                location=location,
                job_url=job_url,
                note_name=note_name,
            ),
        )
        for path in tracker_paths
    ]

    cycle_notes_dir = _notes_dir(notes_root, cycles[0], tracker_paths[0])
    cycle_notes_dir.mkdir(exist_ok=True)
    note_path = cycle_notes_dir / f"{note_name}.md"
    managed_block = _managed_note_block(
        tracker_stems=[path.stem for path in tracker_paths],
        job_url=job_url,
        package_dir=package_dir,
        location=location,
        compensation=compensation,
        resume_pdf=resume_pdf,
        research_path=research_path,
        research_highlights=research_highlights,
        research_responsibilities=research_responsibilities,
        research_ambiguities=research_ambiguities,
        application_constraints=application_constraints,
        posting_cycles=posting_cycles,
    )
    _upsert_managed_note(note_path, title=note_name, managed_block=managed_block)
    for tracker_path, rendered in rendered_trackers:
        if rendered != tracker_path.read_text(encoding="utf-8"):
            tracker_path.write_text(rendered, encoding="utf-8")
    return note_path
