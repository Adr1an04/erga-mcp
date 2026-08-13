from __future__ import annotations

from collections.abc import Sequence

from erga_mcp.models import Application, MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.mail_reconciliation import reconcile_mail_events


def apply_mail_status_transition(store: ErgaStore, event: MailEvent) -> Application | None:
    """Compatibility wrapper over the provenance-aware reconciliation engine."""
    before = {item.id: item.status for item in store.list_applications()}
    reconcile_mail_events(store, [event])
    return next(
        (
            item
            for item in store.list_applications()
            if before.get(item.id) is not None and before[item.id] != item.status
        ),
        None,
    )


def reconcile_mail_status_transitions(store: ErgaStore, events: Sequence[MailEvent]) -> int:
    """Compatibility wrapper returning the number of guarded local transitions."""
    return reconcile_mail_events(store, events).transitions
