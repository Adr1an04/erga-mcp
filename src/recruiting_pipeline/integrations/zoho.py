from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..classification import classify_application_message
from ..models import MailEvent
from ..store import PipelineStore


@dataclass(frozen=True)
class MailMessageMetadata:
    """Minimal metadata needed for local classification; body retention is opt-in."""

    message_id: str
    received_at: datetime
    sender: str
    subject: str
    preview: str


class ReadOnlyMailSource(Protocol):
    """A future Zoho adapter boundary with no mutation methods."""

    def list_candidate_messages(
        self, *, folder: str, since_message_id: str | None
    ) -> Sequence[MailMessageMetadata]:
        """Return metadata from a user-authorized read-only folder."""
        raise NotImplementedError


_ALLOWED_READ_ONLY_SCOPES = frozenset({"ZohoMail.messages.READ", "ZohoMail.accounts.READ"})


def validate_read_only_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    """Reject broad or mutating Zoho scopes before an authorization flow is attempted."""
    normalized = tuple(sorted(set(scopes)))
    if "ZohoMail.messages.READ" not in normalized:
        raise ValueError("ZohoMail.messages.READ is required for mail metadata polling")
    unsupported = set(normalized) - _ALLOWED_READ_ONLY_SCOPES
    if unsupported:
        raise ValueError("only minimum read-only Zoho scopes are allowed")
    return normalized


def _required_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"fixture item requires a non-empty {key!r} string")
    return value


def load_metadata_fixture(fixture_path: Path) -> list[MailMessageMetadata]:
    """Load synthetic or user-exported metadata; no network or OAuth is involved."""
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("mail fixture must contain a JSON array")

    messages: list[MailMessageMetadata] = []
    for item in document:
        if not isinstance(item, dict):
            raise ValueError("each mail fixture item must be an object")
        received_at = datetime.fromisoformat(_required_string(item, "received_at"))
        if received_at.tzinfo is None:
            raise ValueError("received_at must include a timezone")
        messages.append(
            MailMessageMetadata(
                message_id=_required_string(item, "message_id"),
                received_at=received_at,
                sender=_required_string(item, "sender"),
                subject=_required_string(item, "subject"),
                preview=_required_string(item, "preview"),
            )
        )
    return messages


def ingest_fixture(store: PipelineStore, fixture_path: Path) -> int:
    """Classify fixture metadata and persist only normalized minimal event fields."""
    created = 0
    for message in load_metadata_fixture(fixture_path):
        classification = classify_application_message(
            subject=message.subject, preview=message.preview
        )
        event = MailEvent(
            message_id=message.message_id,
            received_at=message.received_at,
            sender=message.sender,
            subject=message.subject,
            kind=classification.kind,
            confidence=classification.confidence,
            requires_review=classification.requires_review,
        )
        created += int(store.record_mail_event(event))
    return created
