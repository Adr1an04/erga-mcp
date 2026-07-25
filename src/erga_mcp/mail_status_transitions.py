from __future__ import annotations

import re

from .models import Application, MailEvent
from .store import ErgaStore

_MAIL_STATUS = {
    "application.acknowledgement": "applied",
    "application.assessment": "oa",
    "application.interview": "interview",
    "application.offer": "offer",
    "application.denial": "rejected",
}
_TERMINAL_STATUSES = frozenset({"offer", "rejected", "withdrawn"})


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
    if application.status == target:
        return None
    if event.kind == "application.acknowledgement" and application.status != "draft":
        return None
    return store.update_application_status_from_mail(
        application.id,
        status=target,
        event=event,
    )
