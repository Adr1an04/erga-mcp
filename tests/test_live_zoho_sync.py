from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.integrations.mail.zoho import MailMessageMetadata
from erga_mcp.integrations.mail.zoho_live import (
    fetch_all_inbox_metadata,
    fetch_inbox_metadata,
    format_recruiting_alerts,
    refresh_known_metadata,
    sync_metadata,
)
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore


class LiveZohoSyncTests(unittest.TestCase):
    def test_reads_the_configured_zoho_folder_instead_of_always_using_inbox(self) -> None:
        urls: list[str] = []
        responses = iter(
            [
                {"data": [{"accountId": "account-1"}]},
                {
                    "data": [
                        {"folderId": "inbox-1", "folderType": "Inbox", "folderName": "Inbox"},
                        {
                            "folderId": "jobs-1",
                            "folderType": "Custom",
                            "folderName": "Job Applications",
                        },
                    ]
                },
                {
                    "data": [
                        {
                            "messageId": "message-1",
                            "receivedTime": "1784556770435",
                            "fromAddress": "jobs@example.com",
                            "subject": "Application received",
                            "summary": "Thanks for applying",
                        }
                    ]
                },
            ]
        )

        class Response:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request: object, *, timeout: int) -> Response:
            self.assertEqual(timeout, 30)
            urls.append(str(getattr(request, "full_url")))
            return Response(next(responses))

        with patch("erga_mcp.integrations.mail.zoho_live.urlopen", fake_urlopen):
            messages = fetch_inbox_metadata(
                access_token="access-token", folder="Job Applications", limit=1
            )

        self.assertEqual([message.message_id for message in messages], ["message-1"])
        self.assertIn("folderId=jobs-1", urls[-1])
        self.assertNotIn("folderId=inbox-1", urls[-1])

    def test_reads_message_content_without_persisting_it(self) -> None:
        responses = iter(
            [
                {"data": [{"accountId": "account-1"}]},
                {"data": [{"folderId": "inbox-1", "folderType": "Inbox", "folderName": "Inbox"}]},
                {"data": [{"messageId": "message-1", "receivedTime": "1784556770435"}]},
                {"data": {"content": "Your application has been received."}},
            ]
        )

        class Response:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        with patch(
            "erga_mcp.integrations.mail.zoho_live.urlopen",
            lambda *_args, **_kwargs: Response(next(responses)),
        ):
            messages = fetch_inbox_metadata(
                access_token="access-token", folder="Inbox", limit=1, include_content=True
            )

        self.assertEqual(messages[0].content, "Your application has been received.")

    def test_fetches_content_only_for_unknown_messages(self) -> None:
        urls: list[str] = []
        responses = iter(
            [
                {"data": [{"accountId": "account-1"}]},
                {"data": [{"folderId": "inbox-1", "folderType": "Inbox", "folderName": "Inbox"}]},
                {
                    "data": [
                        {"messageId": "known-1", "receivedTime": "1784556770435"},
                        {"messageId": "new-1", "receivedTime": "1784556770435"},
                    ]
                },
                {"data": {"content": "New message content."}},
            ]
        )

        class Response:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request: object, *, timeout: int) -> Response:
            self.assertEqual(timeout, 30)
            urls.append(str(getattr(request, "full_url")))
            return Response(next(responses))

        with patch("erga_mcp.integrations.mail.zoho_live.urlopen", fake_urlopen):
            messages = fetch_inbox_metadata(
                access_token="access-token",
                folder="Inbox",
                limit=2,
                include_content=True,
                known_message_ids={"known-1"},
            )

        self.assertEqual(messages[0].content, "")
        self.assertEqual(messages[1].content, "New message content.")
        content_urls = [url for url in urls if url.endswith("/content")]
        self.assertEqual(len(content_urls), 1)
        self.assertIn("/new-1/content", content_urls[0])

    def test_reads_each_page_until_the_configured_folder_is_exhausted(self) -> None:
        urls: list[str] = []
        responses = iter(
            [
                {"data": [{"accountId": "account-1"}]},
                {"data": [{"folderId": "inbox-1", "folderType": "Inbox", "folderName": "Inbox"}]},
                {
                    "data": [
                        {"messageId": "m1", "receivedTime": "1784556770435"},
                        {"messageId": "m2", "receivedTime": "1784556770435"},
                    ]
                },
                {"data": [{"accountId": "account-1"}]},
                {"data": [{"folderId": "inbox-1", "folderType": "Inbox", "folderName": "Inbox"}]},
                {"data": [{"messageId": "m3", "receivedTime": "1784556770435"}]},
            ]
        )

        class Response:
            def __init__(self, payload: object) -> None:
                self.payload = payload

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(request: object, *, timeout: int) -> Response:
            self.assertEqual(timeout, 30)
            urls.append(str(getattr(request, "full_url")))
            return Response(next(responses))

        with patch("erga_mcp.integrations.mail.zoho_live.urlopen", fake_urlopen):
            messages = fetch_all_inbox_metadata(
                access_token="access-token", folder="Inbox", page_size=2
            )

        self.assertEqual([message.message_id for message in messages], ["m1", "m2", "m3"])
        message_urls = [url for url in urls if "/messages/view?" in url]
        self.assertIn("start=0", message_urls[0])
        self.assertIn("start=2", message_urls[1])

    def test_records_new_messages_once_with_application_job_and_other_categories(self) -> None:
        messages = [
            MailMessageMetadata(
                "m1",
                datetime(2026, 7, 18, tzinfo=UTC),
                "jobs@example.com",
                "Application received",
                "Thanks for applying",
            ),
            MailMessageMetadata(
                "m2",
                datetime(2026, 7, 18, tzinfo=UTC),
                "recruiter@example.com",
                "Software Engineer role",
                "I found your profile and would like to connect",
            ),
            MailMessageMetadata(
                "m3",
                datetime(2026, 7, 18, tzinfo=UTC),
                "news@example.com",
                "July newsletter",
                "Read our latest news",
            ),
        ]
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            self.assertEqual(
                sync_metadata(store, messages),
                {
                    "application": 1,
                    "job": 1,
                    "other": 1,
                    "created": 3,
                    "status_transitions": 0,
                    "alerts": [
                        {
                            "kind": "application.acknowledgement",
                            "received_at": "2026-07-18T00:00:00+00:00",
                            "sender": "jobs@example.com",
                            "subject": "Application received",
                            "requires_review": False,
                        },
                        {
                            "kind": "job.candidate",
                            "received_at": "2026-07-18T00:00:00+00:00",
                            "sender": "recruiter@example.com",
                            "subject": "Software Engineer role",
                            "requires_review": True,
                        },
                    ],
                },
            )
            self.assertEqual(sync_metadata(store, messages)["created"], 0)
            self.assertEqual(sync_metadata(store, messages)["alerts"], [])

    def test_generic_job_language_and_newsletters_do_not_become_recruiter_leads(self) -> None:
        messages = [
            MailMessageMetadata(
                "generic-position",
                datetime(2026, 7, 18, tzinfo=UTC),
                "product@example.com",
                "Position your team for success",
                "See the latest product opportunity.",
            ),
            MailMessageMetadata(
                "recruiter-newsletter",
                datetime(2026, 7, 18, tzinfo=UTC),
                "recruiter@example.com",
                "This week's hiring newsletter",
                "Recommended jobs for you. Unsubscribe here.",
            ),
        ]
        with TemporaryDirectory() as directory:
            summary = sync_metadata(ErgaStore(Path(directory) / "erga.sqlite3"), messages)

        self.assertEqual(summary["job"], 0)
        self.assertEqual(summary["other"], 2)
        self.assertEqual(summary["alerts"], [])

    def test_application_footer_does_not_hide_real_receipt_and_retains_only_identity(self) -> None:
        message = MailMessageMetadata(
            "example-software-receipt",
            datetime(2026, 8, 25, tzinfo=UTC),
            "Example Software Careers <donotreply@email.careers.example.test>",
            "Thank you for your application!",
            "",
            content=(
                "Thank you for taking the time to submit your application for AI Software "
                "Engineering Intern - Edge (Job number: 900050373). Unsubscribe"
            ),
        )
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            summary = sync_metadata(store, [message])
            retained = store.list_mail_events()[0]

            self.assertEqual(summary["application"], 1)
            self.assertEqual(retained.kind, "application.acknowledgement")
            self.assertEqual(retained.company_hint, "Example Software")
            self.assertEqual(retained.role_hint, "AI Software Engineering Intern - Edge")
            self.assertEqual(retained.requisition_ids, ("900050373",))
            self.assertNotIn("Unsubscribe", str(retained))

    def test_candidate_account_password_reset_is_not_an_application(self) -> None:
        message = MailMessageMetadata(
            "candidate-password-reset",
            datetime(2026, 8, 25, tzinfo=UTC),
            "acme-gpu@otp.workday.com",
            "Reset your password for your candidate account",
            "Use this code to reset your account password.",
        )
        with TemporaryDirectory() as directory:
            summary = sync_metadata(ErgaStore(Path(directory) / "erga.sqlite3"), [message])

        self.assertEqual(summary["application"], 0)
        self.assertEqual(summary["other"], 1)

    def test_metadata_only_refresh_does_not_downgrade_a_known_decision(self) -> None:
        message = MailMessageMetadata(
            "known-decision",
            datetime(2026, 8, 25, tzinfo=UTC),
            "recruiting@example.test",
            "Thank you for applying",
            "",
        )
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            store.record_mail_event(
                MailEvent(
                    message_id=message.message_id,
                    received_at=message.received_at,
                    sender=message.sender,
                    subject=message.subject,
                    kind="application.denial",
                    confidence=0.95,
                    requires_review=True,
                )
            )

            refresh_known_metadata(store, [message])

            retained = store.list_mail_events()[0]
            self.assertEqual(retained.kind, "application.denial")
            self.assertTrue(retained.requires_review)

    def test_oa_and_interview_survive_normal_email_footers(self) -> None:
        messages = [
            MailMessageMetadata(
                "oa",
                datetime(2026, 8, 25, tzinfo=UTC),
                "recruiting@example.test",
                "Complete your coding challenge invitation",
                "Your assessment link is ready. Unsubscribe from optional recruiting news.",
            ),
            MailMessageMetadata(
                "interview",
                datetime(2026, 8, 25, tzinfo=UTC),
                "recruiting@example.test",
                "Choose an interview time",
                "Share your interview availability. Unsubscribe from optional recruiting news.",
            ),
        ]
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            summary = sync_metadata(store, messages)
            kinds = {event.kind for event in store.list_mail_events()}

        self.assertEqual(summary["application"], 2)
        self.assertEqual(kinds, {"application.assessment", "application.interview"})

    def test_reclassifies_existing_messages_when_rules_improve(self) -> None:
        message = MailMessageMetadata(
            "tesla-1",
            datetime(2026, 7, 12, tzinfo=UTC),
            "noreply@tesla.com",
            "Adrian, thank you for your interest in Tesla",
            "",
        )
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            store.record_mail_event(
                MailEvent(
                    message_id=message.message_id,
                    received_at=message.received_at,
                    sender=message.sender,
                    subject=message.subject,
                    kind="other",
                    confidence=0.0,
                    requires_review=False,
                )
            )

            summary = sync_metadata(store, [message])

            self.assertEqual(summary["created"], 0)
            self.assertEqual(store.list_mail_events()[0].kind, "application.acknowledgement")

    def test_renders_only_new_relevant_mail_with_source_and_subject(self) -> None:
        message = MailMessageMetadata(
            "m1",
            datetime(2026, 7, 18, tzinfo=UTC),
            "recruiting@acme.example",
            "Online assessment invitation",
            "Complete the coding test.",
        )
        with TemporaryDirectory() as directory:
            summary = sync_metadata(ErgaStore(Path(directory) / "erga.sqlite3"), [message])

        self.assertEqual(
            summary["alerts"],
            [
                {
                    "kind": "application.assessment",
                    "received_at": "2026-07-18T00:00:00+00:00",
                    "sender": "recruiting@acme.example",
                    "subject": "Online assessment invitation",
                    "requires_review": True,
                }
            ],
        )
        self.assertEqual(
            format_recruiting_alerts(summary["alerts"]),
            "[Recruiting inbox update]\n\n"
            "Assessment invitation — needs review\n"
            "Received: 2026-07-18T00:00:00+00:00\n"
            "From: recruiting@acme.example\n"
            "Subject: Online assessment invitation",
        )


if __name__ == "__main__":
    unittest.main()
