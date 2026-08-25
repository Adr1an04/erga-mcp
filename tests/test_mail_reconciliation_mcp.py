from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.mcp.server import build_server
from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.mail_reconciliation import reconcile_mail_events


class MailReconciliationMcpTests(unittest.TestCase):
    def test_unmatched_history_retries_silently_instead_of_flooding_review_queue(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"'),
                encoding="utf-8",
            )
            store = ErgaStore(root / "state" / "erga.sqlite3")
            store.record_mail_event(
                MailEvent(
                    message_id="historical-without-local-application",
                    received_at=datetime.now(UTC),
                    sender="jobs@example.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                )
            )
            reconcile_mail_events(store, store.list_mail_events())

            listed: Any = asyncio.run(
                build_server(config_path).call_tool("list_mail_reconciliation_reviews", {})
            )
            payload = cast(dict[str, Any], listed.structured_content)

            self.assertEqual(store.list_mail_reconciliations()[0].state, "unmatched")
            self.assertEqual(payload["pending_count"], 0)
            self.assertIsNone(payload["review"])

    def test_lists_card_and_resolves_explicit_candidate_without_message_content(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"'),
                encoding="utf-8",
            )
            store = ErgaStore(root / "state" / "erga.sqlite3")
            application = store.create_application(
                company="Example Labs",
                role="Software Engineering Intern",
                source_url="https://jobs.ashbyhq.com/example-labs/req-4242",
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")
            store.record_mail_event(
                MailEvent(
                    message_id="gmail:RAW_PROVIDER_MESSAGE_SECRET_123",
                    received_at=datetime.now(UTC),
                    sender="talent@examplelabs.test",
                    subject="Example Labs interview invitation",
                    kind="application.interview",
                    confidence=0.98,
                    requires_review=True,
                    job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
                )
            )
            reconcile_mail_events(store, store.list_mail_events())
            server = build_server(config_path)

            listed: Any = asyncio.run(server.call_tool("list_mail_reconciliation_reviews", {}))
            payload = cast(dict[str, Any], listed.structured_content)
            review = cast(dict[str, Any], payload["review"])
            candidate = cast(list[dict[str, Any]], review["candidates"])[0]

            self.assertEqual(payload["pending_count"], 1)
            self.assertEqual(candidate["application_id"], application.id)
            self.assertEqual(payload["card"]["title"], "Recruiting inbox review")
            self.assertNotIn("preview", str(payload).casefold())
            self.assertNotIn("content", str(payload).casefold())
            self.assertNotIn("message_id", review)
            self.assertNotIn("RAW_PROVIDER_MESSAGE_SECRET_123", str(payload))

            resolved: Any = asyncio.run(
                server.call_tool(
                    "resolve_mail_reconciliation",
                    {
                        "review_id": review["id"],
                        "action": "match",
                        "application_id": application.id,
                    },
                )
            )
            resolved_payload = cast(dict[str, Any], resolved.structured_content)

            self.assertEqual(resolved_payload["review"]["state"], "resolved")
            self.assertNotIn("message_id", resolved_payload["review"])
            self.assertNotIn("RAW_PROVIDER_MESSAGE_SECRET_123", str(resolved_payload))
            self.assertEqual(store.list_applications()[0].status, "interview")

    def test_retry_reconciles_historical_unmatched_after_application_change(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace('tool_profile = "career"', 'tool_profile = "hermes"'),
                encoding="utf-8",
            )
            store = ErgaStore(root / "state" / "erga.sqlite3")
            event_time = datetime.now(UTC) + timedelta(minutes=1)
            store.record_mail_event(
                MailEvent(
                    message_id="historical-retry",
                    received_at=event_time,
                    sender="talent@examplelabs.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    job_urls=("https://jobs.ashbyhq.com/example-labs/req-4242",),
                )
            )
            reconcile_mail_events(store, store.list_mail_events())
            store.create_application(
                company="Example Labs",
                role="Software Engineering Intern",
                source_url="https://jobs.ashbyhq.com/example-labs/req-4242",
                evidence_ids=[],
            )

            retried: Any = asyncio.run(
                build_server(config_path).call_tool("retry_mail_reconciliation", {})
            )
            payload = cast(dict[str, Any], retried.structured_content)

            self.assertEqual(payload["summary"]["transitions"], 1)
            self.assertEqual(store.list_applications()[0].status, "applied")


if __name__ == "__main__":
    unittest.main()
