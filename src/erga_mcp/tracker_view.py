from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TABLE_HEADER = (
    "company",
    "role",
    "location / work mode",
    "source",
    "status",
    "applied",
    "next action",
    "contact / link",
)
_TRACKER_SUFFIXES = (" Application Tracker", " Applications")
_MARKDOWN_LINK = re.compile(r"\[[^]]*\]\((https?://[^)\s]+)\)")
_DISPLAY_TRACKING_QUERY_KEYS = frozenset(
    {"fbclid", "gh_src", "ref", "referrer", "source", "sourceid", "trk", "tracking"}
)
_STATUS_ICONS = {
    "applied": "📬",
    "oa": "🧪",
    "online assessment": "🧪",
    "assessment": "🧪",
    "interview": "🗣️",
    "offer": "🎉",
    "rejected": "⛔",
    "withdrawn": "↩️",
    "researching": "🟡",
    "draft": "⚪",
}
_DISPLAY_PRIORITY = {
    "offer": 0,
    "interview": 1,
    "oa": 2,
    "online assessment": 2,
    "assessment": 2,
    "applied": 3,
    "ready to apply": 4,
    "draft": 5,
    "researching": 6,
    "rejected": 7,
    "withdrawn": 8,
}
_SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}
_STATUS_PROGRESS = {
    "researching": 0,
    "draft": 1,
    "ready to apply": 2,
    "applied": 3,
    "oa": 4,
    "online assessment": 4,
    "assessment": 4,
    "interview": 5,
    "offer": 6,
    "rejected": 7,
    "withdrawn": 7,
}


@dataclass(frozen=True)
class TrackerEntry:
    cycle: str
    company: str
    role: str
    location: str
    source_url: str
    status: str
    applied: str
    next_action: str


@dataclass(frozen=True)
class TrackerSnapshot:
    entries: tuple[TrackerEntry, ...]
    summary: dict[str, int]


@dataclass(frozen=True)
class TrackerPage:
    entries: tuple[TrackerEntry, ...]
    page: int
    page_count: int
    page_size: int
    total: int
    start: int
    end: int


def _cells(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()
    return tuple(" ".join(cell.split()) for cell in stripped[1:-1].split("|"))


def _cycle_name(path: Path) -> str:
    for suffix in _TRACKER_SUFFIXES:
        if path.stem.endswith(suffix):
            return path.stem[: -len(suffix)]
    return path.stem


def _tracker_paths(tracker_dir: Path) -> tuple[Path, ...]:
    if not tracker_dir.is_dir():
        return ()
    paths: list[Path] = []
    for path in sorted(tracker_dir.glob("*.md"), key=lambda item: item.name.casefold()):
        if path.is_symlink() or not path.is_file():
            continue
        if any(path.stem.endswith(suffix) for suffix in _TRACKER_SUFFIXES):
            paths.append(path)
    return tuple(paths)


def _source_url(source: str) -> str:
    match = _MARKDOWN_LINK.search(source)
    if match is None:
        return ""
    parsed = urlsplit(match.group(1))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _DISPLAY_TRACKING_QUERY_KEYS
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))


def _canonical_status(status: str) -> str:
    normalized = " ".join(status.casefold().split())
    if normalized in {"oa", "online assessment", "assessment"}:
        return "assessment"
    return normalized or "unknown"


def _snapshot(entries: tuple[TrackerEntry, ...]) -> TrackerSnapshot:
    counts = Counter(_canonical_status(entry.status) for entry in entries)
    summary = dict(
        sorted(
            counts.items(),
            key=lambda item: (_DISPLAY_PRIORITY.get(item[0], 9), item[0]),
        )
    )
    return TrackerSnapshot(entries=entries, summary=summary)


def _entries_from_tracker(path: Path) -> tuple[TrackerEntry, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    header_index: int | None = None
    for index, line in enumerate(lines):
        if tuple(cell.casefold() for cell in _cells(line)) == _TABLE_HEADER:
            header_index = index
            break
    if header_index is None or header_index + 1 >= len(lines):
        return ()
    entries: list[TrackerEntry] = []
    for line in lines[header_index + 2 :]:
        cells = _cells(line)
        if not cells:
            if entries:
                break
            continue
        if len(cells) != len(_TABLE_HEADER):
            continue
        company, role, location, source, status, applied, next_action, _link = cells
        if not company or not role:
            continue
        entries.append(
            TrackerEntry(
                cycle=_cycle_name(path),
                company=company,
                role=role,
                location=location,
                source_url=_source_url(source),
                status=status or "Researching",
                applied=applied,
                next_action=next_action,
            )
        )
    return tuple(entries)


def _coalesce_email_confirmations(
    entries: tuple[TrackerEntry, ...],
) -> tuple[TrackerEntry, ...]:
    """Merge an unlinked mail-confirmation row into one unambiguous sourced job row."""
    sourced_by_company: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        if entry.source_url:
            sourced_by_company.setdefault(entry.company.casefold(), []).append(index)
    merged = list(entries)
    removed: set[int] = set()
    for index, confirmation in enumerate(entries):
        if (
            confirmation.source_url
            or confirmation.role.casefold() != "application confirmed by email"
        ):
            continue
        candidates = sourced_by_company.get(confirmation.company.casefold(), [])
        if len(candidates) != 1:
            continue
        target_index = candidates[0]
        target = merged[target_index]
        confirmation_progress = _STATUS_PROGRESS.get(confirmation.status.casefold(), -1)
        target_progress = _STATUS_PROGRESS.get(target.status.casefold(), -1)
        use_confirmation = confirmation_progress > target_progress
        merged[target_index] = replace(
            target,
            status=confirmation.status if use_confirmation else target.status,
            applied=confirmation.applied or target.applied,
            next_action=confirmation.next_action if use_confirmation else target.next_action,
        )
        removed.add(index)
    return tuple(entry for index, entry in enumerate(merged) if index not in removed)


def read_application_tracker(tracker_dir: Path) -> TrackerSnapshot:
    """Read the configured Obsidian tracker tables without modifying the vault."""
    raw_entries = tuple(
        entry for path in _tracker_paths(tracker_dir) for entry in _entries_from_tracker(path)
    )
    entries = _coalesce_email_confirmations(raw_entries)
    return _snapshot(entries)


def filter_application_tracker(snapshot: TrackerSnapshot, query: str) -> TrackerSnapshot:
    """Return case-insensitive token matches across the human-searchable tracker fields."""
    if query.strip().casefold() in {"", "all", "*"}:
        return snapshot
    tokens = tuple(token.casefold() for token in query.split() if token.strip())

    def matches(entry: TrackerEntry) -> bool:
        haystack = "\n".join(
            (
                entry.company,
                entry.role,
                entry.location,
                entry.status,
                entry.cycle,
                entry.next_action,
            )
        ).casefold()
        return all(token in haystack for token in tokens)

    entries = tuple(entry for entry in snapshot.entries if matches(entry))
    return _snapshot(entries)


def _short(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1].rstrip()}…"


def _status_label(status: str) -> str:
    normalized = status.casefold()
    return f"{_STATUS_ICONS.get(normalized, '•')} {status}"


def _cycle_sort_key(cycle: str) -> tuple[int, int, int, str]:
    normalized = " ".join(cycle.casefold().split())
    match = re.fullmatch(r"(winter|spring|summer|fall)\s+(\d{4})", normalized)
    if match is not None:
        # Keep future/newer recruiting cycles together and ahead of older cycles.
        return (0, -int(match.group(2)), -_SEASON_ORDER[match.group(1)], normalized)
    if normalized == "unscheduled":
        return (2, 0, 0, normalized)
    return (1, 0, 0, normalized)


def _natural_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.split(r"(\d+)", value.casefold())
    )


def _ordered_entries(snapshot: TrackerSnapshot) -> tuple[TrackerEntry, ...]:
    return tuple(
        sorted(
            snapshot.entries,
            key=lambda entry: (
                _cycle_sort_key(entry.cycle),
                _DISPLAY_PRIORITY.get(entry.status.casefold(), 9),
                _natural_key(entry.company),
                _natural_key(entry.role),
            ),
        )
    )


def paginate_application_tracker(
    snapshot: TrackerSnapshot, *, page: int = 1, page_size: int = 6
) -> TrackerPage:
    """Return one stable cross-cycle page without hiding the total result count."""
    if page < 1:
        raise ValueError("page must be positive")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    ordered = _ordered_entries(snapshot)
    total = len(ordered)
    page_count = max(1, (total + page_size - 1) // page_size)
    resolved_page = min(page, page_count)
    start = (resolved_page - 1) * page_size
    entries = ordered[start : start + page_size]
    return TrackerPage(
        entries=entries,
        page=resolved_page,
        page_count=page_count,
        page_size=page_size,
        total=total,
        start=start + 1 if entries else 0,
        end=start + len(entries),
    )


def render_tracker_message(
    snapshot: TrackerSnapshot,
    *,
    page: int = 1,
    page_size: int = 6,
    max_entries: int | None = None,
    query: str = "",
    token_usage_by_source_url: Mapping[str, Mapping[str, int]] | None = None,
    local_application_count: int | None = None,
) -> str:
    """Render an intentionally compact Markdown card that works across gateway platforms."""
    if max_entries is not None:
        page_size = max_entries
    pagination = paginate_application_tracker(snapshot, page=page, page_size=page_size)
    if not snapshot.entries:
        return (
            "### Erga application tracker\n\n"
            "No application rows are available in the configured Obsidian trackers yet."
        )

    summary = " · ".join(f"{count} {status}" for status, count in snapshot.summary.items())
    cycle_count = len({entry.cycle for entry in snapshot.entries})
    cycle_noun = "cycle" if cycle_count == 1 else "cycles"
    header = f"**{pagination.total} roles across {cycle_count} {cycle_noun}**"
    if local_application_count is not None:
        header += f" · {local_application_count} local records"
    lines = ["### Erga application tracker", header, summary]
    if query.strip() and query.strip().casefold() not in {"all", "*"}:
        noun = "match" if pagination.total == 1 else "matches"
        lines.append(f"Search: {_short(query, limit=80)} · {pagination.total} {noun}")
    lines.append("")
    current_cycle: str | None = None
    for entry in pagination.entries:
        if entry.cycle != current_cycle:
            if current_cycle is not None:
                lines.append("")
            lines.append(f"**{_short(entry.cycle, limit=80)}**")
            current_cycle = entry.cycle
        details = _status_label(entry.status)
        if entry.location:
            details = f"{details} · {_short(entry.location, limit=80)}"
        if entry.applied:
            details = f"{details} · Applied {entry.applied}"
        company = _short(entry.company, limit=80)
        if entry.source_url:
            company = f"[{company}]({entry.source_url})"
        lines.append(
            f"{_STATUS_ICONS.get(entry.status.casefold(), '•')} "
            f"**{company}** - {_short(entry.role, limit=120)}"
        )
        lines.append(f"> {details}")
        if entry.next_action:
            lines.append(f"> Next: {_short(entry.next_action, limit=160)}")
        usage = (token_usage_by_source_url or {}).get(entry.source_url)
        if usage and usage.get("events", 0):
            lines.append(
                "> Tokens: "
                f"{usage.get('input_tokens', 0):,} in · "
                f"{usage.get('output_tokens', 0):,} out · "
                f"{usage.get('total_tokens', 0):,} total"
            )
    if pagination.page_count > 1:
        lines.extend(
            [
                "",
                f"Page {pagination.page} of {pagination.page_count} · "
                f"showing {pagination.start}-{pagination.end} of {pagination.total} roles.",
            ]
        )
    return "\n".join(lines)
