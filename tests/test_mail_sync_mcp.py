from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.integrations.mail.zoho import MailMessageMetadata
from erga_mcp.mcp.server import build_server
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore


class MailSyncMcpTests(unittest.TestCase):
    def test_recovers_previously_missed_receipt_with_one_bounded_body_fetch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"')
                .replace('client_id = ""', 'client_id = "test-client"')
                .replace('folder = "Job Applications"', 'folder = "Inbox"'),
                encoding="utf-8",
            )
            store = ErgaStore(root / "state" / "erga.sqlite3")
            message = MailMessageMetadata(
                message_id="historical-example-software",
                received_at=datetime(2026, 8, 25, tzinfo=UTC),
                sender="Example Software Careers <donotreply@email.careers.example.test>",
                subject="Thank you for your application!",
                preview="",
                content=(
                    "Thank you for taking the time to submit your application for AI Software "
                    "Engineering Intern - Edge (Job number: 900050373). Unsubscribe"
                ),
            )
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
            with (
                patch(
                    "erga_mcp.integrations.mail.provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail.provider.fetch_all_inbox_metadata",
                    return_value=[message],
                ) as fetch,
            ):
                result: Any = asyncio.run(
                    build_server(config_path).call_tool("sync_recruiting_mail", {})
                )
            payload = cast(dict[str, Any], result.structured_content)
            retained = store.list_mail_events()[0]

            self.assertEqual(payload["created"], 0)
            self.assertEqual(payload["recruiting_events"], 1)
            self.assertEqual(payload["historical_events_promoted"], 1)
            self.assertEqual(retained.kind, "application.acknowledgement")
            self.assertEqual(retained.role_hint, "AI Software Engineering Intern - Edge")
            self.assertTrue(retained.receipt_parsed)
            self.assertEqual(fetch.call_args.kwargs["known_message_ids"], set())

    def test_projection_failure_reports_sanitized_warning_after_canonical_sync(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"')
                .replace('client_id = ""', 'client_id = "test-client"')
                .replace(
                    'enabled = false\ntracker_dir = ""', 'enabled = true\ntracker_dir = "tracker"'
                ),
                encoding="utf-8",
            )
            message = MailMessageMetadata(
                message_id="projection-failure",
                received_at=datetime.now(UTC),
                sender="talent@example.test",
                subject="Application received",
                preview="We received your application.",
            )
            sentinel = "/Users/private/Vault/Secret.md"
            with (
                patch(
                    "erga_mcp.integrations.mail.provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail.provider.fetch_all_inbox_metadata",
                    return_value=[message],
                ),
                patch(
                    "erga_mcp.mcp.workspace_tools.reconcile_application_status_tracker_rows",
                    side_effect=OSError(f"permission denied: {sentinel}"),
                ),
                patch(
                    "erga_mcp.mcp.workspace_tools.project_recruiter_contacts",
                    side_effect=OSError(f"permission denied: {sentinel}"),
                ),
            ):
                result: Any = asyncio.run(
                    build_server(config_path).call_tool("sync_recruiting_mail", {})
                )
            payload = cast(dict[str, Any], result.structured_content)
            retained = ErgaStore(root / "state" / "erga.sqlite3").list_mail_events()

        self.assertEqual(len(retained), 1)
        self.assertEqual(payload["created"], 1)
        self.assertEqual(len(payload["warnings"]), 2)
        self.assertNotIn(sentinel, str(payload))

    def test_mail_status_transition_is_mirrored_to_the_exact_tracker_row(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            job_url = "https://jobs.uber.com/en/jobs/300697"
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            tracker_path.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                f"| Uber | Software Engineer Intern | Remote | [Posting]({job_url}) | "
                "Applied | 2026-07-20 | Await acknowledgement or recruiting update. | Note |\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"')
                .replace('client_id = ""', 'client_id = "test-client"')
                .replace('folder = "Job Applications"', 'folder = "Inbox"')
                .replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )
            store = ErgaStore(root / "state" / "erga.sqlite3")
            application = store.create_application(
                company="Uber",
                role="Software Engineer Intern",
                source_url=job_url,
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")
            denial = MailMessageMetadata(
                message_id="uber-denial",
                received_at=datetime.now(UTC),
                sender="talent@uber.com",
                subject="Thanks for your interest in Uber",
                preview="Unfortunately, we will not be moving forward.",
            )

            with (
                patch(
                    "erga_mcp.integrations.mail.provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail.provider.fetch_all_inbox_metadata",
                    return_value=[denial],
                ),
            ):
                result: Any = asyncio.run(
                    build_server(config_path).call_tool("sync_recruiting_mail", {})
                )
            payload = cast(dict[str, Any], result.structured_content)

            self.assertEqual(payload["tracker_updates"], 0)
            self.assertIn("| Applied |", tracker_path.read_text(encoding="utf-8"))
            self.assertEqual(store.list_mail_reconciliations()[0].state, "review")

    def test_syncs_configured_zoho_folder_and_returns_a_safe_compact_message(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"')
                .replace('client_id = ""', 'client_id = "test-client"')
                .replace('folder = "Job Applications"', 'folder = "Inbox"')
                .replace("enabled = false", "enabled = true")
                .replace('tracker_dir = ""', 'tracker_dir = "tracker"'),
                encoding="utf-8",
            )
            message = MailMessageMetadata(
                message_id="message-1",
                received_at=datetime(2026, 7, 20, tzinfo=UTC),
                sender="jobs@example.test",
                subject="Thank you for applying",
                preview="Sensitive preview text must not appear in the command response.",
            )
            with (
                patch(
                    "erga_mcp.integrations.mail.provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail.provider.fetch_all_inbox_metadata",
                    return_value=[message],
                ) as fetch,
                patch(
                    "erga_mcp.mcp.workspace_tools.reconcile_confirmed_application_tracker_rows",
                    return_value=1,
                ) as reconcile,
                patch(
                    "erga_mcp.mcp.workspace_tools.import_confirmed_application_tracker_rows",
                    return_value=2,
                ) as imports,
            ):
                result: Any = asyncio.run(
                    build_server(config_path).call_tool("sync_recruiting_mail", {})
                )
            payload = cast(dict[str, Any], result.structured_content)

        self.assertEqual(payload["provider"], "zoho")
        self.assertEqual(payload["fetched"], 1)
        self.assertEqual(payload["created"], 1)
        self.assertEqual(payload["recruiting_events"], 1)
        self.assertEqual(payload["tracker_updates"], 3)
        self.assertEqual(payload["tracker_imports"], 2)
        self.assertEqual(payload["mail_reviews_pending"], 0)
        self.assertIn("Erga mail sync complete", payload["message"])
        self.assertNotIn("/erga-mail-review", payload["message"])
        self.assertNotIn(message.preview, payload["message"])
        self.assertNotIn(message.subject, payload["message"])
        fetch.assert_called_once_with(
            access_token="test-token",
            folder="Inbox",
            max_messages=1000,
            page_size=100,
            include_content=True,
            known_message_ids=set(),
        )
        self.assertEqual(reconcile.call_args.kwargs["tracker_dir"], root / "tracker")
        self.assertEqual(len(reconcile.call_args.kwargs["events"]), 1)
        self.assertEqual(imports.call_args.kwargs["active_cycles"], ())
        self.assertEqual(imports.call_args.kwargs["tracker_dir"], root / "tracker")

    def test_repeat_zoho_sync_fetches_content_only_for_new_messages(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"')
                .replace('client_id = ""', 'client_id = "test-client"')
                .replace('folder = "Job Applications"', 'folder = "Inbox"'),
                encoding="utf-8",
            )
            known_id_calls: list[set[str]] = []

            def fetch(**kwargs: object) -> list[MailMessageMetadata]:
                known_ids = set(cast(set[str], kwargs.get("known_message_ids", set())))
                known_id_calls.append(known_ids)
                return [
                    MailMessageMetadata(
                        message_id="message-1",
                        received_at=datetime(2026, 7, 20, tzinfo=UTC),
                        sender="updates@example.test",
                        subject="Update on your application",
                        preview="",
                        content=(
                            "We received your application." if "message-1" not in known_ids else ""
                        ),
                    )
                ]

            with (
                patch(
                    "erga_mcp.integrations.mail.provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail.provider.fetch_all_inbox_metadata",
                    side_effect=fetch,
                ),
            ):
                server = build_server(config_path)
                asyncio.run(server.call_tool("sync_recruiting_mail", {}))
                asyncio.run(server.call_tool("sync_recruiting_mail", {}))
                listed: Any = asyncio.run(server.call_tool("list_mail_events", {}))

            events = cast(list[dict[str, Any]], listed.structured_content["result"])

        self.assertEqual(known_id_calls, [set(), {"message-1"}])
        self.assertEqual(events[0]["kind"], "application.acknowledgement")


if __name__ == "__main__":
    unittest.main()
