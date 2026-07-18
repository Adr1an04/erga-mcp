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
class AuditEvent:
    id: str
    action: str
    subject_id: str
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
