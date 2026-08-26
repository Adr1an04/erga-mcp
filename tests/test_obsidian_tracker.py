from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.integrations.obsidian.tracker import (
    import_confirmed_application_tracker_rows,
    reconcile_application_status_tracker_rows,
    reconcile_confirmed_application_tracker_rows,
    write_job_tracker_note,
)
from erga_mcp.models import Application, MailEvent


class ObsidianTrackerTests(unittest.TestCase):
    def test_status_reconciliation_uses_job_url_when_one_company_has_multiple_roles(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            path = tracker / "Summer 2027 Application Tracker.md"
            first_url = "https://jobs.example.test/company/first"
            second_url = "https://jobs.example.test/company/second"
            path.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                f"| Example | First role | Remote | [Posting]({first_url}) | Draft |  | "
                "Prepare and submit application. | Note |\n"
                f"| Example | Second role | Remote | [Posting]({second_url}) | Draft |  | "
                "Prepare and submit application. | Note |\n",
                encoding="utf-8",
            )
            applications = [
                Application(
                    id="app_first",
                    company="Example",
                    role="First role",
                    source_url=first_url,
                    status="applied",
                    evidence_ids=[],
                    created_at=datetime(2026, 8, 12, tzinfo=UTC),
                ),
                Application(
                    id="app_second",
                    company="Example",
                    role="Second role",
                    source_url=second_url,
                    status="oa",
                    evidence_ids=[],
                    created_at=datetime(2026, 8, 12, tzinfo=UTC),
                ),
            ]

            updates = reconcile_application_status_tracker_rows(
                tracker_dir=tracker,
                applications=applications,
            )

            rendered = path.read_text(encoding="utf-8")
            self.assertEqual(updates, 2)
            self.assertIn(f"[Posting]({first_url}) | Applied |", rendered)
            self.assertIn(f"[Posting]({second_url}) | OA |", rendered)

    def test_mirrors_an_unambiguous_canonical_rejection(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            path = tracker / "Unscheduled Application Tracker.md"
            path.write_text(
                "## Application tracker\n\n"
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Uber | Job Opportunity |  | Link | Applied | 2026-07-20 | "
                "Await acknowledgement or recruiting update. |  |\n",
                encoding="utf-8",
            )
            application = Application(
                id="app_uber",
                company="Uber",
                role="Job Opportunity",
                source_url="https://example.test",
                status="rejected",
                evidence_ids=[],
                created_at=datetime(2026, 7, 25, tzinfo=UTC),
            )
            self.assertEqual(
                reconcile_application_status_tracker_rows(
                    tracker_dir=tracker, applications=[application]
                ),
                1,
            )
            self.assertIn("| Uber | Job Opportunity |  | Link | Rejected |", path.read_text())

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
                    company_hint="Example Systems",
                    role_hint="Platform Engineering Intern",
                ),
                MailEvent(
                    message_id="second-current",
                    received_at=datetime(2026, 7, 12, tzinfo=UTC),
                    sender="noreply@example.test",
                    subject="We received your application",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                    company_hint="Example Devices",
                    role_hint="Systems Software Intern",
                ),
                MailEvent(
                    message_id="legacy",
                    received_at=datetime(2026, 2, 1, tzinfo=UTC),
                    sender="jobs@example.test",
                    subject="Thank you for applying to Legacy Systems",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                    company_hint="Legacy Systems",
                    role_hint="Software Intern",
                ),
            ]

            created = import_confirmed_application_tracker_rows(
                tracker_dir=tracker,
                active_cycles=("Fall 2026", "Spring 2027"),
                events=events,
            )

            self.assertEqual(created, 2)
            rendered = fall_tracker.read_text()
            self.assertIn("| Example Systems | Platform Engineering Intern |", rendered)
            self.assertIn("| Example Devices | Systems Software Intern |", rendered)
            self.assertNotIn("Legacy Systems", spring_tracker.read_text())

    def test_rebuilds_managed_rows_and_preserves_user_rows(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            tracker_path.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| User Company | User Chosen Role | Remote | Saved posting | Researching |  | "
                "Review requirements. | Note |\n"
                "| Example Finance Engineering Internship | Application confirmed by email | "
                " | Email acknowledgement | Applied | 2026-08-03 | Await recruiting update. |  |\n"
                "| Example Finance Engineering Internship | the position of Engineering "
                "Internship |  | Email acknowledgement | Applied | 2026-08-03 | Await "
                "recruiting update. |  |\n"
                "| Example Hackathon | Application confirmed by email |  | Email "
                "acknowledgement | Applied | 2026-07-09 | Await recruiting update. |  |\n",
                encoding="utf-8",
            )
            events = [
                MailEvent(
                    message_id="valid-receipt",
                    received_at=datetime(2026, 8, 3, tzinfo=UTC),
                    sender="careers@example.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    company_hint="Example Financial",
                    role_hint="Engineering Internship",
                ),
                MailEvent(
                    message_id="non-job-registration",
                    received_at=datetime(2026, 7, 9, tzinfo=UTC),
                    sender="events@example.test",
                    subject="Hackathon registration received",
                    kind="other",
                    confidence=0.0,
                    requires_review=False,
                ),
            ]

            first = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=(), events=events
            )
            second = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=(), events=events
            )
            rendered = tracker_path.read_text(encoding="utf-8")

            self.assertGreater(first, 0)
            self.assertEqual(second, 0)
            self.assertIn("| User Company | User Chosen Role |", rendered)
            self.assertEqual(rendered.count("| Example Financial | Engineering Internship |"), 1)
            self.assertNotIn("Application confirmed by email", rendered)
            self.assertNotIn("the position of", rendered)
            self.assertNotIn("Example Hackathon", rendered)

    def test_routes_receipts_by_role_term_instead_of_email_date(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            fall_tracker = tracker / "Fall 2026 Application Tracker.md"
            fall_tracker.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| User Company | User Role | Remote | Saved posting | Researching |  | "
                "Review requirements. | Note |\n"
                "| Example Financial | Campus Undergraduate Summer Internship Program - "
                "2027 Digital Product |  | Email acknowledgement | Applied | 2026-08-03 | "
                "Await recruiting update. |  |\n"
                "| Example Entertainment | Platform Engineering Internship, Spring 2027 |  | "
                "Email acknowledgement | Applied | 2026-08-25 | Await recruiting update. |  |\n",
                encoding="utf-8",
            )
            events = [
                MailEvent(
                    message_id="summer-receipt",
                    received_at=datetime(2026, 8, 3, tzinfo=UTC),
                    sender="careers@example.test",
                    subject=(
                        "Thank you for applying to Campus Undergraduate Summer Internship "
                        "Program - 2027 Digital Product"
                    ),
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    company_hint="Example Financial",
                    role_hint=(
                        "Campus Undergraduate Summer Internship Program - 2027 Digital Product"
                    ),
                ),
                MailEvent(
                    message_id="spring-receipt",
                    received_at=datetime(2026, 8, 25, tzinfo=UTC),
                    sender="careers@example.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    company_hint="Example Entertainment",
                    role_hint="Platform Engineering Internship, Spring 2027",
                ),
                MailEvent(
                    message_id="winter-receipt",
                    received_at=datetime(2026, 8, 17, tzinfo=UTC),
                    sender="careers@example.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    company_hint="Example Metrics",
                    role_hint="Software Engineering Intern (Winter)",
                ),
                MailEvent(
                    message_id="year-only-receipt",
                    received_at=datetime(2026, 8, 19, tzinfo=UTC),
                    sender="careers@example.test",
                    subject="Application received",
                    kind="application.acknowledgement",
                    confidence=0.95,
                    requires_review=False,
                    company_hint="Example Compute",
                    role_hint="2027 Internships: Systems Software Engineering",
                ),
            ]

            first = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=(), events=events
            )
            second = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=(), events=events
            )
            fall_rendered = fall_tracker.read_text(encoding="utf-8")
            summer_rendered = (tracker / "Summer 2027 Application Tracker.md").read_text(
                encoding="utf-8"
            )
            spring_rendered = (tracker / "Spring 2027 Application Tracker.md").read_text(
                encoding="utf-8"
            )
            winter_rendered = (tracker / "Winter 2027 Application Tracker.md").read_text(
                encoding="utf-8"
            )

            self.assertGreater(first, 0)
            self.assertEqual(second, 0)
            self.assertIn("| User Company | User Role |", fall_rendered)
            self.assertNotIn("Example Financial", fall_rendered)
            self.assertNotIn("Example Entertainment", fall_rendered)
            self.assertNotIn("Example Metrics", fall_rendered)
            self.assertNotIn("Example Compute", fall_rendered)
            self.assertIn("| Example Financial | Campus Undergraduate Summer", summer_rendered)
            self.assertIn("| Example Compute | 2027 Internships:", summer_rendered)
            self.assertIn("| Example Entertainment | Platform Engineering", spring_rendered)
            self.assertIn("| Example Metrics | Software Engineering Intern", winter_rendered)

    def test_routes_termless_receipt_to_its_existing_job_tracker(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            header = (
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            fall_tracker = tracker / "Fall 2026 Application Tracker.md"
            fall_tracker.write_text(header, encoding="utf-8")
            summer_tracker = tracker / "Summer 2027 Applications.md"
            summer_tracker.write_text(
                header + "| Example Devices | Systems Software Intern | Remote | Saved posting | "
                "Researching |  | Review requirements. | Note |\n",
                encoding="utf-8",
            )
            event = MailEvent(
                message_id="termless-receipt",
                received_at=datetime(2026, 8, 19, tzinfo=UTC),
                sender="careers@example.test",
                subject="We received your application",
                kind="application.acknowledgement",
                confidence=0.95,
                requires_review=False,
                company_hint="Example Devices",
                role_hint="Systems Software Intern",
            )

            changed = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=("Fall 2026",), events=[event]
            )

            self.assertEqual(changed, 0)
            self.assertNotIn("Example Devices", fall_tracker.read_text(encoding="utf-8"))
            self.assertEqual(
                summer_tracker.read_text(encoding="utf-8").count(
                    "| Example Devices | Systems Software Intern |"
                ),
                1,
            )

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

    def test_imports_multiple_distinct_roles_for_the_same_company(self) -> None:
        with TemporaryDirectory() as directory:
            tracker = Path(directory)
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            tracker_path.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n",
                encoding="utf-8",
            )
            events = [
                MailEvent(
                    message_id=f"acme-gpu-{index}",
                    received_at=datetime(2026, 8, 19, index, tzinfo=UTC),
                    sender="acme-gpu@myworkday.com",
                    subject="Thank you for your interest in Acme GPU Labs",
                    kind="application.acknowledgement",
                    confidence=0.9,
                    requires_review=False,
                    company_hint="Acme GPU Labs",
                    role_hint=role,
                    requisition_ids=(requisition,),
                )
                for index, (role, requisition) in enumerate(
                    (
                        ("Machine Learning Systems Intern", "jr9000001"),
                        ("Systems Software Intern", "jr9000002"),
                    )
                )
            ]

            first = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=("Fall 2026",), events=events
            )
            second = import_confirmed_application_tracker_rows(
                tracker_dir=tracker, active_cycles=("Fall 2026",), events=events
            )
            rendered = tracker_path.read_text(encoding="utf-8")

            self.assertEqual(first, 2)
            self.assertEqual(second, 0)
            self.assertIn("| Acme GPU Labs | Machine Learning Systems Intern |", rendered)
            self.assertIn("| Acme GPU Labs | Systems Software Intern |", rendered)


if __name__ == "__main__":
    unittest.main()
