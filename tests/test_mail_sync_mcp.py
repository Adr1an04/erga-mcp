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
                DEFAULT_CONFIG.replace('client_id = ""', 'client_id = "test-client"').replace(
                    'folder = "Job Applications"', 'folder = "Inbox"'
                ),
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
                patch("erga_mcp.mcp_server.refresh_access_token", return_value="test-token"),
                patch(
                    "erga_mcp.mcp_server.fetch_all_inbox_metadata", return_value=[message]
                ) as fetch,
            ):
                result: Any = asyncio.run(
                    build_server(config_path).call_tool("sync_recruiting_mail", {})
                )
            payload = cast(dict[str, Any], result[1])

        self.assertEqual(payload["provider"], "zoho")
        self.assertEqual(payload["fetched"], 1)
        self.assertEqual(payload["created"], 1)
        self.assertEqual(payload["recruiting_events"], 1)
        self.assertIn("Erga mail sync complete", payload["message"])
        self.assertNotIn(message.preview, payload["message"])
        self.assertNotIn(message.subject, payload["message"])
        fetch.assert_called_once_with(
            access_token="test-token", folder="Inbox", max_messages=1000, page_size=100
        )


if __name__ == "__main__":
    unittest.main()
