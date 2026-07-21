from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .zoho import MailMessageMetadata

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _run_gws(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a GWS command, including Windows .cmd launchers without shell injection."""
    invocation = command
    if os.name == "nt" and command[0].casefold().endswith((".bat", ".cmd")):
        invocation = [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(command),
        ]
    return subprocess.run(invocation, check=True, capture_output=True, text=True, timeout=30)


def parse_message_metadata(payload: dict[str, object]) -> MailMessageMetadata:
    """Normalize Gmail message metadata without fetching or storing a message body."""
    raw_headers = payload.get("payload", {})
    headers = raw_headers.get("headers", []) if isinstance(raw_headers, dict) else []
    values = {
        str(item.get("name", "")).casefold(): str(item.get("value", ""))
        for item in headers
        if isinstance(item, dict)
    }
    message_id = payload.get("id")
    received_at = payload.get("internalDate")
    if not isinstance(message_id, str) or not isinstance(received_at, str):
        raise ValueError("Gmail message metadata is missing id or internalDate")
    return MailMessageMetadata(
        message_id=f"gmail:{message_id}",
        received_at=datetime.fromtimestamp(int(received_at) / 1000, UTC),
        sender=values.get("from", ""),
        subject=values.get("subject", ""),
        preview=str(payload.get("snippet", "")),
    )


def fetch_inbox_metadata(
    *, access_token: str, limit: int = 20, get: Callable[[str], dict[str, object]] | None = None
) -> list[MailMessageMetadata]:
    """Read bounded Gmail Inbox metadata with the gmail.readonly scope; never mutate Gmail."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    def api_get(url: str) -> dict[str, object]:
        request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Google HTTPS endpoint
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Gmail API response was not an object")
        return decoded

    request_get = get or api_get
    listed = request_get(
        f"{_GMAIL_API}/messages?" + urlencode({"labelIds": "INBOX", "maxResults": limit})
    )
    messages = listed.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("Gmail message listing returned invalid data")
    result: list[MailMessageMetadata] = []
    for item in messages:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        result.append(
            parse_message_metadata(
                request_get(
                    f"{_GMAIL_API}/messages/{item['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
                )
            )
        )
    return result


def fetch_inbox_metadata_with_gws(
    *,
    gws_command: str = "gws",
    limit: int = 20,
    run: Callable[[list[str]], dict[str, object]] | None = None,
) -> list[MailMessageMetadata]:
    """Use an already-authenticated Google Workspace CLI as a read-only Gmail provider."""
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")

    def invoke(command: list[str]) -> dict[str, object]:
        completed = _run_gws(command)
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError("gws returned non-object JSON")
        return value

    execute = run or invoke
    prefix = [gws_command, "gmail", "users", "messages"]
    listed = execute(
        [
            *prefix,
            "list",
            "--params",
            json.dumps({"userId": "me", "labelIds": ["INBOX"], "maxResults": limit}),
            "--format",
            "json",
        ]
    )
    message_refs = listed.get("messages", [])
    if not isinstance(message_refs, list):
        raise ValueError("gws Gmail listing returned invalid data")
    messages: list[MailMessageMetadata] = []
    for item in message_refs:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        details = execute(
            [
                *prefix,
                "get",
                "--params",
                json.dumps(
                    {
                        "userId": "me",
                        "id": item["id"],
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject"],
                    }
                ),
                "--format",
                "json",
            ]
        )
        messages.append(parse_message_metadata(details))
    return messages


def fetch_all_inbox_metadata_with_gws(
    *,
    gws_command: str = "gws",
    page_size: int = 100,
    max_messages: int = 1000,
    run: Callable[[list[str]], dict[str, object]] | None = None,
) -> list[MailMessageMetadata]:
    """Read Gmail Inbox metadata page by page through a configured read-only GWS client."""
    if page_size < 1 or page_size > 100:
        raise ValueError("Gmail page size must be between 1 and 100")
    if max_messages < 1:
        raise ValueError("Gmail maximum message count must be positive")

    def invoke(command: list[str]) -> dict[str, object]:
        completed = _run_gws(command)
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError("gws returned non-object JSON")
        return value

    execute = run or invoke
    prefix = [gws_command, "gmail", "users", "messages"]
    messages: list[MailMessageMetadata] = []
    page_token: str | None = None
    while len(messages) < max_messages:
        params: dict[str, object] = {
            "userId": "me",
            "labelIds": ["INBOX"],
            "maxResults": min(page_size, max_messages - len(messages)),
        }
        if page_token is not None:
            params["pageToken"] = page_token
        listed = execute([*prefix, "list", "--params", json.dumps(params), "--format", "json"])
        message_refs = listed.get("messages", [])
        if not isinstance(message_refs, list):
            raise ValueError("gws Gmail listing returned invalid data")
        for item in message_refs:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            details = execute(
                [
                    *prefix,
                    "get",
                    "--params",
                    json.dumps(
                        {
                            "userId": "me",
                            "id": item["id"],
                            "format": "metadata",
                            "metadataHeaders": ["From", "Subject"],
                        }
                    ),
                    "--format",
                    "json",
                ]
            )
            messages.append(parse_message_metadata(details))
        next_token = listed.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token or not message_refs:
            break
        page_token = next_token
    return messages
