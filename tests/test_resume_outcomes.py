from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.models import Application
from erga_mcp.resumes.outcomes import build_resume_outcome_report


class ResumeOutcomeTests(unittest.TestCase):
    def test_uses_only_explicit_resume_versions_and_requires_directional_sample(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            applications = []
            for index, status in enumerate(("interview", "rejected", "offer"), 1):
                application_id = f"app_{index}"
                version_id = f"resume_{index}"
                applications.append(
                    Application(
                        id=application_id,
                        company="Synthetic",
                        role="Engineer",
                        source_url=f"https://jobs.example.test/{index}",
                        status=status,
                        evidence_ids=[],
                        created_at=datetime.now(UTC),
                    )
                )
                package = root / "fall-2026" / f"role-{index}"
                package.mkdir(parents=True)
                (package / "package.json").write_text(
                    json.dumps(
                        {
                            "used_resume_version_id": version_id,
                            "resume_versions": [
                                {
                                    "id": version_id,
                                    "application_id": application_id,
                                    "project_ids": ["platform"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            report = build_resume_outcome_report(
                applications=applications,
                output_root=root,
            )

            self.assertEqual(report["explicitly_used_resume_versions"], 3)
            signal = report["project_signals"][0]
            self.assertEqual(signal["interview_or_better"], 2)
            self.assertTrue(signal["eligible_for_directional_personalization"])
            self.assertIn("do not prove", report["interpretation"])


if __name__ == "__main__":
    unittest.main()
