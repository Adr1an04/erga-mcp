from __future__ import annotations

import re
from email.utils import parseaddr

from erga_mcp.models import MailEvent, RecruiterContact
from erga_mcp.store import ErgaStore

_AUTOMATED_LOCAL_PART_MARKERS = (
    "noreply",
    "no-reply",
    "do_not_reply",
    "do-not-reply",
    "donotreply",
    "notification",
    "recruiting",
    "careers",
    "jobs",
    "talent",
    "support",
    "service",
    "programs",
    "resume",
    "system",
    "mailer-daemon",
    "assessment",
    "interview",
    "verification",
    "admin",
    "clientservices",
    "workflow",
    "sender",
)
_PERSONAL_MAILBOX = re.compile(r"^[a-z]{2,}[._-][a-z]{2,}(?:[._-][a-z]{2,})?$")


def _is_automated_mailbox(local_part: str) -> bool:
    normalized = local_part.casefold().replace("_", "-")
    return any(marker in normalized for marker in _AUTOMATED_LOCAL_PART_MARKERS)


def record_recruiter_contact_from_mail(
    store: ErgaStore, event: MailEvent
) -> RecruiterContact | None:
    """Persist a reviewable person contact only from recruiting-mail sender metadata.

    This deliberately ignores message bodies, generic mailboxes, and non-application mail.
    """
    if not (event.kind.startswith("application.") or event.kind == "job.candidate"):
        return None
    name, email = parseaddr(event.sender)
    normalized_email = email.strip().casefold()
    if "@" not in normalized_email:
        return None
    local_part = normalized_email.partition("@")[0]
    if _is_automated_mailbox(local_part):
        return None
    normalized_name = " ".join(name.split()) or None
    if normalized_name is None and _PERSONAL_MAILBOX.fullmatch(local_part) is None:
        return None
    return store.upsert_recruiter_contact(
        email=normalized_email,
        name=normalized_name,
        company=None,
        source_message_id=event.message_id,
        seen_at=event.received_at,
    )
