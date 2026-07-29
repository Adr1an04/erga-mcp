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
class GitEvidenceCandidate:
    id: str
    repo_path: str
    commit_sha: str
    commit_range: str
    text: str
    approved: bool
    approved_evidence_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class GitResearchBullet:
    text: str
    source_candidate_ids: list[str]
    source_commit_shas: list[str]
    source_files: list[str]
    diff_hashes: list[str]
    confidence: float


@dataclass(frozen=True)
class GitResearchDraft:
    id: str
    repo_path: str
    summary: str
    bullet_candidates: list[GitResearchBullet]
    generated_from_commit_metadata: bool
    generated_from_git_diffs: bool
    needs_review: bool
    source_commit_shas: list[str]
    source_files: list[str]
    diff_hashes: list[str]
    created_at: datetime


@dataclass(frozen=True)
class GitChangeObservation:
    repo_path: str
    commit_sha: str
    files: list[str]
    additions: int
    deletions: int
    symbols: list[str]
    change_kinds: list[str]
    diff_hash: str


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
