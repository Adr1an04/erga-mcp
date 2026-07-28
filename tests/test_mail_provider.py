from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from erga_mcp.integrations.mail_provider import build_mail_provider


class MailProviderTests(unittest.TestCase):
    def test_selects_gmail_provider_from_config(self) -> None:
        config = SimpleNamespace(mail_provider="gmail", gws_command="gws")
        expected = [object()]
        with patch(
            "erga_mcp.integrations.mail_provider.fetch_all_inbox_metadata_with_gws",
            return_value=expected,
        ) as fetch:
            messages = build_mail_provider(config).fetch_inbox_metadata()

        self.assertIs(messages, expected)
        fetch.assert_called_once_with(gws_command="gws", page_size=100, max_messages=1000)

    def test_selects_zoho_provider_and_requires_client_id(self) -> None:
        config = SimpleNamespace(
            mail_provider="zoho", mail_client_id="", mail_accounts_url="", mail_folder="Inbox"
        )
        with self.assertRaisesRegex(ValueError, "client_id"):
            build_mail_provider(config).fetch_inbox_metadata()


if __name__ == "__main__":
    unittest.main()
