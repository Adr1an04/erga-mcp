from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.integrations.obsidian_tracker import write_job_tracker_note


class ObsidianTrackerTests(unittest.TestCase):
    def test_creates_reviewable_job_note_with_package_link(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            note = write_job_tracker_note(
                vault_path=root / "vault",
                cycle="Fall 2026",
                company="Example Co",
                role="Software Engineer Intern",
                job_url="https://jobs.example.test/123",
                package_dir=root / "applications" / "Fall26" / "ExampleCo",
            )
            self.assertTrue(note.exists())
            self.assertIn("https://jobs.example.test/123", note.read_text(encoding="utf-8"))
            self.assertEqual(
                note,
                write_job_tracker_note(
                    vault_path=root / "vault",
                    cycle="Fall 2026",
                    company="Example Co",
                    role="Software Engineer Intern",
                    job_url="https://jobs.example.test/123",
                    package_dir=root / "applications" / "Fall26" / "ExampleCo",
                ),
            )


if __name__ == "__main__":
    unittest.main()
