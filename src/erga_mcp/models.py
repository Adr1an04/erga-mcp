from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Evidence:
    id: str
    source_ref: str
    text: str
    approved: bool
    created_at: datetime


@dataclass(frozen=True)
class Application:
    id: str
    company: str
    role: str
    source_url: str
    status: str
    evidence_ids: list[str]
    created_at: datetime


@dataclass(frozen=True)
class TokenUsage:
    id: str
    application_id: str
    operation: str
    input_tokens: int
    output_tokens: int
    model: str | None
    created_at: datetime

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class RecruiterContact:
    id: str
    email: str
    name: str | None
    company: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    source_message_id: str


@dataclass(frozen=True)
class AuditEvent:
    id: str
    action: str
    subject_id: str
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class MailEvent:
    message_id: str
    received_at: datetime
    sender: str
    subject: str
    kind: str
    confidence: float
    requires_review: bool
