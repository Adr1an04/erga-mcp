from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore

_EVENT_LABELS = {
    "application.acknowledgement": "Application acknowledgement",
    "application.assessment": "Assessment invitation",
    "application.interview": "Interview invitation",
    "application.offer": "Offer received",
    "application.denial": "Application decision",
    "job.candidate": "Potential job lead",
}


def _recent_recruiting_events(
    events: list[MailEvent], *, days: int, now: datetime
) -> list[MailEvent]:
    cutoff = now - timedelta(days=days)
    return [
        event for event in events if event.received_at >= cutoff and event.kind in _EVENT_LABELS
    ]


def render_history_digest(
    store: ErgaStore,
    *,
    days: int = 7,
    now: datetime | None = None,
    event_limit: int = 10,
) -> str:
    """Render a private, metadata-only recruiting history digest."""
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    if event_limit < 1 or event_limit > 100:
        raise ValueError("event_limit must be between 1 and 100")
    observed_at = now or datetime.now(UTC)
    applications = store.list_applications()
    recent = _recent_recruiting_events(store.list_mail_events(), days=days, now=observed_at)
    recent.sort(key=lambda event: event.received_at, reverse=True)
    status_counts = Counter(application.status for application in applications)
    status_text = (
        ", ".join(f"{status}: {count}" for status, count in sorted(status_counts.items())) or "none"
    )
    needs_review = sum(event.requires_review for event in recent)
    lines = [
        "📊 Recruiting pipeline history",
        "",
        f"Applications: {len(applications)} ({status_text})",
        f"Recruiting updates in the last {days} day(s): {len(recent)}",
        f"Updates needing review: {needs_review}",
    ]
    if recent:
        lines.extend(["", "Recent updates:"])
        for event in recent[:event_limit]:
            label = _EVENT_LABELS[event.kind]
            review = " — needs review" if event.requires_review else ""
            lines.append(
                f"- {event.received_at.date().isoformat()} · {label}{review} · "
                f"{event.subject} · {event.sender}"
            )
    else:
        lines.extend(["", "No new recruiting updates were recorded in this window."])
    return "\n".join(lines)
