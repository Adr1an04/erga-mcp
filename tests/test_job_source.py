from __future__ import annotations

import unittest

from erga_mcp.applications.research import analyze_job_snapshot
from erga_mcp.applications.source import assess_job_source


class JobSourceTests(unittest.TestCase):
    def test_rejects_a_github_repository_even_when_its_readme_mentions_jobs(self) -> None:
        url = "https://github.com/example/career-tool.git"
        snapshot = (
            "Career assistant README. Apply to jobs, research a software engineer role, "
            "and review qualifications."
        )

        assessment = assess_job_source(
            url=url,
            snapshot=snapshot,
            research=analyze_job_snapshot(snapshot, job_url=url),
        )

        self.assertFalse(assessment.accepted)
        self.assertEqual(assessment.reason_code, "source_code_repository")

    def test_accepts_a_sparse_known_ats_posting(self) -> None:
        url = "https://jobs.ashbyhq.com/example/role-id"
        snapshot = "Software Engineer Intern"

        assessment = assess_job_source(
            url=url,
            snapshot=snapshot,
            research=analyze_job_snapshot(snapshot, job_url=url),
        )

        self.assertTrue(assessment.accepted)
        self.assertEqual(assessment.reason_code, "recognized_job_url")

    def test_accepts_structured_jobposting_on_a_generic_company_host(self) -> None:
        url = "https://example.test/team/opening-42"
        snapshot = (
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"Software Engineer Intern",'
            '"description":"Build production software.",'
            '"hiringOrganization":{"name":"Example"}}'
            "</script>"
        )

        assessment = assess_job_source(
            url=url,
            snapshot=snapshot,
            research=analyze_job_snapshot(snapshot, job_url=url),
        )

        self.assertTrue(assessment.accepted)
        self.assertEqual(assessment.reason_code, "structured_job_posting")

    def test_rejects_generic_content_with_no_job_facts(self) -> None:
        url = "https://example.test/blog/building-our-platform"
        snapshot = "How our engineers built a Python platform."

        assessment = assess_job_source(
            url=url,
            snapshot=snapshot,
            research=analyze_job_snapshot(snapshot, job_url=url),
        )

        self.assertFalse(assessment.accepted)
        self.assertEqual(assessment.reason_code, "insufficient_job_evidence")


if __name__ == "__main__":
    unittest.main()
