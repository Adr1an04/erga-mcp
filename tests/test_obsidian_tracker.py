from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.integrations.obsidian_tracker import (
    import_confirmed_application_tracker_rows,
    reconcile_confirmed_application_tracker_rows,
    write_job_tracker_note,
)
from erga_mcp.models import MailEvent


class ObsidianTrackerTests(unittest.TestCase):
    def test_accepts_obsidian_formatted_table_column_widths(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            tracker_path.write_text(
                "# Fall 2026\n\n## Application tracker\n\n"
                "| Company   | Role                | Location / work mode | Source | "
                "Status      | Applied | Next action       | Contact / link |\n"
                "| --------- | ------------------- | -------------------- | ------ | "
                "----------- | ------- | ----------------- | -------------- |\n"
                "| Existing  | Existing role       | Remote               | Link   | "
                "Researching |         | Review            | Note           |\n",
                encoding="utf-8",
            )

            write_job_tracker_note(
                tracker_dir=tracker,
                cycle="Fall 2026",
                company="Example Co",
                role="Software Engineer Intern",
                job_url="https://jobs.example.test/123",
                package_dir=tracker / "package",
            )

            rendered = tracker_path.read_text(encoding="utf-8")
            self.assertIn("[[Example Co — Software Engineer Intern]]", rendered)
            self.assertIn("| Existing  | Existing role", rendered)

    def test_creates_an_unscheduled_tracker_when_no_time_bucket_exists(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            note = write_job_tracker_note(
                tracker_dir=tracker,
                cycle="Unscheduled",
                company="Example Co",
                role="New Graduate Engineer",
                job_url="https://jobs.example.test/unscheduled",
                package_dir=tracker / "package",
                posting_cycles=(),
            )

            self.assertEqual(note.parent.name, "Unscheduled Application Notes")
            self.assertTrue((tracker / "Unscheduled Application Tracker.md").is_file())
            self.assertIn(
                "[[Example Co — New Graduate Engineer]]",
                (tracker / "Unscheduled Application Tracker.md").read_text(encoding="utf-8"),
            )

    def test_creates_reviewable_job_note_with_package_link(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            (tracker / "Fall 2026 Applications.md").write_text(
                "# Fall 2026 Applications\n\n## Application tracker\n\n"
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                encoding="utf-8",
            )
            note = write_job_tracker_note(
                tracker_dir=root / "tracker",
                cycle="Fall 2026",
                company="Example Co",
                role="Software Engineer Intern",
                job_url="https://jobs.example.test/123",
                package_dir=root / "applications" / "Fall26" / "ExampleCo",
            )
            self.assertEqual(
                note,
                (
                    tracker / "Fall 2026 Applications" / "Example Co — Software Engineer Intern.md"
                ).resolve(),
            )
            self.assertTrue(note.exists())
            self.assertIn("https://jobs.example.test/123", note.read_text(encoding="utf-8"))
            self.assertIn(
                "[[Example Co — Software Engineer Intern]]",
                (tracker / "Fall 2026 Applications.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                note,
                write_job_tracker_note(
                    tracker_dir=root / "tracker",
                    cycle="Fall 2026",
                    company="Example Co",
                    role="Software Engineer Intern",
                    job_url="https://jobs.example.test/123",
                    package_dir=root / "applications" / "Fall26" / "ExampleCo",
                ),
            )

    def test_matches_application_tracker_and_notes_vault_convention(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            for cycle, filename in (
                ("Fall 2026", "Fall 2026 Application Tracker.md"),
                ("Summer 2027", "Summer 2027 Applications.md"),
            ):
                (tracker / filename).write_text(
                    f"# {cycle}\n\n## Application tracker\n\n"
                    "| Company | Role | Location / work mode | Source | Status | Applied | "
                    "Next action | Contact / link |\n"
                    "| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                    encoding="utf-8",
                )

            research = tracker / "package" / "research" / "role-research.md"
            research.parent.mkdir(parents=True)
            research.write_text("research\n", encoding="utf-8")
            pdf = tracker / "package" / "artifacts" / "Candidate.pdf"
            pdf.parent.mkdir()
            pdf.write_bytes(b"pdf")
            note = write_job_tracker_note(
                tracker_dir=tracker,
                cycle="Fall 2026",
                additional_cycles=("Summer 2027",),
                company="Example Voice",
                role="Software Engineering Internship",
                location="Remote — United States",
                compensation="$55–$65/hour",
                job_url="https://jobs.example.test/123",
                package_dir=tracker / "package",
                resume_pdf=pdf,
                research_path=research,
                research_highlights=("Ship an end-to-end project.",),
                application_constraints=("No more than two applications.",),
            )

            self.assertEqual(
                note,
                (
                    tracker
                    / "Fall 2026 Application Notes"
                    / "Example Voice — Software Engineering Internship.md"
                ).resolve(),
            )
            note_text = note.read_text(encoding="utf-8")
            self.assertIn("[[Fall 2026 Application Tracker]]", note_text)
            self.assertIn("[[Summer 2027 Applications]]", note_text)
            self.assertIn("Remote — United States", note_text)
            self.assertIn("Ship an end-to-end project", note_text)
            for filename in (
                "Fall 2026 Application Tracker.md",
                "Summer 2027 Applications.md",
            ):
                self.assertIn(
                    "[[Example Voice — Software Engineering Internship]]",
                    (tracker / filename).read_text(encoding="utf-8"),
                )

            original = note.read_text(encoding="utf-8")
            repeated = write_job_tracker_note(
                tracker_dir=tracker,
                cycle="Fall 2026",
                additional_cycles=("Summer 2027",),
                company="Example Voice",
                role="Software Engineering Internship",
                location="Remote — United States",
                compensation="$55–$65/hour",
                job_url="https://jobs.example.test/123",
                package_dir=tracker / "package",
                resume_pdf=pdf,
                research_path=research,
                research_highlights=("Ship an end-to-end project.",),
                application_constraints=("No more than two applications.",),
            )
            self.assertEqual(repeated.read_text(encoding="utf-8"), original)

    def test_imports_only_acknowledgements_in_active_cycles(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            table = (
                "## Application tracker\n\n"
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            fall_tracker = tracker / "Fall 2026 Application Tracker.md"
            spring_tracker = tracker / "Spring 2027 Applications.md"
            fall_tracker.write_text("# Fall 2026\n\n" + table, encoding="utf-8")
            spring_tracker.write_text("# Spring 2027\n\n" + table, encoding="utf-8")
            events = [
                MailEvent(
                    message_id="current",
                    received_at=datetime(2026, 7, 20, tzinfo=UTC),
                    sender="jobs@example.test",
                    subject="Thank you for applying to Example Systems",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                ),
                MailEvent(
                    message_id="tesla",
                    received_at=datetime(2026, 7, 12, tzinfo=UTC),
                    sender="noreply@tesla.example",
                    subject="Adrian, thank you for your interest in Tesla",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                ),
                MailEvent(
                    message_id="legacy",
                    received_at=datetime(2026, 2, 1, tzinfo=UTC),
                    sender="jobs@example.test",
                    subject="Thank you for applying to Legacy Systems",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                ),
            ]

            created = import_confirmed_application_tracker_rows(
                tracker_dir=tracker,
                active_cycles=("Fall 2026", "Spring 2027"),
                events=events,
            )

            self.assertEqual(created, 2)
            rendered = fall_tracker.read_text()
            self.assertIn("| Example Systems | Application confirmed by email |", rendered)
            self.assertIn("| Tesla | Application confirmed by email |", rendered)
            self.assertNotIn("Legacy Systems", spring_tracker.read_text())

    def test_marks_only_exactly_matched_acknowledgements_as_applied(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            tracker_path = tracker / "Summer 2027 Application Tracker.md"
            tracker_path.write_text(
                "# Summer 2027\n\n## Application tracker\n\n"
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Google | Software Engineering Intern | Remote | Link | Researching |  | "
                "Review role requirements and decide whether to apply. | Note |\n"
                "| Snowflake | Software Engineering Intern | Remote | Link | "
                "Online assessment |  | Complete assessment. | Note |\n",
                encoding="utf-8",
            )
            event = MailEvent(
                message_id="mail-1",
                received_at=datetime(2026, 7, 20, 19, tzinfo=UTC),
                sender="careers@google.com",
                subject="Google application received",
                kind="application.acknowledgement",
                confidence=0.9,
                requires_review=False,
            )

            updates = reconcile_confirmed_application_tracker_rows(
                tracker_dir=tracker, events=[event]
            )

            rendered = tracker_path.read_text(encoding="utf-8")
            self.assertEqual(updates, 1)
            self.assertIn(
                "| Google | Software Engineering Intern | Remote | Link | Applied | 2026-07-20 |",
                rendered,
            )
            self.assertIn(
                "| Snowflake | Software Engineering Intern | Remote | Link | Online assessment |",
                rendered,
            )


if __name__ == "__main__":
    unittest.main()
