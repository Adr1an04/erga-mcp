from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.integrations.zoho import ingest_fixture
from recruiting_pipeline.store import PipelineStore


class ZohoFixtureTests(unittest.TestCase):
    def test_ingests_minimal_metadata_once_and_routes_denials_to_review(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "zoho_messages.json"
        with TemporaryDirectory() as directory:
            store = PipelineStore(Path(directory) / "pipeline.sqlite3")

            first_import = ingest_fixture(store, fixture)
            second_import = ingest_fixture(store, fixture)
            events = store.list_mail_events()

            self.assertEqual(first_import, 2)
            self.assertEqual(second_import, 0)
            self.assertEqual(len(events), 2)
            denial = next(event for event in events if event.kind == "denial")
            self.assertTrue(denial.requires_review)
            self.assertNotIn("preview", denial.__dict__)


if __name__ == "__main__":
    unittest.main()
