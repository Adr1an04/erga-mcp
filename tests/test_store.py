from __future__ import annotations

import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.contacts import record_recruiter_contact_from_mail


class StoreTests(unittest.TestCase):
    def test_migrates_existing_mail_events_for_bounded_receipt_identity(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "erga.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                """
                CREATE TABLE mail_events (
                    message_id TEXT PRIMARY KEY, received_at TEXT NOT NULL,
                    sender TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL,
                    confidence REAL NOT NULL, requires_review INTEGER NOT NULL,
                    sender_domain TEXT NOT NULL DEFAULT '',
                    job_urls_json TEXT NOT NULL DEFAULT '[]',
                    requisition_ids_json TEXT NOT NULL DEFAULT '[]',
                    thread_id TEXT NOT NULL DEFAULT '',
                    reference_ids_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()

            store = ErgaStore(database)
            store.initialize()
            connection = sqlite3.connect(database)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(mail_events)")}
            connection.close()

            self.assertTrue(
                {
                    "company_hint",
                    "role_hint",
                    "recruiting_cycle_hints_json",
                    "receipt_parsed",
                    "receipt_parser_version",
                }.issubset(columns)
            )

    def test_mail_event_timestamp_must_be_timezone_aware(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            event = MailEvent(
                message_id="naive-event",
                received_at=datetime(2026, 8, 13),
                sender="talent@example.test",
                subject="Application update",
                kind="application.update",
                confidence=0.9,
                requires_review=True,
            )

            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                store.record_mail_event(event)
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                store.update_mail_event_classification(event)

    def test_persists_only_bounded_recruiting_cycle_hints(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            event = MailEvent(
                message_id="cycle-evidence",
                received_at=datetime(2026, 8, 13, tzinfo=UTC),
                sender="talent@example.test",
                subject="Application received",
                kind="application.acknowledgement",
                confidence=0.9,
                requires_review=False,
                recruiting_cycle_hints=("summer 2027", "not a recruiting term"),
            )

            store.record_mail_event(event)

            self.assertEqual(
                store.list_mail_events()[0].recruiting_cycle_hints,
                ("Summer 2027",),
            )

    def test_persists_one_live_orbit_dashboard_per_discord_channel(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")

            created = store.upsert_orbit_dashboard(
                channel_id="channel-1",
                message_id="message-1",
                owner_user_id="user-1",
                cycle="Summer 2027",
                content_hash="first",
            )
            replaced = store.upsert_orbit_dashboard(
                channel_id="channel-1",
                message_id="message-2",
                owner_user_id="user-1",
                cycle="Fall 2027",
                content_hash="second",
            )

            self.assertEqual(replaced.id, created.id)
            self.assertEqual(replaced.message_id, "message-2")
            self.assertEqual(replaced.cycle, "Fall 2027")
            self.assertEqual(store.list_orbit_dashboards(), [replaced])

            refreshed = store.update_orbit_dashboard_hash(replaced.id, "third")
            self.assertEqual(refreshed.content_hash, "third")
            disabled = store.disable_orbit_dashboard(replaced.id)
            self.assertFalse(disabled.active)
            self.assertEqual(store.list_orbit_dashboards(), [])
            self.assertEqual(store.list_orbit_dashboards(active_only=False), [disabled])

    def test_skill_seeds_are_normalized_review_state_not_resume_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")

            records = store.set_skill_seeds(["Python", "fastapi", "React", "Python"])

            self.assertEqual(
                [(item.skill, item.normalized_skill) for item in records],
                [("Python", "python"), ("fastapi", "fastapi"), ("React", "react")],
            )
            self.assertTrue(all(item.checked for item in records))
            self.assertTrue(all(item.source == "self_reported" for item in records))
            self.assertEqual(store.list_evidence(), [])

            fastapi = store.set_skill_seed_checked("fastapi", checked=False)
            self.assertFalse(fastapi.checked)
            self.assertFalse(store.list_skill_seeds()[1].checked)

            added = store.add_skill_seed("PostgreSQL")
            self.assertEqual(added.normalized_skill, "postgresql")
            imported = store.add_skill_seed("Docker", source="approved_evidence")
            self.assertEqual(imported.source, "approved_evidence")
            store.remove_skill_seed(added.id)
            self.assertEqual(
                [item.normalized_skill for item in store.list_skill_seeds()],
                ["python", "fastapi", "react", "docker"],
            )
            self.assertEqual(store.list_evidence(), [])

    def test_skill_seed_set_preserves_existing_ids_and_check_state(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            original = store.set_skill_seeds(["Python", "React"])
            store.set_skill_seed_checked("react", checked=False)

            replaced = store.set_skill_seeds(["React", "FastAPI", "Python"])

            by_skill = {item.normalized_skill: item for item in replaced}
            self.assertEqual(by_skill["python"].id, original[0].id)
            self.assertEqual(by_skill["react"].id, original[1].id)
            self.assertFalse(by_skill["react"].checked)
            self.assertTrue(by_skill["fastapi"].checked)

    def test_records_evidence_and_application_with_audit_trail(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            store.initialize()

            evidence = store.add_evidence(
                source_ref="Career/Projects.md#Pipeline",
                text="Reduced manual review time by a measured amount.",
                approved=True,
            )
            application = store.create_application(
                company="Example Systems",
                role="Software Engineer",
                source_url="https://jobs.example.test/123",
                evidence_ids=[evidence.id],
            )

            self.assertEqual(application.status, "draft")
            self.assertEqual(application.evidence_ids, [evidence.id])
            self.assertEqual(store.list_applications(), [application])
            self.assertEqual(store.audit_events()[0].action, "application.created")

            updated = store.update_application_metadata(
                application.id,
                company="Correct Example Systems",
                role="Software Engineering Intern",
            )
            self.assertEqual(updated.company, "Correct Example Systems")
            self.assertEqual(updated.role, "Software Engineering Intern")
            self.assertEqual(updated.status, application.status)
            self.assertEqual(updated.evidence_ids, application.evidence_ids)
            audit_count = len(store.audit_events())
            self.assertEqual(store.audit_events()[0].action, "application.metadata_updated")
            store.update_application_metadata(
                application.id,
                company="Correct Example Systems",
                role="Software Engineering Intern",
            )
            self.assertEqual(len(store.audit_events()), audit_count)

            status = store.update_application_status(application.id, status="interview")
            self.assertEqual(status.status, "interview")
            status_audit = store.audit_events()[0]
            self.assertEqual(status_audit.action, "application.status_updated")
            self.assertEqual(status_audit.payload, {"from": "draft", "to": "interview"})

            final_round = store.update_application_status(application.id, status="final-interview")
            self.assertEqual(final_round.status, "final-interview")

            accepted = store.update_application_status(application.id, status="accepted")
            self.assertEqual(accepted.status, "accepted")

            with self.assertRaisesRegex(ValueError, "status must be one of"):
                store.update_application_status(application.id, status="submitted magically")

    def test_records_application_bound_token_usage_and_summarizes_input_and_output(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            application = store.create_application(
                company="Example Systems",
                role="Software Engineer",
                source_url="https://jobs.example.test/123",
                evidence_ids=[],
            )

            usage = store.record_token_usage(
                application_id=application.id,
                operation="deep_research",
                input_tokens=1_200,
                output_tokens=340,
                model="example-model",
            )
            store.record_token_usage(
                application_id=application.id,
                operation="resume_tailoring",
                input_tokens=800,
                output_tokens=200,
            )

            self.assertEqual(usage.application_id, application.id)
            self.assertEqual(usage.total_tokens, 1_540)
            self.assertEqual(
                store.token_usage_summary(application_id=application.id),
                {
                    "applications": 1,
                    "events": 2,
                    "input_tokens": 2_000,
                    "output_tokens": 540,
                    "total_tokens": 2_540,
                },
            )
            self.assertEqual(
                store.token_usage_summary(),
                {
                    "applications": 1,
                    "events": 2,
                    "input_tokens": 2_000,
                    "output_tokens": 540,
                    "total_tokens": 2_540,
                },
            )
            self.assertEqual(store.audit_events()[0].action, "token_usage.recorded")

            with self.assertRaisesRegex(ValueError, "input_tokens must be non-negative"):
                store.record_token_usage(
                    application_id=application.id,
                    operation="bad",
                    input_tokens=-1,
                    output_tokens=0,
                )
            for invalid_value in (1.5, True, "12"):
                with self.subTest(invalid_value=invalid_value):
                    with self.assertRaisesRegex(ValueError, "must be an integer"):
                        store.record_token_usage(
                            application_id=application.id,
                            operation="bad",
                            input_tokens=invalid_value,  # type: ignore[arg-type]
                            output_tokens=0,
                        )

    def test_detects_named_recruiter_contacts_from_application_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            event = MailEvent(
                message_id="message-1",
                received_at=datetime(2026, 7, 21, tzinfo=UTC),
                sender="Jane Smith <jane.smith@company.test>",
                subject="Interview invitation",
                kind="application.interview",
                confidence=0.99,
                requires_review=True,
            )
            store.record_mail_event(event)
            contact = record_recruiter_contact_from_mail(store, event)

            self.assertIsNotNone(contact)
            assert contact is not None
            self.assertEqual(contact.name, "Jane Smith")
            self.assertEqual(contact.email, "jane.smith@company.test")
            self.assertEqual(store.list_recruiter_contacts(), [contact])

            unnamed_event = MailEvent(
                message_id="message-2",
                received_at=datetime(2026, 7, 22, tzinfo=UTC),
                sender="alex.smith@company.test",
                subject="New role",
                kind="job.candidate",
                confidence=0.7,
                requires_review=True,
            )
            store.record_mail_event(unnamed_event)
            unnamed_contact = record_recruiter_contact_from_mail(store, unnamed_event)
            self.assertIsNotNone(unnamed_contact)
            assert unnamed_contact is not None
            self.assertIsNone(unnamed_contact.name)

            automated_event = MailEvent(
                message_id="message-3",
                received_at=datetime(2026, 7, 23, tzinfo=UTC),
                sender="recruitingnoreply@company.test",
                subject="Application update",
                kind="application.acknowledgement",
                confidence=0.98,
                requires_review=False,
            )
            store.record_mail_event(automated_event)
            self.assertIsNone(record_recruiter_contact_from_mail(store, automated_event))


if __name__ == "__main__":
    unittest.main()
