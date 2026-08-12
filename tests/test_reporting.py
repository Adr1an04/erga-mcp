from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import MailEvent
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.reporting import render_history_digest


class ReportingTests(unittest.TestCase):
    def test_renders_application_statuses_and_recent_actionable_history(self) -> None:
        with TemporaryDirectory() as directory:
            store = ErgaStore(Path(directory) / "erga.sqlite3")
            application = store.create_application(
                company="Example Systems",
                role="Software Engineer",
                source_url="https://jobs.example.test/1",
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")
            store.record_mail_event(
                MailEvent(
                    message_id="message-1",
                    received_at=datetime(2026, 7, 18, tzinfo=UTC),
                    sender="recruiting@example.test",
                    subject="Schedule your interview",
                    kind="application.interview",
                    confidence=0.98,
                    requires_review=True,
                )
            )

            digest = render_history_digest(store, days=7, now=datetime(2026, 7, 19, tzinfo=UTC))

            self.assertIn("Applications: 1 (applied: 1)", digest)
            self.assertIn("Interview invitation — needs review", digest)
            self.assertIn("Schedule your interview", digest)


if __name__ == "__main__":
    unittest.main()
