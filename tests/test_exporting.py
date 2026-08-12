from __future__ import annotations

import json
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.operations.exporting import export_bundle
from erga_mcp.store import ErgaStore


class ExportingTests(unittest.TestCase):
    def test_exports_state_and_generated_job_packages_to_a_private_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ErgaStore(root / "erga.sqlite3")
            store.create_application(
                company="Example Systems",
                role="Software Engineer",
                source_url="https://jobs.example.test/1",
                evidence_ids=[],
            )
            package = root / "output" / "summer-2027" / "example-systems"
            package.mkdir(parents=True)
            (package / "package.json").write_text('{"status":"complete"}\n')
            (package / "resume.pdf").write_bytes(b"synthetic-pdf")
            destination = root / "exports" / "recruiting.zip"

            result = export_bundle(
                store=store,
                output_root=root / "output",
                destination=destination,
                exported_at=datetime(2026, 7, 19, tzinfo=UTC),
            )

            self.assertEqual(result["applications"], 1)
            with zipfile.ZipFile(destination) as archive:
                self.assertIn("erga-snapshot.json", archive.namelist())
                self.assertIn(
                    "job-packages/summer-2027/example-systems/resume.pdf",
                    archive.namelist(),
                )
                snapshot = json.loads(archive.read("erga-snapshot.json"))
            self.assertEqual(snapshot["applications"][0]["company"], "Example Systems")
            self.assertEqual(snapshot["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
