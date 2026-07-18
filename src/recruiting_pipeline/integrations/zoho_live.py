from __future__ import annotations

from collections.abc import Sequence

from ..classification import classify_application_message
from ..models import MailEvent
from ..store import PipelineStore
from .zoho import MailMessageMetadata

_JOB_MARKERS = ("recruiter", "opportunity", "role", "position", "opening", "hiring")


def _classify(message: MailMessageMetadata) -> tuple[str, float, bool]:
    application = classify_application_message(subject=message.subject, preview=message.preview)
    if application.kind != "unknown":
        return (
            f"application.{application.kind}",
            application.confidence,
            application.requires_review,
        )
    content = f"{message.sender}\n{message.subject}\n{message.preview}".casefold()
    if any(marker in content for marker in _JOB_MARKERS):
        return "job.candidate", 0.7, True
    return "other", 0.0, False


def sync_metadata(store: PipelineStore, messages: Sequence[MailMessageMetadata]) -> dict[str, int]:
    """Persist minimal classified metadata only; previews and bodies are never stored."""
    counts = {"application": 0, "job": 0, "other": 0, "created": 0}
    for message in messages:
        kind, confidence, requires_review = _classify(message)
        created = store.record_mail_event(
            MailEvent(
                message_id=message.message_id,
                received_at=message.received_at,
                sender=message.sender,
                subject=message.subject,
                kind=kind,
                confidence=confidence,
                requires_review=requires_review,
            )
        )
        if created:
            counts["created"] += 1
            counts[kind.split(".", 1)[0]] += 1
    return counts
