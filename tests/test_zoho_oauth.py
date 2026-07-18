from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from recruiting_pipeline.zoho_oauth import (
    READ_ONLY_SCOPES,
    build_authorization_url,
    exchange_authorization_code,
    pkce_challenge,
    validate_callback,
)


class ZohoOAuthTests(unittest.TestCase):
    def test_builds_a_pkce_authorization_url_with_only_read_scopes(self) -> None:
        verifier = "A" * 64
        url = build_authorization_url(
            accounts_url="https://accounts.zoho.com",
            client_id="client-id",
            redirect_uri="http://127.0.0.1:45678/callback",
            state="state-value",
            code_verifier=verifier,
        )
        query = parse_qs(urlparse(url).query)

        self.assertEqual(urlparse(url).path, "/oauth/v2/auth")
        self.assertEqual(query["scope"], [",".join(READ_ONLY_SCOPES)])
        self.assertEqual(query["state"], ["state-value"])
        self.assertEqual(query["code_challenge"], [pkce_challenge(verifier)])
        self.assertEqual(query["code_challenge_method"], ["S256"])

    def test_exchanges_authorization_code_with_verifier_and_client_secret(self) -> None:
        captured: dict[str, str] = {}

        def post(url: str, data: dict[str, str]) -> dict[str, object]:
            captured["url"] = url
            captured.update(data)
            return {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}

        tokens = exchange_authorization_code(
            accounts_url="https://accounts.zoho.com",
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://127.0.0.1:8765/callback",
            authorization_code="auth-code",
            code_verifier="A" * 64,
            post=post,
        )

        self.assertEqual(captured["url"], "https://accounts.zoho.com/oauth/v2/token")
        self.assertEqual(captured["code_verifier"], "A" * 64)
        self.assertEqual(captured["client_secret"], "client-secret")
        self.assertEqual(tokens["refresh_token"], "refresh")

    def test_connect_stores_token_response_in_keychain(self) -> None:
        from recruiting_pipeline.zoho_oauth import connect

        stored: dict[str, object] = {}

        def browser(url: str) -> bool:
            self.assertIn("/oauth/v2/auth?", url)
            return True

        def receive(_state: str) -> str:
            return "auth-code"

        def post(_url: str, _data: dict[str, str]) -> dict[str, object]:
            return {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}

        def store(service: str, account: str, value: dict[str, object]) -> None:
            stored.update(service=service, account=account, value=value)

        result = connect(
            accounts_url="https://accounts.zoho.com",
            client_id="client-id",
            client_secret="client-secret",
            redirect_uri="http://127.0.0.1:8765/callback",
            browser_open=browser,
            receive_authorization_code=receive,
            post=post,
            token_store=store,
        )

        self.assertEqual(result["refresh_token"], "refresh")
        self.assertEqual(stored["service"], "recruiting-pipeline.zoho.tokens")
        self.assertEqual(stored["account"], "client-id")

    def test_rejects_callback_with_wrong_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "state"):
            validate_callback({"code": ["auth-code"], "state": ["wrong"]}, "expected")


if __name__ == "__main__":
    unittest.main()
