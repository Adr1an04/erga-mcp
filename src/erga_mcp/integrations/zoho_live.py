from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..classification import classify_application_message
from ..models import MailEvent
from ..store import ErgaStore
from .zoho import MailMessageMetadata

_JOB_MARKERS = ("recruiter", "opportunity", "position", "opening", "hiring")
_MARKETING_MARKERS = ("free applications", "one tap", "one click", "jobbie")


def _classify(message: MailMessageMetadata) -> tuple[str, float, bool]:
    content = (
        f"{message.sender}\n{message.subject}\n{message.preview}\n{message.content}".casefold()
    )
    if any(marker in content for marker in _MARKETING_MARKERS):
        return "other", 0.0, False
    application = classify_application_message(
        subject=message.subject, preview=f"{message.preview}\n{message.content}"
    )
    if application.kind != "unknown":
        return (
            f"application.{application.kind}",
            application.confidence,
            application.requires_review,
        )
    content = (
        f"{message.sender}\n{message.subject}\n{message.preview}\n{message.content}".casefold()
    )
    if any(marker in content for marker in _JOB_MARKERS):
        return "job.candidate", 0.7, True
    return "other", 0.0, False


def sync_metadata(
    store: ErgaStore, messages: Sequence[MailMessageMetadata]
) -> dict[str, int | list[dict[str, str | bool]]]:
    """Persist minimal classified metadata and return new relevant-message alerts."""
    counts = {"application": 0, "job": 0, "other": 0, "created": 0}
    alerts: list[dict[str, str | bool]] = []
    for message in messages:
        kind, confidence, requires_review = _classify(message)
        event = MailEvent(
            message_id=message.message_id,
            received_at=message.received_at,
            sender=message.sender,
            subject=message.subject,
            kind=kind,
            confidence=confidence,
            requires_review=requires_review,
        )
        created = store.record_mail_event(event)
        if not created:
            store.update_mail_event_classification(event)
        category = kind.split(".", 1)[0]
        counts[category] = int(counts[category]) + 1
        if created:
            counts["created"] = int(counts["created"]) + 1
            if category != "other":
                alerts.append(
                    {
                        "kind": kind,
                        "received_at": message.received_at.isoformat(),
                        "sender": message.sender,
                        "subject": message.subject,
                        "requires_review": requires_review,
                    }
                )
    return {**counts, "alerts": alerts}


def format_recruiting_alerts(alerts: Sequence[dict[str, str | bool]]) -> str:
    """Render local recruiting-event metadata for a private notification channel."""
    if not alerts:
        return ""
    labels = {
        "application.acknowledgement": "Application acknowledgement",
        "application.assessment": "Assessment invitation",
        "application.interview": "Interview invitation",
        "application.offer": "Offer received",
        "application.denial": "Application decision",
        "job.candidate": "Potential job lead",
    }
    blocks = ["[Recruiting inbox update]"]
    for alert in alerts:
        label = labels.get(str(alert["kind"]), "Recruiting update")
        review = " — needs review" if alert["requires_review"] else ""
        blocks.append(
            f"{label}{review}\n"
            f"Received: {alert['received_at']}\n"
            f"From: {alert['sender']}\n"
            f"Subject: {alert['subject']}"
        )
    return "\n\n".join(blocks)


def fetch_inbox_metadata(
    *,
    access_token: str,
    limit: int = 20,
    folder: str = "Inbox",
    start: int = 0,
    include_content: bool = False,
) -> list[MailMessageMetadata]:
    """Fetch read-only metadata from a named Zoho folder."""

    def get(url: str) -> dict[str, object]:
        request = Request(url, headers={"Authorization": f"Zoho-oauthtoken {access_token}"})
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Zoho HTTPS endpoint
            decoded = json.loads(response.read().decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("Zoho API response was not an object")
        return decoded

    accounts = get("https://mail.zoho.com/api/accounts").get("data", [])
    if not isinstance(accounts, list) or not accounts or not isinstance(accounts[0], dict):
        raise ValueError("Zoho account discovery returned no account")
    account_id = str(accounts[0]["accountId"])
    folders = get(f"https://mail.zoho.com/api/accounts/{account_id}/folders").get("data", [])
    if not isinstance(folders, list):
        raise ValueError("Zoho folder discovery returned invalid data")
    normalized_folder = folder.strip().casefold()
    if not normalized_folder:
        raise ValueError("Zoho folder must not be empty")
    selected_folder = next(
        (
            item
            for item in folders
            if isinstance(item, dict)
            and (
                str(item.get("folderName", "")).strip().casefold() == normalized_folder
                or str(item.get("displayName", "")).strip().casefold() == normalized_folder
                or (
                    normalized_folder == "inbox"
                    and str(item.get("folderType", "")).strip().casefold() == "inbox"
                )
            )
        ),
        None,
    )
    if selected_folder is None:
        raise ValueError(f"Zoho folder not found: {folder}")
    folder_id = str(selected_folder["folderId"])
    messages = get(
        f"https://mail.zoho.com/api/accounts/{account_id}/messages/view?"
        + urlencode({"folderId": folder_id, "start": start, "limit": limit})
    ).get("data", [])
    if not isinstance(messages, list):
        raise ValueError("Zoho message listing returned invalid data")
    result: list[MailMessageMetadata] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        received_at = datetime.fromtimestamp(int(item["receivedTime"]) / 1000, UTC)
        message_id = str(item["messageId"])
        content = ""
        if include_content:
            content_response = get(
                f"https://mail.zoho.com/api/accounts/{account_id}/folders/{folder_id}/messages/"
                f"{message_id}/content"
            ).get("data", {})
            if isinstance(content_response, dict):
                content = str(content_response.get("content", ""))
        result.append(
            MailMessageMetadata(
                message_id=message_id,
                received_at=received_at,
                sender=str(item.get("fromAddress", "")),
                subject=str(item.get("subject", "")),
                preview=str(item.get("summary", "")),
                content=content,
            )
        )
    return result


def fetch_all_inbox_metadata(
    *,
    access_token: str,
    folder: str = "Inbox",
    page_size: int = 100,
    max_messages: int = 1000,
    include_content: bool = False,
) -> list[MailMessageMetadata]:
    """Read a configured Zoho folder page by page, bounded by ``max_messages``."""
    if page_size < 1:
        raise ValueError("Zoho page size must be positive")
    if max_messages < 1:
        raise ValueError("Zoho maximum message count must be positive")

    result: list[MailMessageMetadata] = []
    start = 0
    while len(result) < max_messages:
        remaining = max_messages - len(result)
        page_limit = min(page_size, remaining)
        page = fetch_inbox_metadata(
            access_token=access_token,
            folder=folder,
            limit=page_limit,
            start=start,
            include_content=include_content,
        )
        result.extend(page)
        if len(page) < page_limit:
            break
        start += len(page)
    return result
