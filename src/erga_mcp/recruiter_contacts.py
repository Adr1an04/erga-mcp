from __future__ import annotations

from email.utils import parseaddr

from .models import MailEvent, RecruiterContact
from .store import ErgaStore

_AUTOMATED_LOCAL_PARTS = frozenset({"no-reply", "noreply", "notifications", "jobs", "careers"})


def record_recruiter_contact_from_mail(
    store: ErgaStore, event: MailEvent
) -> RecruiterContact | None:
    """Persist a reviewable person contact only from recruiting-mail sender metadata.

    This deliberately ignores message bodies, generic mailboxes, and non-application mail.
    """
    if not event.kind.startswith("application."):
        return None
    name, email = parseaddr(event.sender)
    normalized_email = email.strip().casefold()
    if "@" not in normalized_email:
        return None
    local_part = normalized_email.partition("@")[0]
    if local_part in _AUTOMATED_LOCAL_PARTS:
        return None
    normalized_name = " ".join(name.split()) or None
    if normalized_name is None:
        return None
    return store.upsert_recruiter_contact(
        email=normalized_email,
        name=normalized_name,
        company=None,
        source_message_id=event.message_id,
        seen_at=event.received_at,
    )
