from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.integrations.mail.gmail import parse_message_metadata
from erga_mcp.integrations.mail.zoho import MailMessageMetadata
from erga_mcp.integrations.mail.zoho_live import sync_metadata
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.mail_reconciliation import (
    opaque_identifier_digest,
    reconcile_mail_events,
    resolve_mail_reconciliation,
)


class MailReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = ErgaStore(Path(self.directory.name) / "state.db")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _application(
        self,
        *,
        company: str = "Example Labs",
        role: str = "Software Engineering Intern",
        source_url: str = "https://jobs.ashbyhq.com/example-labs/req-4242",
        status: str = "applied",
    ) -> str:
        application = self.store.create_application(
            company=company,
            role=role,
            source_url=source_url,
            evidence_ids=[],
        )
        if status != "draft":
            self.store.update_application_status(application.id, status=status)
        return application.id

    def test_exact_canonical_job_url_selects_one_of_two_company_roles(self) -> None:
        software_id = self._application()
        data_id = self._application(
            role="Data Engineering Intern",
            source_url="https://jobs.ashbyhq.com/example-labs/req-9999",
        )

        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="decision-4242",
                    received_at=datetime.now(UTC),
                    sender="talent@examplelabs.test",
                    subject="Update on your application",
                    preview="We will not be moving forward.",
                    content=(
                        "Posting: https://jobs.ashbyhq.com/example-labs/req-4242?utm_source=mail"
                    ),
                )
            ],
        )

        statuses = {item.id: item.status for item in self.store.list_applications()}
        self.assertEqual(statuses[software_id], "applied")
        self.assertEqual(statuses[data_id], "applied")
        self.assertEqual(result["status_transitions"], 0)
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reconciliation.state, "review")
        self.assertIsNone(reconciliation.matched_application_id)
        self.assertEqual(reconciliation.confidence, "high")
        self.assertIn("canonical_job_url", reconciliation.provenance)
        resolve_mail_reconciliation(
            self.store,
            reconciliation.id,
            action="match",
            application_id=software_id,
        )
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[software_id],
            "rejected",
        )

    def test_same_company_without_role_or_identifier_stays_reviewable(self) -> None:
        first_id = self._application()
        second_id = self._application(
            role="Data Engineering Intern",
            source_url="https://jobs.ashbyhq.com/example-labs/req-9999",
        )

        result = sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="ambiguous-example",
                    received_at=datetime.now(UTC),
                    sender="talent@examplelabs.test",
                    subject="Example Labs application update",
                    preview="We will not be moving forward.",
                )
            ],
        )

        self.assertEqual(result["status_transitions"], 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()},
            {first_id: "applied", second_id: "applied"},
        )
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reconciliation.state, "review")
        self.assertIsNone(reconciliation.matched_application_id)
        self.assertEqual(
            {candidate.application_id for candidate in reconciliation.candidates},
            {first_id, second_id},
        )

    def test_requisition_and_role_signals_disambiguate_same_company(self) -> None:
        software_id = self._application()
        data_id = self._application(
            role="Data Engineering Intern",
            source_url="https://jobs.ashbyhq.com/example-labs/req-9999",
        )

        sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="oa-9999",
                    received_at=datetime.now(UTC),
                    sender="no-reply@ashbyhq.com",
                    subject="Data Engineering Intern assessment — requisition REQ-9999",
                    preview="You are invited to complete an online assessment.",
                )
            ],
        )

        statuses = {item.id: item.status for item in self.store.list_applications()}
        self.assertEqual(statuses[software_id], "applied")
        self.assertEqual(statuses[data_id], "applied")
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertIsNone(reconciliation.matched_application_id)
        self.assertIn("requisition_id", reconciliation.provenance)
        self.assertIn("role_tokens", reconciliation.provenance)
        resolve_mail_reconciliation(
            self.store, reconciliation.id, action="match", application_id=data_id
        )
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[data_id],
            "oa",
        )

    def test_sender_domain_requires_a_dns_label_not_company_substring(self) -> None:
        application_id = self._application(
            company="Uber",
            source_url="https://jobs.ashbyhq.com/uber/req-4242",
            status="draft",
        )
        event = MailEvent(
            message_id="not-uber",
            received_at=datetime.now(UTC),
            sender="talent@notuber.com",
            subject="Software Engineering Intern update req-4242",
            kind="applied",
            confidence=0.99,
            requires_review=False,
            sender_domain="notuber.com",
            requisition_ids=("req-4242",),
        )
        self.store.record_mail_event(event)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "draft",
        )
        self.assertEqual(self.store.list_mail_reconciliations()[0].state, "ignored")

    def test_historical_unmatched_event_is_retried_after_application_creation(self) -> None:
        event = MailEvent(
            message_id="historical-4242",
            received_at=datetime.now(UTC) + timedelta(minutes=1),
            sender="talent@examplelabs.test",
            subject="Example Labs assessment invitation",
            kind="application.assessment",
            confidence=0.96,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
            requisition_ids=("req-4242",),
        )
        self.store.record_mail_event(event)

        first = reconcile_mail_events(self.store, self.store.list_mail_events())
        self.assertEqual(first.unmatched, 1)
        self.assertEqual(self.store.list_mail_reconciliations()[0].state, "unmatched")

        application_id = self._application(status="applied")
        second = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(second.transitions, 1)
        self.assertEqual(self.store.list_applications()[0].status, "oa")
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reconciliation.state, "matched")
        self.assertEqual(reconciliation.matched_application_id, application_id)
        self.assertNotEqual(first.application_fingerprint, second.application_fingerprint)

    def test_thread_identifier_links_follow_up_without_company_tokens(self) -> None:
        application_id = self._application()
        first = MailEvent(
            message_id="thread-first",
            received_at=datetime.now(UTC) + timedelta(hours=1),
            sender="recruiting@ats.test",
            subject="Application received",
            kind="application.acknowledgement",
            confidence=0.95,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
            thread_id="thread-17",
            reference_ids=("message-rfc-1",),
        )
        second = MailEvent(
            message_id="thread-second",
            received_at=datetime.now(UTC) + timedelta(hours=2),
            sender="recruiting@ats.test",
            subject="Next steps",
            kind="application.assessment",
            confidence=0.95,
            requires_review=False,
            thread_id="thread-17",
            reference_ids=("message-rfc-1", "message-rfc-2"),
        )
        self.store.record_mail_event(first)
        self.store.record_mail_event(second)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 1)
        self.assertEqual(self.store.list_applications()[0].status, "oa")
        follow_up = next(
            item
            for item in self.store.list_mail_reconciliations()
            if item.message_id == "thread-second"
        )
        self.assertEqual(follow_up.matched_application_id, application_id)
        self.assertTrue({"thread_id", "reference_id"}.intersection(follow_up.provenance))

    def test_gmail_parser_retains_only_sanitized_thread_reference_metadata(self) -> None:
        parsed = parse_message_metadata(
            {
                "id": "provider-message",
                "threadId": "provider-thread",
                "internalDate": "1760000000000",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "recruiting@example.test"},
                        {"name": "Subject", "value": "Next steps"},
                        {"name": "Message-ID", "value": "<current@example.test>"},
                        {"name": "In-Reply-To", "value": "<previous@example.test>"},
                        {
                            "name": "References",
                            "value": "<old@example.test> <previous@example.test>",
                        },
                    ]
                },
                "snippet": "Private preview remains transient.",
            }
        )

        self.assertEqual(parsed.thread_id, opaque_identifier_digest("provider-thread"))
        self.assertEqual(
            parsed.reference_ids,
            tuple(
                sorted(
                    opaque_identifier_digest(value)
                    for value in (
                        "current@example.test",
                        "old@example.test",
                        "previous@example.test",
                    )
                )
            ),
        )

    def test_newest_mail_wins_even_when_input_order_is_reversed(self) -> None:
        application_id = self._application()
        job_url = "https://jobs.ashbyhq.com/example-labs/req-4242"
        now = datetime.now(UTC)
        events = [
            MailEvent(
                message_id="newer-interview",
                received_at=now + timedelta(hours=2),
                sender="talent@examplelabs.test",
                subject="Interview invitation",
                kind="application.interview",
                confidence=0.99,
                requires_review=False,
                job_urls=(job_url,),
            ),
            MailEvent(
                message_id="older-denial",
                received_at=now + timedelta(hours=1),
                sender="talent@examplelabs.test",
                subject="Application decision",
                kind="application.denial",
                confidence=0.99,
                requires_review=False,
                job_urls=(job_url,),
            ),
        ]
        for event in events:
            self.store.record_mail_event(event)

        result = reconcile_mail_events(self.store, list(reversed(events)))

        self.assertEqual(result.transitions, 1)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "interview",
        )
        older = next(
            item
            for item in self.store.list_mail_reconciliations()
            if item.message_id == "older-denial"
        )
        self.assertEqual(older.reason, "older_than_latest_mail_transition")

    def test_event_before_application_creation_never_mutates_new_application(self) -> None:
        event = MailEvent(
            message_id="historical-old-cycle",
            received_at=datetime(2024, 1, 1, tzinfo=UTC),
            sender="talent@examplelabs.test",
            subject="Application decision",
            kind="application.denial",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(event)
        application_id = self._application()

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "applied",
        )
        reconciliation = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reconciliation.state, "review")
        self.assertEqual(reconciliation.reason, "event_predates_application")
        resolved = resolve_mail_reconciliation(
            self.store,
            reconciliation.id,
            action="match",
            application_id=application_id,
        )
        self.assertEqual(resolved.matched_application_id, application_id)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "applied",
        )

    def test_trial_security_newsletter_is_ignored_before_denial_language(self) -> None:
        application_id = self._application(company="Uber")

        sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="trial-ended",
                    received_at=datetime.now(UTC),
                    sender="security@uber.test",
                    subject="Security alert",
                    preview="Unfortunately, your Uber trial has ended. Unsubscribe here.",
                )
            ],
        )

        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "applied",
        )
        self.assertEqual(self.store.list_mail_events()[0].kind, "other")
        self.assertEqual(self.store.list_mail_reconciliations()[0].reason, "non_status_event")

    def test_preview_tracking_token_and_raw_identifiers_never_reach_database(self) -> None:
        self._application()
        secret = "private-preview-phrase-and-token"
        sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="privacy-event",
                    received_at=datetime.now(UTC),
                    sender="talent@examplelabs.test",
                    subject="Application received",
                    preview=secret,
                    content=("https://jobs.ashbyhq.com/example-labs/req-4242?token=" + secret),
                    thread_id="raw-provider-thread",
                    reference_ids=("raw-reference-id",),
                )
            ],
        )

        database = self.store.database_path.read_bytes()
        self.assertNotIn(secret.encode(), database)
        self.assertNotIn(b"raw-provider-thread", database)
        self.assertNotIn(b"raw-reference-id", database)
        stored = self.store.list_mail_events()[0]
        self.assertEqual(stored.job_urls, ("https://jobs.ashbyhq.com/example-labs/req-4242",))
        self.assertTrue(stored.thread_id.startswith("sha256:"))

    def test_explicit_resolution_validates_membership_and_is_idempotent(self) -> None:
        application_id = self._application()
        sync_metadata(
            self.store,
            [
                MailMessageMetadata(
                    message_id="review-membership",
                    received_at=datetime.now(UTC),
                    sender="talent@examplelabs.test",
                    subject="Example Labs interview invitation",
                    preview="Schedule your interview.",
                    content="https://jobs.ashbyhq.com/example-labs/req-4242",
                )
            ],
        )
        review = self.store.list_mail_reconciliations()[0]

        with self.assertRaisesRegex(ValueError, "one of this review's candidates"):
            resolve_mail_reconciliation(
                self.store,
                review.id,
                action="match",
                application_id="app_not_a_candidate",
            )
        first = resolve_mail_reconciliation(
            self.store,
            review.id,
            action="match",
            application_id=application_id,
        )
        second = resolve_mail_reconciliation(
            self.store,
            review.id,
            action="match",
            application_id=application_id,
        )

        self.assertEqual(first, second)
        self.assertEqual(self.store.list_applications()[0].status, "interview")
        resolved_audits = [
            item
            for item in self.store.audit_events()
            if item.action == "mail_reconciliation.resolved"
        ]
        self.assertEqual(len(resolved_audits), 1)

    def test_reclassification_reopens_automatic_ignore_but_not_explicit_ignore(self) -> None:
        application = self.store.create_application(
            company="Example Labs",
            role="Software Engineering Intern",
            source_url="https://jobs.ashbyhq.com/example-labs/req-4242",
            evidence_ids=[],
        )
        event = MailEvent(
            message_id="reclassified-event",
            received_at=datetime.now(UTC),
            sender="talent@examplelabs.test",
            subject="Routine update",
            kind="other",
            confidence=0.0,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(event)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        self.assertEqual(self.store.list_mail_reconciliations()[0].reason, "non_status_event")

        self.store.update_mail_event_classification(
            MailEvent(
                **{
                    **event.__dict__,
                    "kind": "application.acknowledgement",
                    "confidence": 0.95,
                }
            )
        )
        reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(self.store.list_applications()[0].status, "applied")
        reopened = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reopened.matched_application_id, application.id)
        self.assertEqual(reopened.state, "matched")

    def test_manual_update_after_event_blocks_later_automatic_reconciliation(self) -> None:
        application_id = self._application()
        event = MailEvent(
            message_id="before-manual-update",
            received_at=datetime.now(UTC),
            sender="talent@examplelabs.test",
            subject="Interview invitation",
            kind="application.interview",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(event)
        self.store.update_application_status(application_id, status="oa")

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(self.store.list_applications()[0].status, "oa")
        self.assertEqual(
            self.store.list_mail_reconciliations()[0].reason,
            "older_than_manual_status_update",
        )

    def test_concurrent_different_resolution_choices_allow_only_one_winner(self) -> None:
        first_id = self._application()
        second_id = self._application(
            role="Data Engineering Intern",
            source_url="https://jobs.ashbyhq.com/example-labs/req-9999",
        )
        event = MailEvent(
            message_id="concurrent-review",
            received_at=datetime.now(UTC),
            sender="talent@examplelabs.test",
            subject="Example Labs interview invitation",
            kind="application.interview",
            confidence=0.98,
            requires_review=True,
        )
        self.store.record_mail_event(event)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        review = self.store.list_mail_reconciliations()[0]

        def choose(application_id: str) -> str:
            try:
                resolved = resolve_mail_reconciliation(
                    self.store,
                    review.id,
                    action="match",
                    application_id=application_id,
                )
                return f"resolved:{resolved.matched_application_id}"
            except ValueError as exc:
                return f"error:{exc}"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(choose, (first_id, second_id)))

        self.assertEqual(sum(value.startswith("resolved:") for value in outcomes), 1)
        self.assertEqual(sum(value.startswith("error:") for value in outcomes), 1)
        resolved = self.store.list_mail_reconciliations()[0]
        self.assertIn(resolved.matched_application_id, {first_id, second_id})
        statuses = {item.id: item.status for item in self.store.list_applications()}
        self.assertEqual(sum(status == "interview" for status in statuses.values()), 1)

    def test_explicit_resolution_wins_a_race_with_automatic_reconciliation(self) -> None:
        first_id = self._application()
        second_id = self._application(
            source_url="https://jobs.ashbyhq.com/example-labs/req-9999",
        )
        event = MailEvent(
            message_id="sync-resolution-race",
            received_at=datetime.now(UTC),
            sender="talent@examplelabs.test",
            subject="Example Labs interview invitation",
            kind="application.interview",
            confidence=0.99,
            requires_review=True,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(event)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        review = self.store.list_mail_reconciliations()[0]
        self.store.update_mail_event_classification(
            MailEvent(**{**event.__dict__, "requires_review": False})
        )
        original = self.store.upsert_mail_reconciliation
        raced = False

        def resolve_during_upsert(reconciliation):
            nonlocal raced
            if reconciliation.state == "automatic_pending" and not raced:
                raced = True
                resolve_mail_reconciliation(
                    self.store,
                    review.id,
                    action="match",
                    application_id=second_id,
                )
            return original(reconciliation)

        with patch.object(
            self.store,
            "upsert_mail_reconciliation",
            side_effect=resolve_during_upsert,
        ):
            reconcile_mail_events(self.store, self.store.list_mail_events())

        statuses = {item.id: item.status for item in self.store.list_applications()}
        self.assertEqual(statuses[first_id], "applied")
        self.assertEqual(statuses[second_id], "interview")

    def test_future_provider_timestamp_never_creates_a_status_watermark(self) -> None:
        application_id = self._application()
        future = MailEvent(
            message_id="future-interview",
            received_at=datetime(2099, 1, 1, tzinfo=UTC),
            sender="talent@examplelabs.test",
            subject="Example Labs interview invitation",
            kind="application.interview",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        legitimate = MailEvent(
            **{
                **future.__dict__,
                "message_id": "legitimate-offer",
                "received_at": datetime.now(UTC) + timedelta(minutes=1),
                "kind": "application.offer",
                "subject": "Example Labs offer",
            }
        )
        self.store.record_mail_event(future)
        self.store.record_mail_event(legitimate)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 1)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "offer",
        )
        future_review = next(
            item
            for item in self.store.list_mail_reconciliations()
            if item.message_id == "future-interview"
        )
        self.assertEqual(future_review.reason, "provider_timestamp_in_future")

    def test_shared_ats_requisition_without_company_identity_stays_review_only(self) -> None:
        application_id = self._application(status="draft")
        event = MailEvent(
            message_id="shared-ats-requisition",
            received_at=datetime.now(UTC),
            sender="no-reply@ashbyhq.com",
            subject="Software Engineering Intern application received — req-4242",
            kind="application.acknowledgement",
            confidence=0.99,
            requires_review=False,
            requisition_ids=("req-4242",),
        )
        self.store.record_mail_event(event)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "draft",
        )
        self.assertEqual(self.store.list_mail_reconciliations()[0].state, "review")

    def test_conflicting_query_requisition_vetoes_generic_canonical_url_match(self) -> None:
        application_id = self._application(
            source_url="https://jobs.example.test/careers/job?job_id=111"
        )
        event = MailEvent(
            message_id="conflicting-query-requisition",
            received_at=datetime.now(UTC),
            sender="talent@example.test",
            subject="Software Engineering Intern assessment",
            kind="application.assessment",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.example.test/careers/job",),
            requisition_ids=("222",),
        )
        self.store.record_mail_event(event)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "applied",
        )
        review = self.store.list_mail_reconciliations()[0]
        self.assertEqual(review.state, "review")
        self.assertIn("conflicting_requisition_id", review.provenance)

    def test_conflicting_requisition_forces_review_even_with_prior_thread_link(self) -> None:
        application_id = self._application(
            source_url="https://jobs.example.test/careers/job?job_id=111"
        )
        first = MailEvent(
            message_id="thread-req-111",
            received_at=datetime.now(UTC),
            sender="talent@example.test",
            subject="Example Labs application received",
            kind="application.acknowledgement",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.example.test/careers/job",),
            requisition_ids=("111",),
            thread_id="shared-thread",
        )
        second = MailEvent(
            **{
                **first.__dict__,
                "message_id": "thread-req-222",
                "received_at": datetime.now(UTC) + timedelta(minutes=1),
                "kind": "application.assessment",
                "subject": "Assessment invitation",
                "requisition_ids": ("222",),
            }
        )
        self.store.record_mail_event(first)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        self.store.record_mail_event(second)

        result = reconcile_mail_events(self.store, self.store.list_mail_events())

        self.assertEqual(result.transitions, 0)
        self.assertEqual(
            {item.id: item.status for item in self.store.list_applications()}[application_id],
            "applied",
        )
        review = next(
            item
            for item in self.store.list_mail_reconciliations()
            if item.message_id == "thread-req-222"
        )
        self.assertEqual(review.state, "review")
        self.assertIn("conflicting_requisition_id", review.provenance)

    def test_store_boundary_strips_tracking_tokens_and_invalid_requisition_ids(self) -> None:
        self.store.record_mail_event(
            MailEvent(
                message_id="store-privacy",
                received_at=datetime.now(UTC),
                sender="talent@example.test",
                subject="Application update",
                kind="application.acknowledgement",
                confidence=0.99,
                requires_review=False,
                job_urls=(
                    "https://jobs.example.test/jobs/req-4242?token=PRIVATE_TRACKING_TOKEN_123",
                ),
                requisition_ids=("req-4242", "not-valid"),
            )
        )

        retained = self.store.list_mail_events()[0]

        self.assertEqual(retained.job_urls, ("https://jobs.example.test/jobs/req-4242",))
        self.assertEqual(retained.requisition_ids, ("req-4242",))

    def test_store_boundary_drops_arbitrary_non_job_urls(self) -> None:
        self.store.record_mail_event(
            MailEvent(
                message_id="store-private-url",
                received_at=datetime.now(UTC),
                sender="talent@example.test",
                subject="Application update",
                kind="application.acknowledgement",
                confidence=0.99,
                requires_review=False,
                job_urls=("https://private.example.test/users/alice?token=secret",),
            )
        )

        self.assertEqual(self.store.list_mail_events()[0].job_urls, ())

    def test_explicit_future_match_associates_without_poisoning_status_watermark(self) -> None:
        application_id = self._application()
        future = MailEvent(
            message_id="future-explicit",
            received_at=datetime(2099, 1, 1, tzinfo=UTC),
            sender="talent@examplelabs.test",
            subject="Example Labs interview invitation",
            kind="application.interview",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(future)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        review = self.store.list_mail_reconciliations()[0]

        resolved = resolve_mail_reconciliation(
            self.store, review.id, action="match", application_id=application_id
        )

        self.assertEqual(resolved.reason, "explicit_match_future_event_no_status_change")
        self.assertEqual(self.store.list_applications()[0].status, "applied")

    def test_classification_change_reopens_prior_automatic_match_for_review(self) -> None:
        self._application(status="draft")
        event = MailEvent(
            message_id="classification-correction",
            received_at=datetime.now(UTC),
            sender="talent@examplelabs.test",
            subject="Example Labs application received",
            kind="application.acknowledgement",
            confidence=0.99,
            requires_review=False,
            job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
        )
        self.store.record_mail_event(event)
        reconcile_mail_events(self.store, self.store.list_mail_events())
        self.assertEqual(self.store.list_applications()[0].status, "applied")
        self.store.update_mail_event_classification(
            MailEvent(
                **{
                    **event.__dict__,
                    "kind": "application.denial",
                    "requires_review": True,
                }
            )
        )

        reconcile_mail_events(self.store, self.store.list_mail_events())

        reopened = self.store.list_mail_reconciliations()[0]
        self.assertEqual(reopened.state, "review")
        self.assertEqual(reopened.event_kind, "application.denial")
        self.assertEqual(self.store.list_applications()[0].status, "applied")


if __name__ == "__main__":
    unittest.main()
