from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from erga_mcp.job_urls import job_identity
from erga_mcp.models import Application, MailEvent

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
_ACTIVE_CYCLE_PATTERN = re.compile(r"^(Winter|Spring|Summer|Fall)\s+(20\d{2})$", re.IGNORECASE)
_ROLE_CYCLE_PATTERN = re.compile(r"\b(Winter|Spring|Summer|Fall)\s+(20\d{2})\b", re.IGNORECASE)
_REVERSED_ROLE_CYCLE_PATTERN = re.compile(
    r"\b(20\d{2})\s+(Winter|Spring|Summer|Fall)\b", re.IGNORECASE
)
_LOOSE_ROLE_CYCLE_PATTERN = re.compile(
    r"\b(Winter|Spring|Summer|Fall)\b"
    r"(?:\s+(?:internship|intern|co-?op|program|role|position|engineering|"
    r"undergraduate|graduate|campus|software)){0,5}\s*[-–—,:]?\s*(20\d{2})\b",
    re.IGNORECASE,
)
_ROLE_SEASON_PATTERN = re.compile(r"\b(Winter|Spring|Summer|Fall)\b", re.IGNORECASE)
_ROLE_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
_INTERNSHIP_ROLE_PATTERN = re.compile(r"\b(?:intern|internship|internships|co-?op)\b", re.I)
_SEASON_INFERENCE_CUTOFF_MONTH = {"winter": 2, "spring": 5, "summer": 6, "fall": 8}
_SOURCE_URL_PATTERN = re.compile(r"https?://[^)\s|]+", re.IGNORECASE)
_MANAGED_MAIL_SOURCE = "email acknowledgement"


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in " -_&()'" else " " for char in value)
    cleaned = " ".join(cleaned.split())
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("company, role, and cycle must contain a safe display name")
    return cleaned


def _safe_tracker_identity(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned or not any(character.isalnum() for character in cleaned):
        raise ValueError("tracker identity must contain a display name")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError("tracker identity contains control characters")
    return cleaned[:limit].rstrip()


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
    content = f"{event.sender}\n{event.subject}\n{event.company_hint}".casefold()
    return all(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", content) for token in tokens
    )


def _role_matches_acknowledgement(role: str, event: MailEvent) -> bool:
    if not event.role_hint:
        return True
    expected = set(re.findall(r"[a-z0-9]+", role.casefold()))
    observed = set(re.findall(r"[a-z0-9]+", event.role_hint.casefold()))
    stopwords = {"and", "co", "intern", "internship", "of", "the"}
    expected -= stopwords
    observed -= stopwords
    return bool(expected and observed and expected.intersection(observed))


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
                    and _role_matches_acknowledgement(cells[1], event)
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


_STATUS_LABELS = {
    "draft": ("Draft", "Prepare and submit application."),
    "applied": ("Applied", "Await acknowledgement or recruiting update."),
    "oa": ("OA", "Complete assessment and review its deadline."),
    "assessment": ("OA", "Complete assessment and review its deadline."),
    "interview": ("Interview", "Prepare for interview and confirm details."),
    "interview-2": ("Interview 2", "Prepare for the second interview and confirm details."),
    "interview-3": ("Interview 3", "Prepare for the third interview and confirm details."),
    "final-interview": ("Final Interview", "Prepare for the final interview."),
    "offer": ("Offer", "Review offer terms and deadline."),
    "accepted": ("Accepted", "Complete authorized onboarding steps."),
    "rejected": ("Rejected", "No action - application closed."),
    "withdrawn": ("Withdrawn", "No action - application withdrawn."),
}


def reconcile_application_status_tracker_rows(
    *, tracker_dir: Path, applications: Sequence[Application]
) -> int:
    """Reflect unambiguous canonical application statuses in existing tracker rows."""
    applications_by_company: dict[str, list[Application]] = {}
    applications_by_company_role: dict[tuple[str, str], list[Application]] = {}
    applications_by_identity: dict[str, list[Application]] = {}
    for application in applications:
        company = application.company.casefold()
        role = application.role.casefold()
        applications_by_company.setdefault(company, []).append(application)
        applications_by_company_role.setdefault((company, role), []).append(application)
        identity = job_identity(application.source_url)
        if identity:
            applications_by_identity.setdefault(identity, []).append(application)
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
            source_match = _SOURCE_URL_PATTERN.search(cells[3])
            source_identity = job_identity(source_match.group(0)) if source_match else ""
            if source_identity:
                matches = applications_by_identity.get(source_identity, [])
            else:
                matches = applications_by_company_role.get(
                    (cells[0].casefold(), cells[1].casefold()),
                    [],
                )
                if len(matches) != 1:
                    matches = applications_by_company.get(cells[0].casefold(), [])
            if len(matches) != 1:
                continue
            status = _STATUS_LABELS.get(matches[0].status)
            if status is None:
                continue
            label, next_action = status
            if cells[4] == label and cells[6] == next_action:
                continue
            updated = list(cells)
            updated[4] = label
            updated[6] = next_action
            lines[index] = "| " + " | ".join(_table_cell(cell) for cell in updated) + " |"
            updates += 1
            changed = True
        if changed:
            tracker_path.write_text(
                "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
            )
    return updates


def _fallback_cycle_for_received_at(
    received_at_year: int, received_at_month: int, active_cycles: Sequence[str]
) -> str | None:
    season = "Fall" if received_at_month >= 7 else "Spring"
    candidate = f"{season} {received_at_year}"
    return next(
        (cycle for cycle in active_cycles if cycle.casefold() == candidate.casefold()), None
    )


def _explicit_event_cycles(event: MailEvent) -> tuple[str, ...]:
    """Extract bounded recruiting terms from receipt identity, never arbitrary filenames."""
    text = f"{event.role_hint}\n{event.subject}"
    found: list[str] = []
    found_keys: set[str] = set()

    def add(season: str, year_text: str) -> None:
        year = int(year_text)
        if year < event.received_at.year - 1 or year > event.received_at.year + 2:
            return
        cycle = f"{season.title()} {year}"
        if cycle.casefold() not in found_keys:
            found.append(cycle)
            found_keys.add(cycle.casefold())

    for season, year in _ROLE_CYCLE_PATTERN.findall(text):
        add(season, year)
    for year, season in _REVERSED_ROLE_CYCLE_PATTERN.findall(text):
        add(season, year)
    for season, year in _LOOSE_ROLE_CYCLE_PATTERN.findall(text):
        add(season, year)
    if found:
        return tuple(found)

    seasons = {season.casefold() for season in _ROLE_SEASON_PATTERN.findall(event.role_hint)}
    if len(seasons) == 1:
        season = seasons.pop()
        inferred_year = event.received_at.year + int(
            event.received_at.month > _SEASON_INFERENCE_CUTOFF_MONTH[season]
        )
        add(season, str(inferred_year))
        return tuple(found)

    years = set(_ROLE_YEAR_PATTERN.findall(text))
    if len(years) == 1 and _INTERNSHIP_ROLE_PATTERN.search(event.role_hint):
        add("Summer", years.pop())
    return tuple(found)


def _existing_tracker_cycles(tracker_dir: Path) -> tuple[str, ...]:
    cycles: list[str] = []
    for path in sorted(tracker_dir.glob("*.md")):
        stem = path.stem
        for suffix in (" Application Tracker", " Applications"):
            if stem.endswith(suffix):
                cycle = stem.removesuffix(suffix)
                if _ACTIVE_CYCLE_PATTERN.fullmatch(cycle) and cycle not in cycles:
                    cycles.append(cycle)
                break
    return tuple(cycles)


def _existing_tracker_identity_cycles(
    tracker_dir: Path, cycles: Sequence[str]
) -> dict[tuple[str, str], tuple[str, ...]]:
    matches: dict[tuple[str, str], list[str]] = {}
    for cycle in cycles:
        candidates = (
            tracker_dir / f"{cycle} Application Tracker.md",
            tracker_dir / f"{cycle} Applications.md",
        )
        paths = [path for path in candidates if path.is_file()]
        if len(paths) != 1:
            continue
        lines = paths[0].read_text(encoding="utf-8").splitlines()
        divider_line = _application_table_divider_line(lines)
        if divider_line is None:
            continue
        table_end = _application_table_end(lines, divider_line)
        for line in lines[divider_line + 1 : table_end]:
            cells = _table_cells(line)
            if (
                len(cells) != len(_EXPECTED_TABLE_COLUMNS)
                or not cells[0]
                or not cells[1]
                or cells[3].casefold() == _MANAGED_MAIL_SOURCE
            ):
                continue
            key = (cells[0].casefold(), cells[1].casefold())
            if cycle not in matches.setdefault(key, []):
                matches[key].append(cycle)
    return {key: tuple(values) for key, values in matches.items()}


def _application_table_end(lines: list[str], divider_line: int) -> int:
    end = divider_line + 1
    while end < len(lines) and _table_cells(lines[end]):
        end += 1
    return end


def import_confirmed_application_tracker_rows(
    *, tracker_dir: Path, active_cycles: Sequence[str], events: Sequence[MailEvent]
) -> int:
    """Project complete acknowledgement identities into canonical managed tracker rows."""
    active_cycles = tuple(" ".join(cycle.split()) for cycle in active_cycles if cycle.strip())
    if any(_ACTIVE_CYCLE_PATTERN.fullmatch(cycle) is None for cycle in active_cycles):
        raise ValueError("active tracker cycles must be Winter, Spring, Summer, or Fall YYYY")

    tracker_dir = tracker_dir.expanduser().resolve()
    projection_cycles = list(active_cycles)
    projection_cycle_keys = {cycle.casefold() for cycle in projection_cycles}
    for cycle in _existing_tracker_cycles(tracker_dir):
        if cycle.casefold() not in projection_cycle_keys:
            projection_cycles.append(cycle)
            projection_cycle_keys.add(cycle.casefold())
    existing_identity_cycles = _existing_tracker_identity_cycles(tracker_dir, projection_cycles)
    desired_by_cycle: dict[str, dict[tuple[str, str], list[str]]] = {}
    for event in sorted(events, key=lambda item: item.received_at):
        if event.kind != "application.acknowledgement":
            continue
        if not event.company_hint or not event.role_hint:
            continue
        try:
            company = _safe_tracker_identity(event.company_hint, limit=100)
            role = _safe_tracker_identity(event.role_hint, limit=200)
        except ValueError:
            continue
        event_cycles = _explicit_event_cycles(event)
        if not event_cycles:
            existing_cycles = existing_identity_cycles.get(
                (company.casefold(), role.casefold()), ()
            )
            if len(existing_cycles) == 1:
                event_cycles = existing_cycles
        if not event_cycles:
            fallback = _fallback_cycle_for_received_at(
                event.received_at.year, event.received_at.month, projection_cycles
            )
            event_cycles = (fallback,) if fallback is not None else ()
        if not event_cycles:
            continue
        for cycle in event_cycles:
            if cycle.casefold() not in projection_cycle_keys:
                projection_cycles.append(cycle)
                projection_cycle_keys.add(cycle.casefold())
            desired_by_cycle.setdefault(cycle.casefold(), {}).setdefault(
                (company.casefold(), role.casefold()),
                [
                    company,
                    role,
                    "",
                    "Email acknowledgement",
                    "Applied",
                    event.received_at.date().isoformat(),
                    "Await recruiting update.",
                    "",
                ],
            )

    changes = 0
    for cycle in projection_cycles:
        candidates = (
            tracker_dir / f"{cycle} Application Tracker.md",
            tracker_dir / f"{cycle} Applications.md",
        )
        if (
            not any(path.is_file() for path in candidates)
            and cycle.casefold() not in desired_by_cycle
        ):
            continue
        tracker_path = _tracker_path(tracker_dir, cycle)
        text = tracker_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        divider_line = _application_table_divider_line(lines)
        if divider_line is None:
            continue
        table_end = _application_table_end(lines, divider_line)
        existing_rows = [
            list(cells)
            for line in lines[divider_line + 1 : table_end]
            if len(cells := _table_cells(line)) == len(_EXPECTED_TABLE_COLUMNS)
        ]
        unmanaged = [row for row in existing_rows if row[3].casefold() != _MANAGED_MAIL_SOURCE]
        managed = [row for row in existing_rows if row[3].casefold() == _MANAGED_MAIL_SOURCE]
        unmanaged_keys = {(row[0].casefold(), row[1].casefold()) for row in unmanaged}
        managed_by_key = {(row[0].casefold(), row[1].casefold()): row for row in managed}
        projected: list[list[str]] = []
        for key, desired in desired_by_cycle.get(cycle.casefold(), {}).items():
            if key in unmanaged_keys:
                continue
            existing = managed_by_key.get(key)
            if existing is not None:
                desired[2] = existing[2]
                desired[4] = existing[4] or desired[4]
                desired[5] = existing[5] or desired[5]
                desired[6] = existing[6] or desired[6]
                desired[7] = existing[7]
            projected.append(desired)
        rebuilt_rows = unmanaged + projected
        rendered_rows = [
            "| " + " | ".join(_table_cell(cell) for cell in row) + " |" for row in rebuilt_rows
        ]
        previous_rows = lines[divider_line + 1 : table_end]
        if previous_rows == rendered_rows:
            continue
        changes += max(1, len(managed), len(projected))
        lines[divider_line + 1 : table_end] = rendered_rows
        tracker_path.write_text(
            "\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8"
        )
    return changes


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
