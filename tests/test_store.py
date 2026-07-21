from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import MailEvent
from erga_mcp.recruiter_contacts import record_recruiter_contact_from_mail
from erga_mcp.store import ErgaStore


class StoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
