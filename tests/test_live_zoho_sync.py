from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.integrations.zoho import MailMessageMetadata
from recruiting_pipeline.integrations.zoho_live import sync_metadata
from recruiting_pipeline.store import PipelineStore


class LiveZohoSyncTests(unittest.TestCase):
    def test_records_new_messages_once_with_application_job_and_other_categories(self) -> None:
        messages = [
            MailMessageMetadata(
                "m1",
                datetime(2026, 7, 18, tzinfo=UTC),
                "jobs@example.com",
                "Application received",
                "Thanks for applying",
            ),
            MailMessageMetadata(
                "m2",
                datetime(2026, 7, 18, tzinfo=UTC),
                "recruiter@example.com",
                "Software Engineer role",
                "I found your profile and would like to connect",
            ),
            MailMessageMetadata(
                "m3",
                datetime(2026, 7, 18, tzinfo=UTC),
                "news@example.com",
                "July newsletter",
                "Read our latest news",
            ),
        ]
        with TemporaryDirectory() as directory:
            store = PipelineStore(Path(directory) / "pipeline.sqlite3")
            self.assertEqual(
                sync_metadata(store, messages),
                {"application": 1, "job": 1, "other": 1, "created": 3},
            )
            self.assertEqual(sync_metadata(store, messages)["created"], 0)


if __name__ == "__main__":
    unittest.main()
