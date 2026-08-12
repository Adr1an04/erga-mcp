from __future__ import annotations

import base64
import hashlib
import json
import secrets
import subprocess
import sys
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

READ_ONLY_SCOPES = (
    "ZohoMail.messages.READ",
    "ZohoMail.folders.READ",
    "ZohoMail.accounts.READ",
)
_CLIENT_SECRET_SERVICE = "erga-mcp.zoho.client-secret"
_TOKEN_SERVICE = "erga-mcp.zoho.tokens"


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


def _set_credential(service: str, account: str, value: str) -> None:
    try:
        keyring.set_password(service, account, value)
    except KeyringError as error:
        raise RuntimeError(
            "the operating system credential store is unavailable; "
            "configure a supported keyring backend and try again"
        ) from error


def _get_macos_credential(service: str, account: str) -> str:
    """Read a Keychain item without the Python keyring bridge, which can block in launchd."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("could not read the macOS Keychain credential") from error
    return result.stdout


def _get_credential(service: str, account: str) -> str:
    if sys.platform == "darwin":
        value = _get_macos_credential(service, account)
    else:
        try:
            value = keyring.get_password(service, account)
        except KeyringError as error:
            raise RuntimeError(
                "the operating system credential store is unavailable; "
                "configure a supported keyring backend and try again"
            ) from error
    if value is None:
        raise ValueError(f"no credential is stored for service {service!r} and account {account!r}")
    return value


def _store_tokens(service: str, account: str, value: dict[str, object]) -> None:
    _set_credential(service, account, json.dumps(value, separators=(",", ":")))


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


def read_tokens(client_id: str) -> dict[str, object]:
    value = json.loads(_get_credential(_TOKEN_SERVICE, client_id))
    if not isinstance(value, dict) or not isinstance(value.get("refresh_token"), str):
        raise ValueError("Zoho credential-store token entry has no refresh token")
    return value


def refresh_access_token(*, client_id: str, accounts_url: str = "https://accounts.zoho.com") -> str:
    response = _post_form(
        f"{accounts_url.rstrip('/')}/oauth/v2/token",
        {
            "refresh_token": str(read_tokens(client_id)["refresh_token"]),
            "client_id": client_id,
            "client_secret": read_client_secret(client_id),
            "grant_type": "refresh_token",
        },
    )
    token = response.get("access_token")
    if not isinstance(token, str):
        raise ValueError("Zoho refresh response did not include an access token")
    return token


def read_client_secret(client_id: str) -> str:
    return _get_credential(_CLIENT_SECRET_SERVICE, client_id)


def store_client_secret(client_id: str, client_secret: str) -> None:
    if not client_secret:
        raise ValueError("client secret must not be empty")
    _set_credential(_CLIENT_SECRET_SERVICE, client_id, client_secret)


def delete_credentials(client_id: str) -> tuple[str, ...]:
    """Delete Erga-owned Zoho credentials for one configured OAuth client."""
    deleted: list[str] = []
    for service in (_CLIENT_SECRET_SERVICE, _TOKEN_SERVICE):
        try:
            keyring.delete_password(service, client_id)
        except PasswordDeleteError:
            continue
        except KeyringError as error:
            raise RuntimeError(
                "could not remove Zoho credentials from the credential store"
            ) from error
        deleted.append(service)
    return tuple(deleted)


def connect(
    *,
    accounts_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str = "http://127.0.0.1:8765/callback",
    browser_open: Callable[[str], bool] = webbrowser.open,
    receive_authorization_code: Callable[[str], str] | None = None,
    post: Callable[[str, dict[str, str]], dict[str, object]] = _post_form,
    token_store: Callable[[str, str, dict[str, object]], None] = _store_tokens,
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
    token_store(_TOKEN_SERVICE, client_id, tokens)
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
