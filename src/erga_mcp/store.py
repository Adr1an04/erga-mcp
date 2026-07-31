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
    GitChangeObservation,
    GitEvidenceCandidate,
    GitResearchBullet,
    GitResearchDraft,
    MailEvent,
    RecruiterContact,
    TokenUsage,
)
from .private_files import restrict_private_directory, restrict_private_file

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
CREATE TABLE IF NOT EXISTS git_change_observations (
    repo_path TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    files_json TEXT NOT NULL,
    additions INTEGER NOT NULL,
    deletions INTEGER NOT NULL,
    symbols_json TEXT NOT NULL,
    change_kinds_json TEXT NOT NULL,
    diff_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(repo_path, commit_sha)
);
CREATE TABLE IF NOT EXISTS git_research_drafts (
    id TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    bullet_candidates_json TEXT NOT NULL,
    generated_from_commit_metadata INTEGER NOT NULL,
    needs_review INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'git',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'pending',
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
        parent_created = not self.database_path.parent.exists()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if parent_created:
            restrict_private_directory(self.database_path.parent)
        database_created = not self.database_path.exists()
        connection = sqlite3.connect(self.database_path)
        if database_created:
            restrict_private_file(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self._connection()) as connection:
            connection.executescript(_SCHEMA)
            existing_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(git_research_drafts)").fetchall()
            }
            for name, definition in (
                ("generated_from_git_diffs", "INTEGER NOT NULL DEFAULT 0"),
                ("source_commit_shas_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("source_files_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("diff_hashes_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("source", "TEXT NOT NULL DEFAULT 'git'"),
                ("title", "TEXT NOT NULL DEFAULT ''"),
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("review_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ):
                if name not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE git_research_drafts ADD COLUMN {name} {definition}"
                    )
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

    def set_active_master_resume_evidence(self, *, source_ref: str, text: str) -> Evidence:
        """Atomically make one master résumé the only approved master source."""
        self.initialize()
        with closing(self._connection()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM evidence WHERE source_ref = ? AND text = ? ORDER BY created_at "
                "LIMIT 1",
                (source_ref, text),
            ).fetchone()
            superseded = connection.execute(
                "SELECT id FROM evidence WHERE approved = 1 AND source_ref LIKE 'master-resume:%' "
                "AND NOT (source_ref = ? AND text = ?)",
                (source_ref, text),
            ).fetchall()
            connection.execute(
                "UPDATE evidence SET approved = 0 WHERE approved = 1 "
                "AND source_ref LIKE 'master-resume:%' AND NOT (source_ref = ? AND text = ?)",
                (source_ref, text),
            )
            if existing is None:
                evidence = Evidence(
                    id=f"ev_{uuid4().hex}",
                    source_ref=source_ref,
                    text=text,
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
                self._record_audit(
                    connection,
                    "evidence.added",
                    evidence.id,
                    {"approved": True, "kind": "master-resume"},
                )
            else:
                connection.execute(
                    "UPDATE evidence SET approved = 1 WHERE id = ?",
                    (existing["id"],),
                )
                evidence = Evidence(
                    id=existing["id"],
                    source_ref=existing["source_ref"],
                    text=existing["text"],
                    approved=True,
                    created_at=_as_datetime(existing["created_at"]),
                )
            for row in superseded:
                self._record_audit(
                    connection,
                    "evidence.superseded",
                    row["id"],
                    {"replacement_id": evidence.id, "kind": "master-resume"},
                )
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

    def save_git_change_observation(self, observation: GitChangeObservation) -> bool:
        """Persist bounded diff facts only; raw diff text is intentionally never stored."""
        self.initialize()
        with closing(self._connection()) as connection:
            result = connection.execute(
                """
                INSERT INTO git_change_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_path, commit_sha) DO NOTHING
                """,
                (
                    observation.repo_path,
                    observation.commit_sha,
                    json.dumps(observation.files),
                    observation.additions,
                    observation.deletions,
                    json.dumps(observation.symbols),
                    json.dumps(observation.change_kinds),
                    observation.diff_hash,
                    _as_text(_now()),
                ),
            )
            if result.rowcount:
                self._record_audit(
                    connection,
                    "git_evidence.diff_observation_created",
                    observation.commit_sha,
                    {"repo_path": observation.repo_path, "diff_hash": observation.diff_hash},
                )
                connection.commit()
                return True
        return False

    def list_git_change_observations(self, *, repo_path: str) -> list[GitChangeObservation]:
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM git_change_observations WHERE repo_path = ? ORDER BY created_at",
                (repo_path,),
            ).fetchall()
        return [self._git_change_observation_from_row(row) for row in rows]

    def save_git_research_draft(
        self,
        *,
        repo_path: str,
        summary: str,
        bullet_candidates: list[GitResearchBullet],
        generated_from_git_diffs: bool = False,
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
            generated_from_git_diffs=generated_from_git_diffs,
            needs_review=True,
            source_commit_shas=sorted(
                {sha for bullet in bullet_candidates for sha in bullet.source_commit_shas}
            ),
            source_files=sorted(
                {path for bullet in bullet_candidates for path in bullet.source_files}
            ),
            diff_hashes=sorted(
                {value for bullet in bullet_candidates for value in bullet.diff_hashes}
            ),
            source="git",
            title=Path(repo_path).name or repo_path,
            description=summary,
            review_status="pending",
            created_at=created_at,
        )
        serialized_bullets = json.dumps(
            [
                {
                    "text": bullet.text,
                    "source_candidate_ids": bullet.source_candidate_ids,
                    "source_commit_shas": bullet.source_commit_shas,
                    "source_files": bullet.source_files,
                    "diff_hashes": bullet.diff_hashes,
                    "confidence": bullet.confidence,
                }
                for bullet in bullet_candidates
            ]
        )
        with closing(self._connection()) as connection:
            connection.execute(
                """
                INSERT INTO git_research_drafts (
                    id, repo_path, summary, bullet_candidates_json,
                    generated_from_commit_metadata, needs_review, created_at,
                    generated_from_git_diffs, source_commit_shas_json, source_files_json,
                    diff_hashes_json, source, title, description, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_path) DO UPDATE SET
                    summary = excluded.summary,
                    bullet_candidates_json = excluded.bullet_candidates_json,
                    generated_from_commit_metadata = excluded.generated_from_commit_metadata,
                    needs_review = excluded.needs_review,
                    generated_from_git_diffs = excluded.generated_from_git_diffs,
                    source_commit_shas_json = excluded.source_commit_shas_json,
                    source_files_json = excluded.source_files_json,
                    diff_hashes_json = excluded.diff_hashes_json,
                    source = excluded.source,
                    title = excluded.title,
                    description = excluded.description,
                    review_status = 'pending',
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
                    draft.generated_from_git_diffs,
                    json.dumps(draft.source_commit_shas),
                    json.dumps(draft.source_files),
                    json.dumps(draft.diff_hashes),
                    draft.source,
                    draft.title,
                    draft.description,
                    draft.review_status,
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

    def add_manual_git_research_draft(self, *, title: str, description: str) -> GitResearchDraft:
        """Persist a user-supplied project as an unapproved, review-only manual draft."""
        normalized_title = " ".join(title.split())
        normalized_description = " ".join(description.split())
        if not normalized_title or not normalized_description:
            raise ValueError("manual project title and description must not be empty")
        self.initialize()
        draft = GitResearchDraft(
            id=f"gitdraft_{uuid4().hex}",
            repo_path=f"manual:{uuid4().hex}",
            summary=normalized_description,
            bullet_candidates=[],
            generated_from_commit_metadata=False,
            generated_from_git_diffs=False,
            needs_review=True,
            source_commit_shas=[],
            source_files=[],
            diff_hashes=[],
            source="manual",
            title=normalized_title,
            description=normalized_description,
            review_status="pending",
            created_at=_now(),
        )
        with closing(self._connection()) as connection:
            connection.execute(
                """
                INSERT INTO git_research_drafts (
                    id, repo_path, summary, bullet_candidates_json,
                    generated_from_commit_metadata, needs_review, created_at,
                    generated_from_git_diffs, source_commit_shas_json, source_files_json,
                    diff_hashes_json, source, title, description, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft.id,
                    draft.repo_path,
                    draft.summary,
                    "[]",
                    False,
                    True,
                    _as_text(draft.created_at),
                    False,
                    "[]",
                    "[]",
                    "[]",
                    draft.source,
                    draft.title,
                    draft.description,
                    draft.review_status,
                ),
            )
            self._record_audit(
                connection, "git_evidence.manual_draft_added", draft.id, {"source": "manual"}
            )
            connection.commit()
        return draft

    def review_git_research_draft(
        self,
        *,
        action: str,
        draft_id: str | None,
        title: str | None = None,
        description: str | None = None,
    ) -> tuple[GitResearchDraft, int, int]:
        """Navigate or save/edit a draft without approving evidence or changing a resume."""
        if action not in {"show", "next", "back", "save", "skip", "edit"}:
            raise ValueError("review action must be show, next, back, save, skip, or edit")
        self.initialize()
        with closing(self._connection()) as connection:
            rows = connection.execute(
                "SELECT * FROM git_research_drafts WHERE review_status != 'skipped' "
                "ORDER BY created_at DESC, id DESC"
            ).fetchall()
            if not rows:
                raise ValueError("no review drafts are available")
            identifiers = [str(row["id"]) for row in rows]
            if action == "show":
                index = 0
            else:
                if draft_id not in identifiers:
                    raise ValueError("review draft does not exist")
                index = identifiers.index(draft_id)
                if action == "next":
                    index = min(index + 1, len(rows) - 1)
                elif action == "back":
                    index = max(index - 1, 0)
                elif action == "save":
                    connection.execute(
                        "UPDATE git_research_drafts SET review_status = 'saved' WHERE id = ?",
                        (draft_id,),
                    )
                elif action == "skip":
                    connection.execute(
                        "UPDATE git_research_drafts SET review_status = 'skipped' WHERE id = ?",
                        (draft_id,),
                    )
                    remaining = [row for row in rows if str(row["id"]) != draft_id]
                    if not remaining:
                        raise ValueError("no review drafts are available")
                    index = min(index, len(remaining) - 1)
                    rows = remaining
                elif action == "edit":
                    normalized_title = " ".join((title or "").split())
                    normalized_description = " ".join((description or "").split())
                    if not normalized_title or not normalized_description:
                        raise ValueError("edited draft title and description must not be empty")
                    connection.execute(
                        "UPDATE git_research_drafts SET title = ?, description = ?, "
                        "summary = ? WHERE id = ?",
                        (
                            normalized_title,
                            normalized_description,
                            normalized_description,
                            draft_id,
                        ),
                    )
                self._record_audit(
                    connection, "git_evidence.review_draft_" + action, str(draft_id), {}
                )
                connection.commit()
                rows = connection.execute(
                    "SELECT * FROM git_research_drafts WHERE review_status != 'skipped' "
                    "ORDER BY created_at DESC, id DESC"
                ).fetchall()
                identifiers = [str(row["id"]) for row in rows]
                if action in {"save", "edit"}:
                    assert draft_id is not None
                    index = identifiers.index(draft_id)
            row = rows[index]
        return self._git_research_draft_from_row(row), index + 1, len(rows)

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
    def _git_change_observation_from_row(row: sqlite3.Row) -> GitChangeObservation:
        return GitChangeObservation(
            repo_path=str(row["repo_path"]),
            commit_sha=str(row["commit_sha"]),
            files=[str(value) for value in json.loads(row["files_json"])],
            additions=int(row["additions"]),
            deletions=int(row["deletions"]),
            symbols=[str(value) for value in json.loads(row["symbols_json"])],
            change_kinds=[str(value) for value in json.loads(row["change_kinds_json"])],
            diff_hash=str(row["diff_hash"]),
        )

    @staticmethod
    def _git_research_draft_from_row(row: sqlite3.Row) -> GitResearchDraft:
        bullets = [
            GitResearchBullet(
                text=str(item["text"]),
                source_candidate_ids=[str(value) for value in item["source_candidate_ids"]],
                source_commit_shas=[str(value) for value in item["source_commit_shas"]],
                source_files=[str(value) for value in item.get("source_files", [])],
                diff_hashes=[str(value) for value in item.get("diff_hashes", [])],
                confidence=float(item.get("confidence", 0.5)),
            )
            for item in json.loads(row["bullet_candidates_json"])
        ]
        return GitResearchDraft(
            id=row["id"],
            repo_path=row["repo_path"],
            summary=row["summary"],
            bullet_candidates=bullets,
            generated_from_commit_metadata=bool(row["generated_from_commit_metadata"]),
            generated_from_git_diffs=bool(row["generated_from_git_diffs"]),
            needs_review=bool(row["needs_review"]),
            source_commit_shas=[str(value) for value in json.loads(row["source_commit_shas_json"])],
            source_files=[str(value) for value in json.loads(row["source_files_json"])],
            diff_hashes=[str(value) for value in json.loads(row["diff_hashes_json"])],
            source=str(row["source"]),
            title=str(row["title"]) or Path(str(row["repo_path"])).name,
            description=str(row["description"]) or str(row["summary"]),
            review_status=str(row["review_status"]),
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
