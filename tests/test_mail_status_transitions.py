from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.integrations.zoho import MailMessageMetadata
from erga_mcp.integrations.zoho_live import sync_metadata
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

    def test_exact_company_denial_transitions_one_active_application_and_is_audited(self) -> None:
        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="uber-denial",
                    received_at=datetime(2026, 7, 25, tzinfo=UTC),
                    sender="Talent@uber.com",
                    subject="Thanks for your interest in Uber",
                    preview="Unfortunately, we will not be moving forward.",
                )
            ],
        )

        application = self.store.list_applications()[0]
        self.assertEqual(application.status, "rejected")
        self.assertEqual(result["status_transitions"], 1)
        audits = self.store.audit_events()
        transition = next(
            item for item in audits if item.action == "application.status_updated_from_mail"
        )
        self.assertEqual(transition.payload["mail_event_id"], "uber-denial")
        self.assertEqual(transition.payload["from"], "applied")
        self.assertEqual(transition.payload["to"], "rejected")

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
                    received_at=datetime(2026, 7, 25, tzinfo=UTC),
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
                received_at=datetime(2026, 7, 25, tzinfo=UTC),
                sender="Talent@uber.com",
                subject="Thanks for your interest in Uber",
                kind="application.denial",
                confidence=0.95,
                requires_review=True,
            )
        )
        result = sync_metadata(self.store, [])
        self.assertEqual(self.store.list_applications()[0].status, "rejected")
        self.assertEqual(result["status_transitions"], 1)


if __name__ == "__main__":
    unittest.main()
