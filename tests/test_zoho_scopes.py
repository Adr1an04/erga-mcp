from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from erga_mcp.integrations.mail.zoho import validate_read_only_scopes
from erga_mcp.integrations.mail.zoho_oauth import read_tokens


class ZohoScopeTests(unittest.TestCase):
    def test_allows_minimum_scopes_for_reading_a_folder(self) -> None:
        self.assertEqual(
            validate_read_only_scopes(
                [
                    "ZohoMail.messages.READ",
                    "ZohoMail.folders.READ",
                    "ZohoMail.accounts.READ",
                ]
            ),
            (
                "ZohoMail.accounts.READ",
                "ZohoMail.folders.READ",
                "ZohoMail.messages.READ",
            ),
        )

    def test_rejects_broader_or_mutating_scopes(self) -> None:
        with self.assertRaises(ValueError):
            validate_read_only_scopes(["ZohoMail.messages.ALL"])

    def test_reads_macos_credentials_via_security_cli_without_keyring_api(self) -> None:
        token = json.dumps({"refresh_token": "token"})
        completed = subprocess.CompletedProcess(
            args=["security"], returncode=0, stdout=token, stderr=""
        )
        with (
            patch("erga_mcp.integrations.mail.zoho_oauth.sys.platform", "darwin"),
            patch(
                "erga_mcp.integrations.mail.zoho_oauth.subprocess.run", return_value=completed
            ) as run,
            patch(
                "erga_mcp.integrations.mail.zoho_oauth.keyring.get_password", return_value=token
            ) as get_password,
        ):
            self.assertEqual(read_tokens("client-id"), {"refresh_token": "token"})

        run.assert_called_once_with(
            [
                "security",
                "find-generic-password",
                "-s",
                "erga-mcp.zoho.tokens",
                "-a",
                "client-id",
                "-w",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
        get_password.assert_not_called()


if __name__ == "__main__":
    unittest.main()
