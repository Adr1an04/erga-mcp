from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.integrations.mail.zoho import MailMessageMetadata
from erga_mcp.integrations.mail.zoho_live import sync_metadata
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore


class MailStatusTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = ErgaStore(Path(self.directory.name) / "state.db")
        evidence = self.store.add_evidence(
            source_ref="Career.md", text="Built software.", approved=True
        )
        self.application = self.store.create_application(
            company="Uber",
            role="Software Engineering Intern",
            source_url="https://jobs.uber.com/en/jobs/300697",
            evidence_ids=[evidence.id],
        )
        self.store.update_application_status(self.application.id, status="applied")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_exact_company_denial_requires_review_and_does_not_mutate(self) -> None:
        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="uber-denial",
                    received_at=datetime.now(UTC),
                    sender="Talent@uber.com",
                    subject="Thanks for your interest in Uber",
                    preview="Unfortunately, we will not be moving forward.",
                )
            ],
        )

        application = self.store.list_applications()[0]
        self.assertEqual(application.status, "applied")
        self.assertEqual(result["status_transitions"], 0)
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reconciliation.state, "review")
        self.assertEqual(reconciliation.reason, "classification_requires_review")

    def test_ambiguous_company_match_does_not_change_any_status(self) -> None:
        evidence = self.store.list_evidence()[0]
        self.store.create_application(
            company="Uber",
            role="Data Engineering Intern",
            source_url="https://jobs.uber.com/en/jobs/other",
            evidence_ids=[evidence.id],
        )

        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="ambiguous-denial",
                    received_at=datetime.now(UTC),
                    sender="Talent@uber.com",
                    subject="Thanks for your interest in Uber",
                    preview="Unfortunately, we will not be moving forward.",
                )
            ],
        )

        self.assertEqual(result["status_transitions"], 0)
        self.assertEqual(
            [item.status for item in self.store.list_applications()], ["applied", "draft"]
        )

    def test_reconciles_an_existing_exact_match_on_a_later_sync(self) -> None:
        self.store.record_mail_event(
            MailEvent(
                message_id="stored-uber-denial",
                received_at=datetime.now(UTC),
                sender="Talent@uber.com",
                subject="Thanks for your interest in Uber",
                kind="application.denial",
                confidence=0.95,
                requires_review=False,
                job_urls=("https://jobs.uber.com/en/jobs/300697",),
            )
        )
        result = sync_metadata(self.store, [])
        self.assertEqual(self.store.list_applications()[0].status, "rejected")
        self.assertEqual(result["status_transitions"], 1)

    def test_processed_mail_event_does_not_override_a_later_manual_status_change(self) -> None:
        message = MailMessageMetadata(
            message_id="processed-uber-denial",
            received_at=datetime.now(UTC),
            sender="Talent@uber.com",
            subject="Thanks for your interest in Uber",
            preview="Unfortunately, we will not be moving forward.",
        )
        older_message = MailMessageMetadata(
            message_id="second-recorded-uber-denial",
            received_at=datetime.now(UTC),
            sender="Talent@uber.com",
            subject="Update on your Uber application",
            preview="Unfortunately, we will not be moving forward.",
        )
        first = sync_metadata(self.store, [message, older_message])
        self.assertEqual(first["status_transitions"], 0)
        self.store.update_application_status(self.application.id, status="oa")

        replay = sync_metadata(self.store, [])

        self.assertEqual(replay["status_transitions"], 0)
        self.assertEqual(self.store.list_applications()[0].status, "oa")

    def test_interview_mail_does_not_regress_a_numbered_interview_stage(self) -> None:
        self.store.update_application_status(self.application.id, status="interview-2")

        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="generic-interview",
                    received_at=datetime.now(UTC),
                    sender="Talent@uber.com",
                    subject="Your Uber technical interview",
                    preview="We invite you to interview.",
                )
            ],
        )

        self.assertEqual(result["status_transitions"], 0)
        self.assertEqual(self.store.list_applications()[0].status, "interview-2")


if __name__ == "__main__":
    unittest.main()
