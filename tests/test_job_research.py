from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.job_research import (
    analyze_job_snapshot,
    official_job_text,
    render_job_research,
    write_job_research,
    write_secondary_research,
)


class JobResearchTests(unittest.TestCase):
    def test_uses_visible_main_content_instead_of_scripts_or_page_chrome(self) -> None:
        snapshot = """
        <html><head>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Systems Intern",
         "hiringOrganization":{"name":"Example"}}
        </script>
        <script>const React = "JavaScript"; const legal = "laws trust expressed";</script>
        </head><body>
        <nav>Developer site built with React and AWS</nav>
        <main>
          <h1>Systems Intern</h1>
          <p>Write production services in C++, Python, or Java.</p>
          <p>Improve reliable real-time systems.</p>
        </main>
        <footer>JavaScript CSS Rust AWS Express</footer>
        </body></html>
        """

        visible = official_job_text(snapshot)
        research = analyze_job_snapshot(snapshot, job_url="https://jobs.example.test/123")

        self.assertIn("C++", visible)
        self.assertNotIn("React", visible)
        self.assertEqual(research.skills, ("C++", "Python", "Java"))

    def test_extracts_greenhouse_role_sections_constraints_and_conflicts(self) -> None:
        greenhouse = {
            "state": {
                "loaderData": {
                    "route": {
                        "jobPost": {
                            "post_type": "job_post",
                            "title": (
                                "Website Developer & Content Marketing Intern - AI & Automation"
                            ),
                            "company_name": "Example Labs",
                            "job_post_location": "Sample City, NY",
                            "published_at": "2026-07-03T09:57:27-04:00",
                            "employment": "hidden",
                            "content": (
                                "<p><strong>Core Responsibilities</strong></p>"
                                "<ul><li>Build pages using HTML, CSS, and JavaScript.</li>"
                                "<li>Build publishing automations with n8n or Zapier.</li>"
                                "<li>About 20 percent of the work supports analytics.</li></ul>"
                                "<p><strong>Requirements</strong></p>"
                                "<ul><li>Show a portfolio of shipped work.</li></ul>"
                                "<p><strong>Details</strong></p>"
                                "<ul><li>Hybrid, 2 days per week in our "
                                "Sample City office.</li></ul>"
                                "<p><strong>About Example Labs</strong></p>"
                                "<p>Example Labs builds synthetic test products.</p>"
                                "<h2><strong>Logistics</strong></h2>"
                                "<ul><li>Summer 2026.</li>"
                                "<li>Hybrid, 4 days per week in person.</li>"
                                "<li>$18 to $20 per hour.</li>"
                                "<li>Complete the application form: "
                                "https://forms.gle/exampleForm</li></ul>"
                            ),
                        }
                    }
                }
            }
        }
        snapshot = "window.__remixContext = " + json.dumps(greenhouse) + ";"

        research = analyze_job_snapshot(
            snapshot,
            job_url="https://job-boards.greenhouse.io/example-labs/jobs/1234567890",
        )

        self.assertEqual(research.company, "Example Labs")
        self.assertEqual(
            research.role,
            "Website Developer & Content Marketing Intern - AI & Automation",
        )
        self.assertEqual(research.cycles, ("Summer 2026",))
        self.assertEqual(research.location, "Hybrid — Sample City, NY")
        self.assertEqual(research.compensation, "$18–$20/hour")
        self.assertIn("HTML", research.skills)
        self.assertEqual(len(research.responsibilities), 3)
        self.assertEqual(len(research.qualifications), 1)
        self.assertIn("conflicting in-office expectations", research.ambiguities[0])
        self.assertIn("https://forms.gle/exampleForm", research.application_constraints[0])

    def test_extracts_grounded_facts_cycles_and_role_signals(self) -> None:
        posting = {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "Software Engineering Internship (Fall 2026/Summer 2027)",
            "description": (
                "Build and ship one project end to end into production. "
                "Use Codex for testing and debugging real-time voice AI, ASR, and TTS. "
                "Reason from first principles and show projects, tools, scripts, or automations."
            ),
            "datePosted": "2026-07-17",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"name": "Example Voice"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": {"name": "United States"},
            "baseSalary": {
                "currency": "USD",
                "value": {
                    "minValue": 55,
                    "maxValue": 65,
                    "unitText": "HOUR",
                },
            },
        }
        snapshot = (
            "Software Engineering Internship @ Example Voice "
            + json.dumps(posting)
            + ' window.__appData={"applicationLimitCalloutHtml":"No more than two applications."}'
        )

        research = analyze_job_snapshot(snapshot, job_url="https://jobs.example.test/123")

        self.assertEqual(research.company, "Example Voice")
        self.assertEqual(research.role, "Software Engineering Internship")
        self.assertEqual(research.cycles, ("Fall 2026", "Summer 2027"))
        self.assertEqual(research.location, "Remote — United States")
        self.assertEqual(research.compensation, "$55–$65/hour")
        self.assertGreaterEqual(len(research.highlights), 4)
        self.assertEqual(research.application_constraints, ("No more than two applications.",))

    def test_renders_cited_research_and_writes_it_idempotently(self) -> None:
        snapshot = (
            'Role @ Example {"@type":"JobPosting","title":"Role",'
            '"hiringOrganization":{"name":"Example"},"description":"Build systems."}'
        )
        research = analyze_job_snapshot(snapshot, job_url="https://jobs.example.test/123")
        rendered = render_job_research(
            research,
            captured_at="2026-07-18T00:00:00+00:00",
            approved_evidence_count=2,
        )

        self.assertIn("[Official job posting](https://jobs.example.test/123)", rendered)
        self.assertIn("2 approved career-evidence record(s)", rendered)
        self.assertIn("Missing details remain missing", rendered)

        with TemporaryDirectory() as directory:
            package_dir = Path(directory)
            first = write_job_research(
                package_dir=package_dir,
                research=research,
                captured_at="2026-07-18T00:00:00+00:00",
                approved_evidence_count=2,
            )
            original_mtime = first.stat().st_mtime_ns
            second = write_job_research(
                package_dir=package_dir,
                research=research,
                captured_at="2026-07-18T00:00:00+00:00",
                approved_evidence_count=2,
            )

            self.assertEqual(first, second)
            self.assertEqual(second.stat().st_mtime_ns, original_mtime)

    def test_secondary_search_results_are_readable_cited_and_separate(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory)
            research_dir = package_dir / "research"
            research_dir.mkdir()
            (research_dir / "role-research.md").write_text(
                "# Example — Role research\n", encoding="utf-8"
            )
            raw = json.dumps(
                {
                    "success": True,
                    "data": {
                        "web": [
                            {
                                "title": "Community interview thread",
                                "url": "https://www.reddit.com/r/example/comments/123/thread/",
                                "description": "One candidate's older experience.",
                            }
                        ]
                    },
                }
            )

            path = write_secondary_research(
                package_dir=package_dir,
                searches=[("Example internship site:reddit.com", raw)],
                captured_at="2026-07-18T00:00:00+00:00",
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn("unverified", text)
            self.assertIn(
                "[Community interview thread](https://www.reddit.com/r/example/comments/123/thread/)",
                text,
            )
            self.assertIn(
                "[secondary online research](secondary-research.md)",
                (research_dir / "role-research.md").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
