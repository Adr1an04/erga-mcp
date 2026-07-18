from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from recruiting_pipeline.job_workspace import create_job_workspace
from recruiting_pipeline.models import Evidence


class JobWorkspaceTests(unittest.TestCase):
    def test_creates_isolated_package_with_template_snapshot_and_approved_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "master.tex"
            template.write_text("\\section{Experience}\nold\n", encoding="utf-8")
            workspace = create_job_workspace(
                output_root=root / "output",
                cycle="Fall26",
                application_slug="ExampleCo",
                job_url="https://jobs.example.test/1",
                job_snapshot="Python engineer role",
                template_path=template,
                selected_evidence=[
                    Evidence("ev1", "Career#Project", "Python work", True, datetime.now(UTC))
                ],
            )
            self.assertEqual(
                workspace.template_copy_path.read_text(encoding="utf-8"),
                template.read_text(encoding="utf-8"),
            )
            self.assertTrue(workspace.job_snapshot_path.exists())
            self.assertIn("ev1", workspace.selected_evidence_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
