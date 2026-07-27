from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from ddgs.exceptions import DDGSException

from erga_mcp.job_discovery import (
    _is_concrete_technical_report,
    _is_relevant_community_result,
    _search,
)
from erga_mcp.models import Application
from erga_mcp.web_scraping import ScrapedPage


class JobDiscoveryTests(unittest.TestCase):
    def test_technical_research_rejects_generic_advice_without_reported_details(self) -> None:
        self.assertFalse(
            _is_concrete_technical_report(
                {
                    "title": "Google technical interview guide",
                    "href": "https://example.test/google-guide",
                    "body": "Study data structures and algorithms for a Google internship.",
                },
                company="Google",
            )
        )
        self.assertTrue(
            _is_concrete_technical_report(
                {
                    "title": "Google SWE intern technical interview experience",
                    "href": "https://example.test/google-swe-intern-report",
                    "body": (
                        "One medium-hard graph question followed by two follow-ups; "
                        "another round used dynamic programming."
                    ),
                },
                company="Google",
            )
        )

    def test_community_relevance_requires_google_and_internship_role_context(self) -> None:
        self.assertTrue(
            _is_relevant_community_result(
                {
                    "title": "Link to reddit.com",
                    "href": "https://www.reddit.com/r/csMajors/comments/example/google_swe_intern/",
                    "body": "",
                },
                company="Google",
            )
        )
        self.assertFalse(
            _is_relevant_community_result(
                {
                    "title": "Link to reddit.com",
                    "href": "https://www.reddit.com/r/csMajors/comments/example/openai/",
                    "body": "",
                },
                company="Google",
            )
        )

    @patch("erga_mcp.job_discovery.DDGS")
    def test_search_uses_yahoo_backend_to_avoid_relative_redirect_results(
        self, ddgs: object
    ) -> None:
        client = ddgs.return_value  # type: ignore[attr-defined]
        client.text.return_value = [  # type: ignore[attr-defined]
            {
                "title": "Google internship interview tips",
                "href": "https://www.reddit.com/r/csMajors/comments/example/",
                "body": "Candidate discussion.",
            },
            {
                "title": "Search redirect",
                "href": "/clev?event=StartpageResultClick",
                "body": "Not a source URL.",
            },
        ]

        results = _search("Google software engineering internship", max_results=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["href"], "https://www.reddit.com/r/csMajors/comments/example/")
        client.text.assert_called_once_with(  # type: ignore[attr-defined]
            "Google software engineering internship", max_results=3, backend="yahoo"
        )

    @patch("erga_mcp.job_discovery.DDGS")
    def test_search_falls_back_to_bing_when_yahoo_has_no_results(self, ddgs: object) -> None:
        yahoo = MagicMock()
        bing = MagicMock()
        yahoo.text.side_effect = DDGSException("No results found.")
        bing.text.return_value = [
            {
                "title": "Google intern discussion",
                "href": "https://www.reddit.com/r/csMajors/comments/example/",
                "body": "Candidate discussion.",
            }
        ]
        ddgs.side_effect = [yahoo, bing]  # type: ignore[attr-defined]

        results = _search("Google software engineering internship", max_results=3)

        self.assertEqual(results[0]["title"], "Google intern discussion")
        yahoo.text.assert_called_once_with(  # type: ignore[attr-defined]
            "Google software engineering internship", max_results=3, backend="yahoo"
        )
        bing.text.assert_called_once_with(  # type: ignore[attr-defined]
            "Google software engineering internship", max_results=3, backend="bing"
        )

    def test_deduplicates_the_posting_when_search_returns_its_canonical_url(self) -> None:
        from erga_mcp.job_discovery import discover_job_research

        tracked_url = "https://careers.example.test/google-intern?tracking=abc"
        application = Application(
            id="app_google",
            company="Google",
            role="Intern",
            source_url=tracked_url,
            status="applied",
            evidence_ids=[],
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

        def search(query: str, *, max_results: int) -> list[dict[str, str]]:
            if "site:careers.example.test" in query:
                return [
                    {
                        "title": "Canonical posting",
                        "href": "https://careers.example.test/google-intern",
                        "body": "Same job.",
                    }
                ]
            return []

        def scrape(url: str, *, max_characters: int, max_links: int) -> ScrapedPage:
            return ScrapedPage(
                url=url,
                title="Google Intern",
                text="Useful excerpt.",
                links=(),
                untrusted=True,
            )

        with TemporaryDirectory() as directory:
            result = discover_job_research(
                application=application,
                package_dir=Path(directory),
                search=search,
                scrape=scrape,
                captured_at=datetime(2026, 7, 27, tzinfo=UTC),
            )

        self.assertEqual(result.sources_scraped, 1)

    def test_falls_back_to_broader_community_search_when_exact_role_has_no_results(self) -> None:
        from erga_mcp.job_discovery import discover_job_research

        application = Application(
            id="app_google",
            company="Google",
            role="Job Opportunity",
            source_url="https://careers.example.test/google-intern",
            status="applied",
            evidence_ids=[],
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        searched: list[str] = []

        def search(query: str, *, max_results: int) -> list[dict[str, str]]:
            searched.append(query)
            if "site:reddit.com" in query and "Summer 2027" in query:
                return []
            if "site:reddit.com" in query:
                return [
                    {
                        "title": "Google intern discussion - Reddit",
                        "href": "https://www.reddit.com/r/csMajors/comments/example/google/",
                        "body": "Relevant candidate discussion.",
                    }
                ]
            return []

        def scrape(url: str, *, max_characters: int, max_links: int) -> ScrapedPage:
            return ScrapedPage(
                url=url,
                title="Software Engineering Intern, BS, Summer 2027",
                text="Useful excerpt.",
                links=(),
                untrusted=True,
            )

        with TemporaryDirectory() as directory:
            result = discover_job_research(
                application=application,
                package_dir=Path(directory),
                search=search,
                scrape=scrape,
                captured_at=datetime(2026, 7, 27, tzinfo=UTC),
            )
            text = result.path.read_text(encoding="utf-8")

        self.assertIn("Google software engineering internship interview site:reddit.com", searched)
        self.assertIn("Google intern discussion - Reddit", text)

    def test_uses_posting_title_and_filters_irrelevant_results(self) -> None:
        from erga_mcp.job_discovery import discover_job_research

        application = Application(
            id="app_google",
            company="Google",
            role="Job Opportunity",
            source_url="https://careers.example.test/google-intern",
            status="applied",
            evidence_ids=[],
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        searched: list[str] = []

        def search(query: str, *, max_results: int) -> list[dict[str, str]]:
            searched.append(query)
            if "site:reddit.com" in query:
                return [
                    {
                        "title": "Relevant Reddit discussion",
                        "href": "https://www.reddit.com/r/csMajors/comments/example/google/",
                        "body": "Interview discussion.",
                    }
                ]
            if "recruiter" in query:
                return [
                    {
                        "title": "Google engineer | LinkedIn",
                        "href": "https://www.linkedin.com/in/engineer/",
                        "body": "Works at Google.",
                    },
                    {
                        "title": "Google university recruiter | LinkedIn",
                        "href": "https://www.linkedin.com/in/recruiter/",
                        "body": "Recruiter at Google.",
                    },
                    {
                        "title": "One Recruiter | LinkedIn Another Recruiter | LinkedIn",
                        "href": "https://www.linkedin.com/in/aggregated/",
                        "body": "Recruiter at Google.",
                    },
                ]
            return [
                {
                    "title": "Unrelated job board",
                    "href": "https://jobs.other.example.test/role",
                    "body": "Not a Google source.",
                },
                {
                    "title": "Google careers information",
                    "href": "https://careers.example.test/internships",
                    "body": "Official information.",
                },
            ]

        def scrape(url: str, *, max_characters: int, max_links: int) -> ScrapedPage:
            title = (
                "Software Engineering Intern, BS, Summer 2027"
                if url == application.source_url
                else "Source"
            )
            return ScrapedPage(
                url=url, title=title, text="Useful excerpt.", links=(), untrusted=True
            )

        with TemporaryDirectory() as directory:
            result = discover_job_research(
                application=application,
                package_dir=Path(directory),
                search=search,
                scrape=scrape,
                captured_at=datetime(2026, 7, 27, tzinfo=UTC),
            )
            text = result.path.read_text(encoding="utf-8")

        self.assertTrue(
            any("Software Engineering Intern, BS, Summer 2027" in query for query in searched)
        )
        self.assertIn("https://careers.example.test/internships", text)
        self.assertNotIn("https://jobs.other.example.test/role", text)
        self.assertIn("https://www.linkedin.com/in/recruiter/", text)
        self.assertNotIn("https://www.linkedin.com/in/aggregated/", text)
        self.assertNotIn("https://www.linkedin.com/in/engineer/", text)

    def test_researches_posting_official_and_community_sources_into_cited_note(self) -> None:
        from erga_mcp.job_discovery import discover_job_research

        application = Application(
            id="app_google",
            company="Google",
            role="Software Engineering Intern",
            source_url="https://careers.example.test/google-intern",
            status="applied",
            evidence_ids=[],
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        searched: list[str] = []
        scraped: list[str] = []

        def search(query: str, *, max_results: int) -> list[dict[str, str]]:
            searched.append(query)
            if "site:reddit.com" in query:
                return [
                    {
                        "title": "Google internship interview discussion - Reddit",
                        "href": "https://www.reddit.com/r/csMajors/comments/example/google-intern/",
                        "body": "Candidate discussion about explaining technical decisions.",
                    }
                ]
            if "technical interview study" in query:
                return [
                    {
                        "title": "Google SWE intern technical interview study guide",
                        "href": "https://example.test/google-swe-technical-study",
                        "body": (
                            "One medium graph question followed by two follow-ups; another round "
                            "used dynamic programming."
                        ),
                    }
                ]
            if "recruiter" in query:
                return [
                    {
                        "title": "Google University Recruiter | LinkedIn",
                        "href": "https://www.linkedin.com/in/example-recruiter/",
                        "body": "Public professional profile result.",
                    }
                ]
            return [
                {
                    "title": "Google internships | Google Careers",
                    "href": "https://careers.example.test/google-internships",
                    "body": "Official student-program information.",
                }
            ]

        def scrape(url: str, *, max_characters: int, max_links: int) -> ScrapedPage:
            scraped.append(url)
            return ScrapedPage(
                url=url,
                title="Scraped source",
                text=f"Bounded visible text for {url}",
                links=(),
                untrusted=True,
            )

        with TemporaryDirectory() as directory:
            result = discover_job_research(
                application=application,
                package_dir=Path(directory),
                search=search,
                scrape=scrape,
                captured_at=datetime(2026, 7, 27, tzinfo=UTC),
            )
            text = result.path.read_text(encoding="utf-8")

        self.assertEqual(result.sources_scraped, 4)
        self.assertIn(application.source_url, scraped)
        self.assertTrue(any("site:reddit.com" in query for query in searched))
        self.assertTrue(any("technical interview study" in query for query in searched))
        self.assertTrue(any("recruiter" in query for query in searched))
        self.assertIn("## Official sources", text)
        self.assertIn("## Community sources (unverified)", text)
        self.assertIn("## Technical interview study (unverified)", text)
        self.assertIn("Trees, graphs, and hashing", text)
        self.assertIn("https://example.test/google-swe-technical-study", text)
        self.assertIn("One medium graph question followed by two follow-ups", text)
        self.assertIn("## Public outreach leads (review before contact)", text)
        self.assertIn("https://www.linkedin.com/in/example-recruiter/", text)
        self.assertIn("Bounded visible text", text)
        self.assertIn("No message was sent", text)


if __name__ == "__main__":
    unittest.main()
