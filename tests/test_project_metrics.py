from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.project_metrics import propose_git_project_metrics, summarize_git_project_metrics


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
            self.assertEqual(report.history_scope, "latest 200 non-merge commits")
            self.assertEqual(report.commit_count, 2)
            self.assertEqual(report.total_commit_count, 3)
            self.assertEqual(report.commit_share_percent, 67)
            self.assertEqual(report.source_files_changed, 1)
            self.assertEqual(report.test_files_changed, 1)
            self.assertEqual(report.lines_added, 4)
            self.assertEqual(report.lines_deleted, 0)
            self.assertEqual(report.test_file_share_percent, 50)
            self.assertEqual(report.other_files_changed, 0)
            self.assertEqual(report.languages, ("Python",))
            self.assertEqual(report.resume_use, "engineering_context_only")
            self.assertEqual(
                report.review_facts,
                (
                    "Attributed 2 of 3 non-merge commits (67%) in the selected history.",
                    "Touched 1 unique implementation file and 1 test file.",
                    "Recorded 4 additions and 0 deletions only in recognized source and test "
                    "files.",
                ),
            )
            self.assertTrue(report.requires_user_confirmation)
            self.assertNotIn("impact", " ".join(report.review_facts).casefold())
            self.assertEqual(report.resume_metric_candidates, ())

    def test_excludes_locks_generated_assets_snapshots_and_data_from_code_totals(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            author = "Adrian <adrian@example.test>"
            self._commit(repo, "src/app.ts", "export const ready = true;\n", "app", author)
            self._commit(repo, "uv.lock", "package = []\n", "lock", author)
            self._commit(repo, "pnpm-lock.yaml", "lockfileVersion: 9\n", "lock", author)
            self._commit(repo, "public/background.svg", "<svg><path /></svg>\n", "asset", author)
            self._commit(
                repo,
                "drizzle/snapshots/schema.json",
                '{"tables": {"large": true}}\n',
                "snapshot",
                author,
            )
            self._commit(repo, "dist/bundle.js", "var generated = true;\n", "build", author)
            self._commit(
                repo,
                "venv/lib/python/site-packages/vendor.py",
                (
                    '@router.get("/fake-one")\n'
                    '@router.post("/fake-two")\n'
                    '@router.delete("/fake-three")\n'
                    'app.command("fake-one")\n'
                    'app.command("fake-two")\n'
                    'app.command("fake-three")\n'
                    "def test_fake_one(): pass\n"
                    "def test_fake_two(): pass\n"
                    "def test_fake_three(): pass\n"
                    "def test_fake_four(): pass\n"
                    "def test_fake_five(): pass\n"
                ),
                "environment",
                author,
            )

            report = propose_git_project_metrics(repo, author_email="adrian@example.test")

        self.assertEqual(report.source_files_changed, 1)
        self.assertEqual(report.test_files_changed, 0)
        self.assertEqual(report.other_files_changed, 6)
        self.assertEqual(report.files_changed, 1)
        self.assertEqual(report.lines_added, 1)
        self.assertEqual(report.languages, ("TypeScript",))
        self.assertEqual(report.attributed_test_cases, 0)
        self.assertEqual(report.attributed_http_routes, 0)
        self.assertEqual(report.attributed_cli_commands, 0)
        self.assertEqual(report.resume_metric_candidates, ())
        self.assertIn("no recognized test files", report.review_facts[1])

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

    def test_does_not_propose_resume_metrics_for_documentation_only_history(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            self._commit(
                repo,
                "README.md",
                "# Project\n",
                "document project",
                "Adrian <adrian@example.test>",
            )

            report = propose_git_project_metrics(repo, author_email="adrian@example.test")

        self.assertEqual(report.resume_metric_candidates, ())
        self.assertEqual(report.source_files_changed, 0)
        self.assertEqual(report.test_files_changed, 0)

    def test_proposes_only_meaningful_functional_scope_from_attributed_code(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            author = "Adrian <adrian@example.test>"
            routes = "\n".join(
                (
                    '@router.get("/jobs")',
                    '@router.post("/jobs")',
                    '@router.delete("/jobs/{job_id}")',
                    "def routes(): pass",
                )
            )
            tests = "\n".join(f"def test_case_{index}(): pass" for index in range(1, 6))
            self._commit(repo, "src/routes.py", routes + "\n", "add routes", author)
            self._commit(repo, "tests/test_routes.py", tests + "\n", "add tests", author)

            report = propose_git_project_metrics(repo, author_email="adrian@example.test")

        self.assertEqual(report.attributed_http_routes, 3)
        self.assertEqual(report.attributed_test_cases, 5)
        self.assertEqual(report.attributed_cli_commands, 0)
        self.assertEqual(report.resume_use, "verified_functional_scope")
        self.assertEqual(
            report.resume_metric_candidates,
            (
                "Verified 5 distinct attributed test cases present in the current repository.",
                "Verified 3 distinct attributed HTTP routes present in the current repository.",
            ),
        )
        self.assertNotIn("files", " ".join(report.resume_metric_candidates).casefold())

    def test_pipeline_summary_inspects_exact_attributed_commits_outside_recent_limit(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory)
            self._git(repo, "init")
            author = "Adrian <adrian@example.test>"
            self._commit(repo, "src/first.py", "FIRST = True\n", "first", author)
            first_sha = self._git(repo, "rev-parse", "HEAD")
            self._commit(repo, "src/second.py", "SECOND = True\n", "second", author)
            self._commit(repo, "src/third.py", "THIRD = True\n", "third", author)

            report = summarize_git_project_metrics(
                repo,
                attributed_commit_shas={first_sha},
                commit_limit=1,
            )

        self.assertEqual(report.commit_count, 1)
        self.assertEqual(report.total_commit_count, 3)
        self.assertEqual(report.history_scope, "all reachable non-merge commits")
        self.assertEqual(report.source_files_changed, 1)


if __name__ == "__main__":
    unittest.main()
