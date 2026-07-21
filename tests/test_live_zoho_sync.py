from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.integrations.zoho import MailMessageMetadata
from erga_mcp.integrations.zoho_live import (
    fetch_all_inbox_metadata,
    fetch_inbox_metadata,
    format_recruiting_alerts,
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

        with patch("erga_mcp.integrations.zoho_live.urlopen", fake_urlopen):
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
            "erga_mcp.integrations.zoho_live.urlopen",
            lambda *_args, **_kwargs: Response(next(responses)),
        ):
            messages = fetch_inbox_metadata(
                access_token="access-token", folder="Inbox", limit=1, include_content=True
            )

        self.assertEqual(messages[0].content, "Your application has been received.")

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

        with patch("erga_mcp.integrations.zoho_live.urlopen", fake_urlopen):
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
