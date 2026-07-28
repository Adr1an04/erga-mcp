from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.cli import main
from erga_mcp.integrations.zoho import MailMessageMetadata


class MailSyncTests(unittest.TestCase):
    def test_mail_sync_dispatches_gmail_provider(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('[paths]\ndata_dir = "state"\n[mail]\nprovider = "gmail"\n')
            message = MailMessageMetadata(
                message_id="gmail:one",
                received_at=datetime(2026, 1, 1, tzinfo=UTC),
                sender="recruiter@example.test",
                subject="Application received",
                preview="Thanks for applying",
            )
            with patch(
                "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata_with_gws",
                return_value=[message],
            ):
                self.assertEqual(main(["mail", "sync", "--config", str(config)]), 0)

    def test_notify_mode_is_silent_without_new_events_and_formats_new_interviews(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text('[paths]\ndata_dir = "state"\n[mail]\nprovider = "gmail"\n')
            message = MailMessageMetadata(
                message_id="gmail:interview",
                received_at=datetime(2026, 7, 19, tzinfo=UTC),
                sender="recruiting@example.test",
                subject="Schedule your interview",
                preview="Choose a technical interview time.",
            )

            first_output = StringIO()
            second_output = StringIO()
            with patch(
                "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata_with_gws",
                return_value=[message],
            ):
                with redirect_stdout(first_output):
                    self.assertEqual(main(["mail", "sync", "--config", str(config), "--notify"]), 0)
                with redirect_stdout(second_output):
                    self.assertEqual(main(["mail", "sync", "--config", str(config), "--notify"]), 0)

            self.assertIn("Interview invitation", first_output.getvalue())
            self.assertEqual(second_output.getvalue(), "")

    def test_configured_zoho_provider_uses_stored_non_secret_client_id(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text(
                '[paths]\ndata_dir = "state"\n[mail]\nprovider = "zoho"\n'
                'client_id = "synthetic-client"\n'
                'accounts_url = "https://accounts.zoho.eu"\n'
            )
            with (
                patch(
                    "erga_mcp.integrations.mail_provider.refresh_access_token",
                    return_value="access-token",
                ) as refresh,
                patch(
                    "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata", return_value=[]
                ),
            ):
                self.assertEqual(main(["mail", "sync", "--config", str(config)]), 0)

            refresh.assert_called_once_with(
                client_id="synthetic-client", accounts_url="https://accounts.zoho.eu"
            )


if __name__ == "__main__":
    unittest.main()
