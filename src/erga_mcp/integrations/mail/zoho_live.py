from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from erga_mcp.integrations.mail.zoho import MailMessageMetadata
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.classification import classify_application_message
from erga_mcp.tracking.contacts import record_recruiter_contact_from_mail
from erga_mcp.tracking.mail_receipts import RECEIPT_PARSER_VERSION, parse_application_receipt
from erga_mcp.tracking.mail_reconciliation import reconcile_mail_events, sanitized_mail_signals

_DIRECT_RECRUITER_OUTREACH_MARKERS = (
    "came across your profile",
    "found your profile",
    "reaching out about",
    "reach out about",
    "your background caught",
    "your experience caught",
    "would like to connect",
    "interested in speaking with you",
    "interested in discussing",
    "would you be interested",
    "are you interested in",
    "open to a new role",
    "open to new opportunities",
)
_RECRUITING_IDENTITY_MARKERS = ("recruiter", "recruiting", "talent acquisition", "sourcer")
_ROLE_CONTEXT_MARKERS = (" role", "position", "opening", "opportunity", "hiring")
_MARKETING_MARKERS = (
    "free applications",
    "job alert",
    "jobbie",
    "newsletter",
    "new jobs available",
    "new roles available",
    "one click",
    "one tap",
    "recommended jobs",
    "unsubscribe",
)


def _classify(message: MailMessageMetadata) -> tuple[str, float, bool]:
    content = (
        f"{message.sender}\n{message.subject}\n{message.preview}\n{message.content}".casefold()
    )
    application = classify_application_message(
        subject=message.subject, preview=f"{message.preview}\n{message.content}"
    )
    if application.kind != "unknown":
        return (
            f"application.{application.kind}",
            application.confidence,
            application.requires_review,
        )
    if any(marker in content for marker in _MARKETING_MARKERS):
        return "other", 0.0, False
    direct_outreach = any(marker in content for marker in _DIRECT_RECRUITER_OUTREACH_MARKERS)
    identified_recruiter = any(marker in content for marker in _RECRUITING_IDENTITY_MARKERS)
    role_context = any(marker in content for marker in _ROLE_CONTEXT_MARKERS)
    if direct_outreach or (identified_recruiter and role_context):
        return "job.candidate", 0.7, True
    return "other", 0.0, False


def _event_from_message(message: MailMessageMetadata) -> MailEvent:
    kind, confidence, requires_review = _classify(message)
    signals = sanitized_mail_signals(
        sender=message.sender,
        subject=message.subject,
        preview=message.preview,
        content=message.content,
        thread_id=message.thread_id,
        reference_ids=message.reference_ids,
    )
    receipt = (
        parse_application_receipt(
            sender=message.sender,
            subject=message.subject,
            preview=message.preview,
            content=message.content,
        )
        if kind.startswith("application.")
        else None
    )
    requisition_ids = set(signals["requisition_ids"])
    if receipt is not None:
        requisition_ids.update(receipt.requisition_ids)
    return MailEvent(
        message_id=message.message_id,
        received_at=message.received_at,
        sender=message.sender,
        subject=message.subject,
        kind=kind,
        confidence=confidence,
        requires_review=requires_review,
        sender_domain=signals["sender_domain"],
        job_urls=signals["job_urls"],
        requisition_ids=tuple(sorted(requisition_ids)),
        thread_id=signals["thread_id"],
        reference_ids=signals["reference_ids"],
        company_hint=receipt.company if receipt is not None else "",
        role_hint=receipt.role if receipt is not None else "",
        recruiting_cycle_hints=(receipt.recruiting_cycle_hints if receipt is not None else ()),
        receipt_parsed=True,
        receipt_parser_version=RECEIPT_PARSER_VERSION,
    )


def refresh_known_metadata(
    store: ErgaStore, messages: Sequence[MailMessageMetadata]
) -> dict[str, int]:
    """Promote retained misses and enrich lifecycle identity without losing prior evidence."""
    existing_by_id = {event.message_id: event for event in store.list_mail_events()}
    promoted = 0
    enriched = 0
    reparsed = 0
    for message in messages:
        existing = existing_by_id.get(message.message_id)
        if existing is None:
            continue
        candidate = _event_from_message(message)
        existing_is_relevant = existing.kind != "other"
        if existing_is_relevant and candidate.kind != existing.kind and not message.content.strip():
            candidate = replace(
                candidate,
                kind=existing.kind,
                confidence=existing.confidence,
                requires_review=existing.requires_review,
            )
        merged = replace(
            candidate,
            job_urls=tuple(sorted(set(existing.job_urls).union(candidate.job_urls))),
            requisition_ids=tuple(
                sorted(set(existing.requisition_ids).union(candidate.requisition_ids))
            ),
            company_hint=candidate.company_hint or existing.company_hint,
            role_hint=candidate.role_hint or existing.role_hint,
            recruiting_cycle_hints=(
                candidate.recruiting_cycle_hints or existing.recruiting_cycle_hints
            ),
        )
        identity_changed = bool(
            merged.company_hint != existing.company_hint
            or merged.role_hint != existing.role_hint
            or set(merged.requisition_ids) != set(existing.requisition_ids)
            or set(merged.job_urls) != set(existing.job_urls)
            or set(merged.recruiting_cycle_hints) != set(existing.recruiting_cycle_hints)
        )
        changed = store.update_mail_event_classification(merged)
        reparsed += int(existing.receipt_parser_version < RECEIPT_PARSER_VERSION)
        promoted += int(changed and not existing_is_relevant and merged.kind != "other")
        enriched += int(changed and existing.kind == merged.kind and identity_changed)
    return {"promoted": promoted, "enriched": enriched, "reparsed": reparsed}


def receipt_recovery_message_ids(events: Sequence[MailEvent]) -> set[str]:
    """Select retained messages once for each deterministic receipt-parser revision."""
    return {
        event.message_id
        for event in events
        if event.receipt_parser_version < RECEIPT_PARSER_VERSION
    }


def sync_metadata(
    store: ErgaStore, messages: Sequence[MailMessageMetadata]
) -> dict[str, int | list[dict[str, str | bool]]]:
    """Persist minimal classified metadata and return new relevant-message alerts."""
    counts = {"application": 0, "job": 0, "other": 0, "created": 0, "status_transitions": 0}
    alerts: list[dict[str, str | bool]] = []
    for message in messages:
        event = _event_from_message(message)
        kind = event.kind
        requires_review = event.requires_review
        created = store.record_mail_event(event)
        if not created:
            store.update_mail_event_classification(event)
        contact = record_recruiter_contact_from_mail(store, event)
        if contact is not None:
            counts.setdefault("contacts", 0)
            counts["contacts"] = int(counts["contacts"]) + 1
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
    reconciliation = reconcile_mail_events(store, store.list_mail_events())
    counts["status_transitions"] = reconciliation.transitions
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
    known_message_ids: Collection[str] = (),
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
        if include_content and message_id not in known_message_ids:
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
                thread_id=str(item.get("threadId", item.get("conversationId", ""))),
                reference_ids=tuple(
                    str(item.get(key, ""))
                    for key in ("messageIdHeader", "inReplyTo")
                    if item.get(key)
                ),
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
    known_message_ids: Collection[str] = (),
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
            known_message_ids=known_message_ids,
        )
        result.extend(page)
        if len(page) < page_limit:
            break
        start += len(page)
    return result
