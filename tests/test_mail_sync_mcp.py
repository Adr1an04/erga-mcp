from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.integrations.zoho import MailMessageMetadata
from erga_mcp.mcp_server import build_server


class MailSyncMcpTests(unittest.TestCase):
    def test_syncs_configured_zoho_folder_and_returns_a_safe_compact_message(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('client_id = ""', 'client_id = "test-client"')
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
                    "erga_mcp.integrations.mail_provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata",
                    return_value=[message],
                ) as fetch,
                patch(
                    "erga_mcp.mcp_server.reconcile_confirmed_application_tracker_rows",
                    return_value=1,
                ) as reconcile,
                patch(
                    "erga_mcp.mcp_server.import_confirmed_application_tracker_rows",
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
        self.assertIn("Erga mail sync complete", payload["message"])
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
                DEFAULT_CONFIG.replace('client_id = ""', 'client_id = "test-client"').replace(
                    'folder = "Job Applications"', 'folder = "Inbox"'
                ),
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
                    "erga_mcp.integrations.mail_provider.refresh_access_token",
                    return_value="test-token",
                ),
                patch(
                    "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata",
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
