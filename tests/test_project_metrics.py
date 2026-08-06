from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.project_metrics import propose_git_project_metrics


class ProjectMetricProposalTests(unittest.TestCase):
    def _git(self, repo: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def _commit(self, repo: Path, path: str, content: str, message: str, author: str) -> None:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._git(repo, "add", path)
        self._git(
            repo,
            "-c",
            f"user.name={author.split(' <', 1)[0]}",
            "-c",
            f"user.email={author.split('<', 1)[1][:-1]}",
            "commit",
            "-m",
            message,
        )

    def test_proposes_attributed_observed_metrics_without_claiming_impact(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            self._commit(
                repo,
                "src/api.py",
                "def create_order():\n    return True\n",
                "add API",
                "Adrian <adrian@example.test>",
            )
            self._commit(
                repo,
                "tests/test_api.py",
                "def test_create_order():\n    assert True\n",
                "test API",
                "Adrian <adrian@example.test>",
            )
            self._commit(
                repo,
                "README.md",
                "# Project\n",
                "docs",
                "Teammate <teammate@example.test>",
            )

            report = propose_git_project_metrics(repo, author_email="adrian@example.test")

            self.assertEqual(report.attribution, "author_email")
            self.assertEqual(report.commit_count, 2)
            self.assertEqual(report.total_commit_count, 3)
            self.assertEqual(report.commit_share_percent, 67)
            self.assertEqual(report.source_files_changed, 1)
            self.assertEqual(report.test_files_changed, 1)
            self.assertEqual(report.lines_added, 4)
            self.assertEqual(report.lines_deleted, 0)
            self.assertEqual(report.test_file_share_percent, 50)
            self.assertEqual(
                report.resume_metric_candidates,
                (
                    "Contributed 2 of 3 commits (67%) in the selected Git history.",
                    "Changed 1 source file and added tests in 1 test file "
                    "(50% of code/test files touched).",
                    "Added 4 lines of tracked code and tests across 2 files.",
                ),
            )
            self.assertTrue(report.requires_user_confirmation)
            self.assertNotIn("impact", " ".join(report.resume_metric_candidates).casefold())

    def test_rejects_an_unknown_author_before_generating_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            self._commit(
                repo,
                "src/app.py",
                "VALUE = True\n",
                "add app",
                "Adrian <adrian@example.test>",
            )

            with self.assertRaisesRegex(ValueError, "no commits matched author_email"):
                propose_git_project_metrics(repo, author_email="nobody@example.test")


if __name__ == "__main__":
    unittest.main()
