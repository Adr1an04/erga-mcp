from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

READ_ONLY_SCOPES = ("ZohoMail.messages.READ", "ZohoMail.accounts.READ")


def pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorization_url(
    *,
    accounts_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
) -> str:
    if not accounts_url.startswith("https://"):
        raise ValueError("accounts_url must use HTTPS")
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": ",".join(READ_ONLY_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{accounts_url.rstrip('/')}/oauth/v2/auth?{query}"


def _post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310 - caller controls trusted Zoho domain
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Zoho token response was not an object")
    return decoded


def exchange_authorization_code(
    *,
    accounts_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    authorization_code: str,
    code_verifier: str,
    post: Callable[[str, dict[str, str]], dict[str, object]] = _post_form,
) -> dict[str, object]:
    if not accounts_url.startswith("https://"):
        raise ValueError("accounts_url must use HTTPS")
    response = post(
        f"{accounts_url.rstrip('/')}/oauth/v2/token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    if not isinstance(response.get("refresh_token"), str):
        raise ValueError("Zoho token response did not include a refresh token")
    return response


def _store_tokens_in_keychain(service: str, account: str, value: dict[str, object]) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            service,
            "-a",
            account,
            "-w",
            json.dumps(value, separators=(",", ":")),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _start_loopback_receiver() -> Callable[[str], str]:
    received: dict[str, Mapping[str, Sequence[str]]] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            received["query"] = parse_qs(parsed.query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Zoho connected</h1><p>You can close this tab.</p>")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = HTTPServer(("127.0.0.1", 8765), CallbackHandler)
    server.timeout = 300

    def receive(expected_state: str) -> str:
        try:
            while "query" not in received:
                server.handle_request()
        finally:
            server.server_close()
        return validate_callback(received["query"], expected_state)

    return receive


def _read_client_secret_from_keychain(client_id: str) -> str:
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            "recruiting-pipeline.zoho.client-secret",
            "-a",
            client_id,
            "-w",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def store_client_secret(client_id: str, client_secret: str) -> None:
    if not client_secret:
        raise ValueError("client secret must not be empty")
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            "recruiting-pipeline.zoho.client-secret",
            "-a",
            client_id,
            "-w",
            client_secret,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def connect(
    *,
    accounts_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "http://127.0.0.1:8765/callback",
    browser_open: Callable[[str], bool] = webbrowser.open,
    receive_authorization_code: Callable[[str], str] | None = None,
    post: Callable[[str, dict[str, str]], dict[str, object]] = _post_form,
    token_store: Callable[[str, str, dict[str, object]], None] = _store_tokens_in_keychain,
) -> dict[str, object]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    authorization_url = build_authorization_url(
        accounts_url=accounts_url,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_verifier=verifier,
    )
    receiver = receive_authorization_code or _start_loopback_receiver()
    if not browser_open(authorization_url):
        raise RuntimeError("could not open the Zoho authorization page")
    authorization_code = receiver(state)
    tokens = exchange_authorization_code(
        accounts_url=accounts_url,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_code=authorization_code,
        code_verifier=verifier,
        post=post,
    )
    token_store("recruiting-pipeline.zoho.tokens", client_id, tokens)
    return tokens


def validate_callback(query: Mapping[str, Sequence[str]], expected_state: str) -> str:
    if query.get("state", [None])[0] != expected_state:
        raise ValueError("OAuth callback state did not match")
    error = query.get("error", [None])[0]
    if error:
        raise ValueError(f"Zoho authorization failed: {error}")
    code = query.get("code", [None])[0]
    if not code:
        raise ValueError("Zoho callback did not include an authorization code")
    return code
