from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
