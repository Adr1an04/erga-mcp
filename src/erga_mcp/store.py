from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .models import (
    Application,
    AuditEvent,
    Evidence,
    GitEvidenceCandidate,
    GitResearchBullet,
    GitResearchDraft,
    MailEvent,
    RecruiterContact,
    TokenUsage,
)

APPLICATION_STATUSES = frozenset(
    {
        "draft",
        "applied",
        "oa",
        "assessment",  # Backward-compatible alias for existing local records.
        "interview",
        "offer",
        "rejected",
        "withdrawn",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL,
    text TEXT NOT NULL,
    approved INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS git_evidence_candidates (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    commit_range TEXT NOT NULL,
    text TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    approved_evidence_id TEXT REFERENCES evidence(id),
    created_at TEXT NOT NULL,
    UNIQUE(repo_path, commit_sha)
);
CREATE TABLE IF NOT EXISTS git_scan_checkpoints (
    repo_path TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS git_research_drafts (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    bullet_candidates_json TEXT NOT NULL,
    generated_from_commit_metadata INTEGER NOT NULL,
    needs_review INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_usage (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    operation TEXT NOT NULL,
    input_tokens INTEGER NOT NULL CHECK(typeof(input_tokens) = 'integer' AND input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK(typeof(output_tokens) = 'integer' AND output_tokens >= 0),
    model TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS token_usage_application_id_idx ON token_usage(application_id);
CREATE TABLE IF NOT EXISTS mail_events (
    message_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    requires_review INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recruiter_contacts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT,
    company TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    source_message_id TEXT NOT NULL REFERENCES mail_events(message_id)
);
CREATE INDEX IF NOT EXISTS recruiter_contacts_last_seen_idx
ON recruiter_contacts(last_seen_at DESC);
CREATE TABLE IF NOT EXISTS recruiter_contact_applications (
    contact_id TEXT NOT NULL REFERENCES recruiter_contacts(id),
    application_id TEXT NOT NULL REFERENCES applications(id),
    PRIMARY KEY(contact_id, application_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _as_text(value: datetime) -> str:
    return value.isoformat()


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _require_token_count(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


class StoreFactory(Protocol):
    """Construct a store for one configured local workspace."""

    def create(self, database_path: Path) -> ErgaStore: ...


class SQLiteStoreFactory:
    """Default local SQLite construction seam for tests and future storage refactoring."""

    def create(self, database_path: Path) -> ErgaStore:
        return ErgaStore(database_path)


class ErgaStore:
    """A local SQLite store. It never talks to external services."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def _connection(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connection()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()

    def add_evidence(self, *, source_ref: str, text: str, approved: bool) -> Evidence:
        self.initialize()
        evidence = Evidence(
            id=f"ev_{uuid4().hex}",
            source_ref=source_ref,
            text=text,
            approved=approved,
            created_at=_now(),
        )
        with closing(self._connection()) as connection:
            connection.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                (
                    evidence.id,
                    evidence.source_ref,
                    evidence.text,
                    evidence.approved,
                    _as_text(evidence.created_at),
                ),
            )
            self._record_audit(connection, "evidence.added", evidence.id, {"approved": approved})
            connection.commit()
        return evidence

    def git_scan_checkpoint(self, repo_path: str) -> str | None:
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT commit_sha FROM git_scan_checkpoints WHERE repo_path = ?", (repo_path,)
            ).fetchone()
        return str(row["commit_sha"]) if row is not None else None

    def add_git_candidate(
        self, *, repo_path: str, commit_sha: str, commit_range: str, text: str
    ) -> GitEvidenceCandidate | None:
        self.initialize()
        candidate = GitEvidenceCandidate(
            id=f"gitcand_{uuid4().hex}",
            repo_path=repo_path,
            commit_sha=commit_sha,
            commit_range=commit_range,
            text=text,
            approved=False,
            approved_evidence_id=None,
            created_at=_now(),
        )
        with closing(self._connection()) as connection:
            result = connection.execute(
                """
                INSERT INTO git_evidence_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_path, commit_sha) DO NOTHING
                """,
                (
                    candidate.id,
                    candidate.repo_path,
                    candidate.commit_sha,
                    candidate.commit_range,
                    candidate.text,
                    candidate.approved,
                    candidate.approved_evidence_id,
                    _as_text(candidate.created_at),
                ),
            )
            if result.rowcount:
                self._record_audit(
                    connection,
                    "git_evidence.candidate_created",
                    candidate.id,
                    {"commit": commit_sha},
                )
                connection.commit()
                return candidate
        return None

    def save_git_scan_checkpoint(self, *, repo_path: str, commit_sha: str) -> None:
        self.initialize()
        with closing(self._connection()) as connection:
            connection.execute(
                """
                INSERT INTO git_scan_checkpoints VALUES (?, ?, ?)
                ON CONFLICT(repo_path) DO UPDATE SET commit_sha = excluded.commit_sha,
                    updated_at = excluded.updated_at
                """,
                (repo_path, commit_sha, _as_text(_now())),
            )
            connection.commit()

    def list_git_candidates(self, *, repo_path: str | None = None) -> list[GitEvidenceCandidate]:
        self.initialize()
        query = "SELECT * FROM git_evidence_candidates"
        parameters: tuple[str, ...] = ()
        if repo_path is not None:
            query += " WHERE repo_path = ?"
            parameters = (repo_path,)
        query += " ORDER BY created_at"
        with closing(self._connection()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._git_candidate_from_row(row) for row in rows]

    def save_git_research_draft(
        self,
        *,
        repo_path: str,
        summary: str,
        bullet_candidates: list[GitResearchBullet],
    ) -> GitResearchDraft:
        """Persist an unapproved deterministic draft; it never creates Evidence."""
        self.initialize()
        created_at = _now()
        draft = GitResearchDraft(
            id=f"gitdraft_{uuid4().hex}",
            repo_path=repo_path,
            summary=summary,
            bullet_candidates=bullet_candidates,
            generated_from_commit_metadata=True,
            needs_review=True,
            created_at=created_at,
        )
        serialized_bullets = json.dumps(
            [
                {
                    "text": bullet.text,
                    "source_candidate_ids": bullet.source_candidate_ids,
                    "source_commit_shas": bullet.source_commit_shas,
                }
                for bullet in bullet_candidates
            ]
        )
        with closing(self._connection()) as connection:
            connection.execute(
                """
                INSERT INTO git_research_drafts VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_path) DO UPDATE SET
                    summary = excluded.summary,
                    bullet_candidates_json = excluded.bullet_candidates_json,
                    generated_from_commit_metadata = excluded.generated_from_commit_metadata,
                    needs_review = excluded.needs_review,
                    created_at = excluded.created_at
                """,
                (
                    draft.id,
                    draft.repo_path,
                    draft.summary,
                    serialized_bullets,
                    draft.generated_from_commit_metadata,
                    draft.needs_review,
                    _as_text(draft.created_at),
                ),
            )
            row = connection.execute(
                "SELECT * FROM git_research_drafts WHERE repo_path = ?", (repo_path,)
            ).fetchone()
            assert row is not None
            saved = self._git_research_draft_from_row(row)
            self._record_audit(
                connection,
                "git_evidence.research_draft_generated",
                saved.id,
                {"bullets": len(saved.bullet_candidates), "repo_path": repo_path},
            )
            connection.commit()
        return saved

    def list_git_research_drafts(self) -> list[GitResearchDraft]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM git_research_drafts ORDER BY created_at DESC"
            ).fetchall()
        return [self._git_research_draft_from_row(row) for row in rows]

    def approve_git_candidate(self, candidate_id: str) -> Evidence:
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM git_evidence_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("git evidence candidate does not exist")
            if bool(row["approved"]):
                raise ValueError("git evidence candidate is already approved")
            evidence = Evidence(
                id=f"ev_{uuid4().hex}",
                source_ref=(f"git:{row['repo_path']}@{row['commit_sha']} ({row['commit_range']})"),
                text=row["text"],
                approved=True,
                created_at=_now(),
            )
            connection.execute(
                "INSERT INTO evidence VALUES (?, ?, ?, ?, ?)",
                (
                    evidence.id,
                    evidence.source_ref,
                    evidence.text,
                    evidence.approved,
                    _as_text(evidence.created_at),
                ),
            )
            connection.execute(
                "UPDATE git_evidence_candidates SET approved = 1, "
                "approved_evidence_id = ? WHERE id = ?",
                (evidence.id, candidate_id),
            )
            self._record_audit(
                connection,
                "git_evidence.candidate_approved",
                candidate_id,
                {"evidence_id": evidence.id},
            )
            connection.commit()
        return evidence

    @staticmethod
    def _git_candidate_from_row(row: sqlite3.Row) -> GitEvidenceCandidate:
        return GitEvidenceCandidate(
            id=row["id"],
            repo_path=row["repo_path"],
            commit_sha=row["commit_sha"],
            commit_range=row["commit_range"],
            text=row["text"],
            approved=bool(row["approved"]),
            approved_evidence_id=row["approved_evidence_id"],
            created_at=_as_datetime(row["created_at"]),
        )

    @staticmethod
    def _git_research_draft_from_row(row: sqlite3.Row) -> GitResearchDraft:
        bullets = [
            GitResearchBullet(
                text=str(item["text"]),
                source_candidate_ids=[str(value) for value in item["source_candidate_ids"]],
                source_commit_shas=[str(value) for value in item["source_commit_shas"]],
            )
            for item in json.loads(row["bullet_candidates_json"])
        ]
        return GitResearchDraft(
            id=row["id"],
            repo_path=row["repo_path"],
            summary=row["summary"],
            bullet_candidates=bullets,
            generated_from_commit_metadata=bool(row["generated_from_commit_metadata"]),
            needs_review=bool(row["needs_review"]),
            created_at=_as_datetime(row["created_at"]),
        )

    def create_application(
        self, *, company: str, role: str, source_url: str, evidence_ids: list[str]
    ) -> Application:
        self.initialize()
        self._require_approved_evidence(evidence_ids)
        application = Application(
            id=f"app_{uuid4().hex}",
            company=company,
            role=role,
            source_url=source_url,
            status="draft",
            evidence_ids=evidence_ids,
            created_at=_now(),
        )
        with closing(self._connection()) as connection:
            connection.execute(
                "INSERT INTO applications VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    application.id,
                    application.company,
                    application.role,
                    application.source_url,
                    application.status,
                    json.dumps(application.evidence_ids),
                    _as_text(application.created_at),
                ),
            )
            self._record_audit(
                connection,
                "application.created",
                application.id,
                {"status": "draft"},
            )
            connection.commit()
        return application

    def list_applications(self) -> list[Application]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute("SELECT * FROM applications ORDER BY created_at").fetchall()
        return [
            Application(
                id=row["id"],
                company=row["company"],
                role=row["role"],
                source_url=row["source_url"],
                status=row["status"],
                evidence_ids=json.loads(row["evidence_ids_json"]),
                created_at=_as_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def record_token_usage(
        self,
        *,
        application_id: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        model: str | None = None,
    ) -> TokenUsage:
        """Record user-visible model token counts against an existing local application."""
        input_tokens = _require_token_count(input_tokens, field="input_tokens")
        output_tokens = _require_token_count(output_tokens, field="output_tokens")
        normalized_operation = " ".join(operation.split())
        if not normalized_operation:
            raise ValueError("operation must not be empty")
        normalized_model = " ".join(model.split()) if model else None
        self.initialize()
        usage = TokenUsage(
            id=f"tok_{uuid4().hex}",
            application_id=application_id,
            operation=normalized_operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=normalized_model,
            created_at=_now(),
        )
        with closing(self._connection()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if exists is None:
                raise ValueError("application does not exist")
            connection.execute(
                "INSERT INTO token_usage VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    usage.id,
                    usage.application_id,
                    usage.operation,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.model,
                    _as_text(usage.created_at),
                ),
            )
            self._record_audit(
                connection,
                "token_usage.recorded",
                usage.id,
                {
                    "application_id": usage.application_id,
                    "input_tokens": usage.input_tokens,
                    "operation": usage.operation,
                    "output_tokens": usage.output_tokens,
                },
            )
            connection.commit()
        return usage

    def token_usage_summary(self, *, application_id: str | None = None) -> dict[str, int]:
        """Return token totals globally or for one application without estimating a dollar cost."""
        self.initialize()
        query = (
            "SELECT COUNT(*) AS events, COUNT(DISTINCT application_id) AS applications, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM token_usage"
        )
        parameters: tuple[str, ...] = ()
        if application_id is not None:
            query += " WHERE application_id = ?"
            parameters = (application_id,)
        with closing(self._connection()) as connection:
            row = connection.execute(query, parameters).fetchone()
        assert row is not None
        input_tokens = int(row["input_tokens"])
        output_tokens = int(row["output_tokens"])
        return {
            "applications": int(row["applications"]),
            "events": int(row["events"]),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    def update_application_metadata(
        self,
        application_id: str,
        *,
        company: str,
        role: str,
    ) -> Application:
        """Correct source-derived metadata without changing status, URL, or evidence."""
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                raise ValueError("application does not exist")
            if row["company"] != company or row["role"] != role:
                connection.execute(
                    "UPDATE applications SET company = ?, role = ? WHERE id = ?",
                    (company, role, application_id),
                )
                self._record_audit(
                    connection,
                    "application.metadata_updated",
                    application_id,
                    {"company": company, "role": role},
                )
                connection.commit()
            return Application(
                id=row["id"],
                company=company,
                role=role,
                source_url=row["source_url"],
                status=row["status"],
                evidence_ids=json.loads(row["evidence_ids_json"]),
                created_at=_as_datetime(row["created_at"]),
            )

    def update_application_status(self, application_id: str, *, status: str) -> Application:
        """Record an explicit local status change without contacting an employer."""
        normalized = status.strip().casefold()
        if normalized not in APPLICATION_STATUSES:
            allowed = ", ".join(sorted(APPLICATION_STATUSES))
            raise ValueError(f"application status must be one of: {allowed}")
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                raise ValueError("application does not exist")
            previous = str(row["status"])
            if previous != normalized:
                connection.execute(
                    "UPDATE applications SET status = ? WHERE id = ?",
                    (normalized, application_id),
                )
                self._record_audit(
                    connection,
                    "application.status_updated",
                    application_id,
                    {"from": previous, "to": normalized},
                )
                connection.commit()
            return Application(
                id=row["id"],
                company=row["company"],
                role=row["role"],
                source_url=row["source_url"],
                status=normalized,
                evidence_ids=json.loads(row["evidence_ids_json"]),
                created_at=_as_datetime(row["created_at"]),
            )

    def update_application_status_from_mail(
        self, application_id: str, *, status: str, event: MailEvent
    ) -> Application:
        """Record a deterministic email-derived status transition with its source event."""
        normalized = status.strip().casefold()
        if normalized not in APPLICATION_STATUSES:
            allowed = ", ".join(sorted(APPLICATION_STATUSES))
            raise ValueError(f"application status must be one of: {allowed}")
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
            if row is None:
                raise ValueError("application does not exist")
            previous = str(row["status"])
            if previous != normalized:
                connection.execute(
                    "UPDATE applications SET status = ? WHERE id = ?",
                    (normalized, application_id),
                )
                self._record_audit(
                    connection,
                    "application.status_updated_from_mail",
                    application_id,
                    {
                        "from": previous,
                        "mail_event_id": event.message_id,
                        "mail_kind": event.kind,
                        "to": normalized,
                    },
                )
                connection.commit()
            return Application(
                id=row["id"],
                company=row["company"],
                role=row["role"],
                source_url=row["source_url"],
                status=normalized,
                evidence_ids=json.loads(row["evidence_ids_json"]),
                created_at=_as_datetime(row["created_at"]),
            )

    def list_evidence(self) -> list[Evidence]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute("SELECT * FROM evidence ORDER BY created_at").fetchall()
        return [
            Evidence(
                id=row["id"],
                source_ref=row["source_ref"],
                text=row["text"],
                approved=bool(row["approved"]),
                created_at=_as_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def approved_evidence(self, evidence_ids: list[str]) -> list[Evidence]:
        evidence_by_id = {item.id: item for item in self.list_evidence()}
        selected = [evidence_by_id.get(evidence_id) for evidence_id in evidence_ids]
        if not selected or any(item is None or not item.approved for item in selected):
            raise ValueError("resume proposals require existing approved evidence")
        return [item for item in selected if item is not None]

    def record_mail_event(self, event: MailEvent) -> bool:
        """Persist minimal classified mail metadata once; never retain preview/body content."""
        self.initialize()
        with closing(self._connection()) as connection:
            result = connection.execute(
                """
                INSERT INTO mail_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (
                    event.message_id,
                    _as_text(event.received_at),
                    event.sender,
                    event.subject,
                    event.kind,
                    event.confidence,
                    event.requires_review,
                    _as_text(_now()),
                ),
            )
            if result.rowcount:
                self._record_audit(
                    connection,
                    "mail_event.recorded",
                    event.message_id,
                    {"kind": event.kind, "requires_review": event.requires_review},
                )
            connection.commit()
        return bool(result.rowcount)

    def update_mail_event_classification(self, event: MailEvent) -> bool:
        """Refresh a retained event when deterministic classification rules improve."""
        self.initialize()
        with closing(self._connection()) as connection:
            result = connection.execute(
                """
                UPDATE mail_events
                SET kind = ?, confidence = ?, requires_review = ?
                WHERE message_id = ?
                  AND (kind != ? OR confidence != ? OR requires_review != ?)
                """,
                (
                    event.kind,
                    event.confidence,
                    event.requires_review,
                    event.message_id,
                    event.kind,
                    event.confidence,
                    event.requires_review,
                ),
            )
            if result.rowcount:
                self._record_audit(
                    connection,
                    "mail_event.reclassified",
                    event.message_id,
                    {"kind": event.kind, "requires_review": event.requires_review},
                )
            connection.commit()
        return bool(result.rowcount)

    def list_mail_events(self) -> list[MailEvent]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute("SELECT * FROM mail_events ORDER BY received_at").fetchall()
        return [
            MailEvent(
                message_id=row["message_id"],
                received_at=_as_datetime(row["received_at"]),
                sender=row["sender"],
                subject=row["subject"],
                kind=row["kind"],
                confidence=float(row["confidence"]),
                requires_review=bool(row["requires_review"]),
            )
            for row in rows
        ]

    def upsert_recruiter_contact(
        self,
        *,
        email: str,
        name: str | None,
        company: str | None,
        source_message_id: str,
        seen_at: datetime,
    ) -> RecruiterContact:
        """Create or refresh a recruiter contact from minimal mail metadata."""
        normalized_email = email.strip().casefold()
        if "@" not in normalized_email or normalized_email.startswith("@"):
            raise ValueError("email must be a valid address")
        normalized_name = " ".join(name.split()) if name else None
        normalized_company = " ".join(company.split()) if company else None
        self.initialize()
        with closing(self._connection()) as connection:
            row = connection.execute(
                "SELECT * FROM recruiter_contacts WHERE email = ?", (normalized_email,)
            ).fetchone()
            if row is None:
                contact = RecruiterContact(
                    id=f"contact_{uuid4().hex}",
                    email=normalized_email,
                    name=normalized_name,
                    company=normalized_company,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    source_message_id=source_message_id,
                )
                connection.execute(
                    "INSERT INTO recruiter_contacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        contact.id,
                        contact.email,
                        contact.name,
                        contact.company,
                        _as_text(contact.first_seen_at),
                        _as_text(contact.last_seen_at),
                        contact.source_message_id,
                    ),
                )
                self._record_audit(
                    connection, "recruiter_contact.created", contact.id, {"email": contact.email}
                )
            else:
                contact = RecruiterContact(
                    id=row["id"],
                    email=row["email"],
                    name=normalized_name or row["name"],
                    company=normalized_company or row["company"],
                    first_seen_at=_as_datetime(row["first_seen_at"]),
                    last_seen_at=seen_at,
                    source_message_id=source_message_id,
                )
                connection.execute(
                    """
                    UPDATE recruiter_contacts
                    SET name = ?, company = ?, last_seen_at = ?, source_message_id = ?
                    WHERE id = ?
                    """,
                    (
                        contact.name,
                        contact.company,
                        _as_text(contact.last_seen_at),
                        contact.source_message_id,
                        contact.id,
                    ),
                )
            connection.commit()
        return contact

    def list_recruiter_contacts(self) -> list[RecruiterContact]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM recruiter_contacts ORDER BY last_seen_at DESC"
            ).fetchall()
        return [
            RecruiterContact(
                id=row["id"],
                email=row["email"],
                name=row["name"],
                company=row["company"],
                first_seen_at=_as_datetime(row["first_seen_at"]),
                last_seen_at=_as_datetime(row["last_seen_at"]),
                source_message_id=row["source_message_id"],
            )
            for row in rows
        ]

    def audit_events(self) -> list[AuditEvent]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC"
            ).fetchall()
        return [
            AuditEvent(
                id=row["id"],
                action=row["action"],
                subject_id=row["subject_id"],
                payload=json.loads(row["payload_json"]),
                created_at=_as_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def _require_approved_evidence(self, evidence_ids: list[str]) -> None:
        if not evidence_ids:
            return
        placeholders = ",".join("?" for _ in evidence_ids)
        with closing(self._connection()) as connection:
            rows = connection.execute(
                f"SELECT id, approved FROM evidence WHERE id IN ({placeholders})", evidence_ids
            ).fetchall()
        found = {row["id"]: bool(row["approved"]) for row in rows}
        invalid = [evidence_id for evidence_id in evidence_ids if not found.get(evidence_id)]
        if invalid:
            raise ValueError("applications may reference only existing approved evidence")

    @staticmethod
    def _record_audit(
        connection: sqlite3.Connection, action: str, subject_id: str, payload: dict[str, object]
    ) -> None:
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
            (f"audit_{uuid4().hex}", action, subject_id, json.dumps(payload), _as_text(_now())),
        )
