from __future__ import annotations

import unittest
from datetime import UTC, datetime

from erga_mcp.application_lookup import select_tracked_application
from erga_mcp.models import Application


def _application(*, company: str, role: str) -> Application:
    return Application(
        id=f"app_{company}_{role}",
        company=company,
        role=role,
        source_url="https://jobs.example.test/posting",
        status="draft",
        evidence_ids=[],
        created_at=datetime.now(UTC),
    )


class ApplicationLookupTests(unittest.TestCase):
    def test_selects_one_application_by_case_insensitive_company_and_role_terms(self) -> None:
        target = _application(company="Example Labs", role="Software Engineer Intern")
        applications = [
            _application(company="Other", role="Data Intern"),
            target,
        ]

        self.assertEqual(select_tracked_application("EXAMPLE engineer", applications), target)

    def test_rejects_empty_missing_and_ambiguous_queries(self) -> None:
        applications = [
            _application(company="Example", role="Software Engineer"),
            _application(company="Example", role="Data Engineer"),
        ]

        with self.assertRaisesRegex(ValueError, "must include"):
            select_tracked_application("  ", applications)
        with self.assertRaisesRegex(ValueError, "no tracked application"):
            select_tracked_application("missing", applications)
        with self.assertRaisesRegex(ValueError, "multiple tracked applications"):
            select_tracked_application("example engineer", applications)


if __name__ == "__main__":
    unittest.main()
