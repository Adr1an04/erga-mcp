from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.tracker_view import (
    filter_application_tracker,
    paginate_application_tracker,
    read_application_tracker,
    render_tracker_message,
)


class TrackerViewTests(unittest.TestCase):
    def test_reads_tracker_rows_and_renders_a_cross_platform_card(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                "# Fall 2026\n\n## Application tracker\n\n"
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Cloudflare | Software Engineer Intern | Austin | "
                "[Posting](https://example.test) | "
                "Researching |  | Review résumé | [[Cloudflare]] |\n"
                "| Google | Software Engineering Intern | Remote | "
                "[Posting](https://example.test) | "
                "Applied | 2026-07-20 | Await update | [[Google]] |\n",
                encoding="utf-8",
            )

            snapshot = read_application_tracker(tracker_dir)
            message = render_tracker_message(snapshot)

        self.assertEqual(len(snapshot.entries), 2)
        self.assertEqual(snapshot.summary, {"applied": 1, "researching": 1})
        self.assertIn("Erga application tracker", message)
        self.assertIn("2 roles", message)
        self.assertIn("**Fall 2026**", message)
        self.assertIn(
            "🟡 **[Cloudflare](https://example.test)** - Software Engineer Intern", message
        )
        self.assertIn(
            "📬 **[Google](https://example.test)** - Software Engineering Intern", message
        )
        self.assertIn("Next: Review résumé", message)
        self.assertIn("**[Cloudflare](https://example.test)**", message)

    def test_uses_distinct_oa_interview_and_offer_icons(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Example OA | Engineer | Remote | Source | OA | 2026-07-20 | Prepare | Note |\n"
                "| Example Interview | Engineer | Remote | Source | Interview | 2026-07-20 | "
                "Prepare | Note |\n"
                "| Example Offer | Engineer | Remote | Source | Offer | 2026-07-20 | Evaluate | "
                "Note |\n",
                encoding="utf-8",
            )

            message = render_tracker_message(read_application_tracker(tracker_dir))

        self.assertIn("🧪 **Example OA**", message)
        self.assertIn("🗣️ **Example Interview**", message)
        self.assertIn("🎉 **Example Offer**", message)

    def test_filters_by_company_role_status_or_cycle(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Cloudflare | Software Engineer Intern | Austin | Source | Applied | "
                "2026-07-20 | Await update | Note |\n"
                "| Snowflake | Software Engineer Intern | Zurich | Source | Researching | "
                "| Review | Note |\n",
                encoding="utf-8",
            )
            matches = filter_application_tracker(
                read_application_tracker(tracker_dir), "cloudflare"
            )
            all_entries = filter_application_tracker(read_application_tracker(tracker_dir), "all")
            message = render_tracker_message(matches, query="cloudflare")

        self.assertEqual([entry.company for entry in matches.entries], ["Cloudflare"])
        self.assertEqual(len(all_entries.entries), 2)
        self.assertIn("Search: cloudflare · 1 match", message)
        self.assertNotIn("Snowflake", message)

        with TemporaryDirectory() as directory:
            snapshot = read_application_tracker(Path(directory))

        self.assertEqual(snapshot.entries, ())
        self.assertEqual(snapshot.summary, {})
        self.assertEqual(
            render_tracker_message(snapshot),
            (
                "### Erga application tracker\n\n"
                "No application rows are available in the configured Obsidian trackers yet."
            ),
        )

    def test_ignores_malformed_rows_and_limits_message_output(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            rows = "".join(
                f"| Company {index} | Role {index} | Remote | Source | Draft | | Review | Note |\n"
                for index in range(25)
            )
            (tracker_dir / "Unscheduled Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| malformed | only | three |\n" + rows,
                encoding="utf-8",
            )

            snapshot = read_application_tracker(tracker_dir)
            message = render_tracker_message(snapshot, max_entries=20)

        self.assertEqual(len(snapshot.entries), 25)
        self.assertIn("Page 1 of 2 · showing 1-20 of 25 roles.", message)
        self.assertIn("Company 19", message)
        self.assertNotIn("Company 20", message)

        second_page = paginate_application_tracker(snapshot, page=2, page_size=20)
        second_message = render_tracker_message(snapshot, page=2, page_size=20)
        self.assertEqual(second_page.page, 2)
        self.assertEqual(len(second_page.entries), 5)
        self.assertIn("Company 20", second_message)
        self.assertNotIn("Company 19", second_message)

    def test_groups_cycles_once_and_combines_assessment_status_aliases(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            header = (
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                header
                + "| Alpha | Engineer | Remote | Source | OA | | Prepare | Note |\n"
                + "| Beta | Engineer | Remote | Source | Applied | | Wait | Note |\n",
                encoding="utf-8",
            )
            (tracker_dir / "Summer 2027 Applications.md").write_text(
                header
                + "| Gamma | Engineer | Remote | Source | Online assessment | | Prepare | Note |\n"
                + "| Delta | Engineer | Remote | Source | Researching | | Review | Note |\n",
                encoding="utf-8",
            )

            snapshot = read_application_tracker(tracker_dir)
            message = render_tracker_message(snapshot, page_size=10)

        self.assertEqual(snapshot.summary["assessment"], 2)
        self.assertNotIn("oa", snapshot.summary)
        self.assertEqual(message.count("**Summer 2027**"), 1)
        self.assertEqual(message.count("**Fall 2026**"), 1)
        self.assertLess(message.index("**Summer 2027**"), message.index("**Fall 2026**"))

    def test_coalesces_one_unambiguous_email_confirmation_with_its_sourced_job(self) -> None:
        with TemporaryDirectory() as directory:
            tracker_dir = Path(directory)
            header = (
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            )
            (tracker_dir / "Fall 2026 Application Tracker.md").write_text(
                header + "| Example | Application confirmed by email | | | Rejected | "
                "2026-08-01 | Closed | Note |\n",
                encoding="utf-8",
            )
            (tracker_dir / "Unscheduled Application Tracker.md").write_text(
                header + "| Example | Software Engineer Intern | Remote | "
                "[Job](https://example.test/job) | Applied | | Wait | Note |\n",
                encoding="utf-8",
            )

            snapshot = read_application_tracker(tracker_dir)

        self.assertEqual(len(snapshot.entries), 1)
        self.assertEqual(snapshot.entries[0].role, "Software Engineer Intern")
        self.assertEqual(snapshot.entries[0].source_url, "https://example.test/job")
        self.assertEqual(snapshot.entries[0].status, "Rejected")
        self.assertEqual(snapshot.entries[0].applied, "2026-08-01")


if __name__ == "__main__":
    unittest.main()
