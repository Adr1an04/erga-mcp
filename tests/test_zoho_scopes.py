from __future__ import annotations

import unittest

from recruiting_pipeline.integrations.zoho import validate_read_only_scopes


class ZohoScopeTests(unittest.TestCase):
    def test_allows_minimum_message_scope_and_optional_account_discovery(self) -> None:
        self.assertEqual(
            validate_read_only_scopes(["ZohoMail.messages.READ", "ZohoMail.accounts.READ"]),
            ("ZohoMail.accounts.READ", "ZohoMail.messages.READ"),
        )

    def test_rejects_broader_or_mutating_scopes(self) -> None:
        with self.assertRaises(ValueError):
            validate_read_only_scopes(["ZohoMail.messages.ALL"])


if __name__ == "__main__":
    unittest.main()
