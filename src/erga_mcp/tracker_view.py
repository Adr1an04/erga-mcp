from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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
    return match.group(1) if match is not None else ""


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


def read_application_tracker(tracker_dir: Path) -> TrackerSnapshot:
    """Read the configured Obsidian tracker tables without modifying the vault."""
    entries = tuple(
        entry for path in _tracker_paths(tracker_dir) for entry in _entries_from_tracker(path)
    )
    counts = Counter(entry.status.casefold() for entry in entries)
    return TrackerSnapshot(entries=entries, summary=dict(sorted(counts.items())))


def filter_application_tracker(snapshot: TrackerSnapshot, query: str) -> TrackerSnapshot:
    """Return case-insensitive token matches across the human-searchable tracker fields."""
    tokens = tuple(token.casefold() for token in query.split() if token.strip())
    if not tokens:
        return snapshot

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
    counts = Counter(entry.status.casefold() for entry in entries)
    return TrackerSnapshot(entries=entries, summary=dict(sorted(counts.items())))


def _short(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1].rstrip()}…"


def _status_label(status: str) -> str:
    normalized = status.casefold()
    return f"{_STATUS_ICONS.get(normalized, '•')} {status}"


def render_tracker_message(
    snapshot: TrackerSnapshot,
    *,
    max_entries: int = 20,
    query: str = "",
    token_usage_by_source_url: Mapping[str, Mapping[str, int]] | None = None,
) -> str:
    """Render an intentionally compact Markdown card that works across gateway platforms."""
    if max_entries < 1:
        raise ValueError("max_entries must be positive")
    if not snapshot.entries:
        return (
            "### Erga application tracker\n\n"
            "No application rows are available in the configured Obsidian trackers yet."
        )

    total = len(snapshot.entries)
    summary = " · ".join(f"{count} {status}" for status, count in snapshot.summary.items())
    lines = ["### Erga application tracker", f"**{total} roles** · {summary}"]
    if query.strip():
        noun = "match" if total == 1 else "matches"
        lines.append(f"Search: {_short(query, limit=80)} · {total} {noun}")
    lines.append("")
    displayed = snapshot.entries[:max_entries]
    current_cycle: str | None = None
    for entry in displayed:
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
        lines.append(
            f"{_STATUS_ICONS.get(entry.status.casefold(), '•')} "
            f"**{_short(entry.company, limit=80)}** - {_short(entry.role, limit=120)}"
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
    if total > len(displayed):
        lines.extend(["", f"Showing {len(displayed)} of {total} roles."])
    return "\n".join(lines)
