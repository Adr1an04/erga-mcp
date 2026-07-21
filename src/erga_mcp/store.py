from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .models import Application, AuditEvent, Evidence, MailEvent, RecruiterContact, TokenUsage

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
