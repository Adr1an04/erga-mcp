from __future__ import annotations

import unittest

from erga_mcp.applications.identity import job_identity, metadata_from_url


class JobIdentityTests(unittest.TestCase):
    def test_opaque_ats_ids_produce_stable_distinct_package_slugs(self) -> None:
        first = metadata_from_url(
            "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000001",
            cycle="fall-2026",
            application_slug="",
        )
        second = metadata_from_url(
            "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000002",
            cycle="fall-2026",
            application_slug="",
        )

        self.assertEqual(first[0], "fall-2026")
        self.assertEqual(second[0], "fall-2026")
        self.assertNotEqual(first[1], second[1])
        self.assertRegex(first[1], r"example-job-opportunity-[0-9a-f]{16}$")
        self.assertRegex(second[1], r"example-job-opportunity-[0-9a-f]{16}$")

    def test_query_posting_ids_and_long_roles_keep_distinct_slug_suffixes(self) -> None:
        first = metadata_from_url(
            "https://www.indeed.com/viewjob?jk=posting-one&utm_source=chat",
            cycle="",
            application_slug="",
        )
        second = metadata_from_url(
            "https://www.indeed.com/viewjob?jk=posting-two&utm_source=chat",
            cycle="",
            application_slug="",
        )
        long_role = metadata_from_url(
            "https://careers.example.test/jobs/"
            + "principal-software-engineer-for-real-time-distributed-audio-systems-" * 3,
            cycle="",
            application_slug="",
        )

        self.assertEqual(first[0], "unsorted")
        self.assertNotEqual(first[1], second[1])
        self.assertRegex(first[1], r"indeed-job-opportunity-[0-9a-f]{16}$")
        self.assertRegex(second[1], r"indeed-job-opportunity-[0-9a-f]{16}$")
        self.assertLessEqual(len(long_role[1]), 80)
        self.assertRegex(long_role[1], r"-[0-9a-f]{16}$")

    def test_tracking_parameters_do_not_change_identity_but_posting_queries_and_ports_do(
        self,
    ) -> None:
        base = "https://jobs.example.test/view?jk=one"

        self.assertEqual(
            job_identity(f"{base}&utm_source=discord&source=mobile"),
            job_identity(f"{base}&source=website&utm_campaign=summer"),
        )
        self.assertNotEqual(job_identity(base), job_identity(base.replace("one", "two")))
        self.assertNotEqual(job_identity(base), job_identity(base.replace(".test", ".test:8443")))


if __name__ == "__main__":
    unittest.main()
