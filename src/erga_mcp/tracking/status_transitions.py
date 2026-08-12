from __future__ import annotations

import re
from collections.abc import Sequence

from erga_mcp.models import Application, MailEvent
from erga_mcp.store import ErgaStore

_MAIL_STATUS = {
    "application.acknowledgement": "applied",
    "application.assessment": "oa",
    "application.interview": "interview",
    "application.offer": "offer",
    "application.denial": "rejected",
}
_TERMINAL_STATUSES = frozenset({"offer", "rejected", "withdrawn"})
_STATUS_PROGRESS = {
    "draft": 0,
    "applied": 1,
    "oa": 2,
    "assessment": 2,
    "interview": 3,
    "interview-2": 4,
    "interview-3": 5,
    "final-interview": 6,
    "offer": 7,
    "accepted": 8,
}


def _mail_event_is_stale(store: ErgaStore, application: Application, event: MailEvent) -> bool:
    audits = store.audit_events()
    if any(
        audit.action == "application.status_updated_from_mail"
        and audit.subject_id == application.id
        and audit.payload.get("mail_event_id") == event.message_id
        for audit in audits
    ):
        return True
    manual_updates = [
        audit.created_at
        for audit in audits
        if audit.action == "application.status_updated" and audit.subject_id == application.id
    ]
    recorded_at = next(
        (
            audit.created_at
            for audit in audits
            if audit.action == "mail_event.recorded" and audit.subject_id == event.message_id
        ),
        None,
    )
    return bool(manual_updates and recorded_at is not None and max(manual_updates) > recorded_at)


def _company_matches(application: Application, event: MailEvent) -> bool:
    tokens = re.findall(r"[a-z0-9]+", application.company.casefold())
    if not tokens:
        return False
    content = f"{event.sender}\n{event.subject}".casefold()
    return all(
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", content) for token in tokens
    )


def apply_mail_status_transition(store: ErgaStore, event: MailEvent) -> Application | None:
    """Apply one status transition only when a recruiting email has one active company match."""
    target = _MAIL_STATUS.get(event.kind)
    if target is None:
        return None
    matches = [
        application
        for application in store.list_applications()
        if application.status not in _TERMINAL_STATUSES and _company_matches(application, event)
    ]
    if len(matches) != 1:
        return None
    application = matches[0]
    if _mail_event_is_stale(store, application, event):
        return None
    if application.status == target:
        return None
    current_progress = _STATUS_PROGRESS.get(application.status)
    target_progress = _STATUS_PROGRESS.get(target)
    if (
        current_progress is not None
        and target_progress is not None
        and target_progress < current_progress
    ):
        return None
    if event.kind == "application.acknowledgement" and application.status != "draft":
        return None
    return store.update_application_status_from_mail(
        application.id,
        status=target,
        event=event,
    )


def reconcile_mail_status_transitions(store: ErgaStore, events: Sequence[MailEvent]) -> int:
    """Apply the newest eligible exact-match event, including ones stored before deployment."""
    transitions = 0
    for event in sorted(events, key=lambda item: item.received_at, reverse=True):
        if apply_mail_status_transition(store, event) is not None:
            transitions += 1
    return transitions
