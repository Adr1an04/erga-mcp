from __future__ import annotations

import json
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cli import main


class GitEvidenceCliTests(unittest.TestCase):
    def _git(self, repo: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _commit(self, repo: Path, path: str, content: str, message: str) -> str:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(content, encoding="utf-8")
        self._git(repo, "add", path)
        self._git(repo, "commit", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    def _run(self, arguments: list[str]) -> tuple[int, dict[str, object] | list[dict[str, object]]]:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(arguments)
        payload = json.loads(output.getvalue())
        assert isinstance(payload, (dict, list))
        return exit_code, payload

    def test_scan_creates_unapproved_candidates_and_approval_creates_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            repo = root / "sample-repo"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.test")
            self._git(repo, "config", "user.name", "Test User")
            commit_sha = self._commit(
                repo,
                "src/cache.py",
                "def cache():\n    return True\n",
                "Implement resilient cache",
            )
            self._commit(repo, "README.md", "# Sample\n", "Update documentation")
            main(["init", "--config", str(config_path)])

            scan_code, scan = self._run(["git", "scan", str(repo), "--config", str(config_path)])
            candidates_code, candidates = self._run(
                ["git", "candidates", "--config", str(config_path)]
            )

            self.assertEqual(scan_code, 0)
            self.assertEqual(scan["created"], 1)
            self.assertEqual(candidates_code, 0)
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertFalse(candidate["approved"])
            self.assertEqual(candidate["repo_path"], str(repo.resolve()))
            self.assertEqual(candidate["commit_sha"], commit_sha)
            self.assertIn(commit_sha, candidate["commit_range"])

            approve_code, evidence = self._run(
                ["git", "approve", str(candidate["id"]), "--config", str(config_path)]
            )

            self.assertEqual(approve_code, 0)
            self.assertTrue(evidence["approved"])
            self.assertIn(commit_sha, evidence["source_ref"])

    def test_scan_persists_local_commit_metadata_research_draft(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            repo = root / "sample-repo"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.test")
            self._git(repo, "config", "user.name", "Test User")
            scheduler_sha = self._commit(
                repo,
                "src/scheduler.py",
                "def schedule(): pass\n",
                "Implement asynchronous job scheduler",
            )
            retry_sha = self._commit(
                repo,
                "src/retry.py",
                "def retry(): pass\n",
                "Add retry controls",
            )
            retry_workers_sha = self._commit(
                repo,
                "src/retry_workers.py",
                "def retry_workers(): pass\n",
                "Add retry controls for worker queues",
            )
            self._commit(
                repo, ".github/workflows/ci.yml", "name: CI\n", "chore: update CI workflow"
            )
            self._commit(repo, "src/version.py", "VERSION = '1.0.0'\n", "Release version 1.0.0")
            self._commit(repo, "src/style.py", "VALUE=1\n", "Format codebase")
            main(["init", "--config", str(config_path)])

            scan_code, scan = self._run(["git", "scan", str(repo), "--config", str(config_path)])
            research_code, drafts = self._run(["git", "research", "--config", str(config_path)])
            _, status = self._run(["status", "--config", str(config_path)])

            self.assertEqual(scan_code, 0)
            self.assertEqual(research_code, 0)
            self.assertEqual(scan["research_drafts"], 1)
            self.assertEqual(len(drafts), 1)
            draft = drafts[0]
            self.assertTrue(draft["generated_from_commit_metadata"])
            self.assertTrue(draft["needs_review"])
            self.assertIn("needs review", draft["summary"].casefold())
            self.assertEqual(len(draft["bullet_candidates"]), 2)
            self.assertEqual(
                [bullet["text"] for bullet in draft["bullet_candidates"]],
                ["Add retry controls for worker queues", "Implement asynchronous job scheduler"],
            )
            source_shas = {
                sha for bullet in draft["bullet_candidates"] for sha in bullet["source_commit_shas"]
            }
            self.assertEqual(source_shas, {scheduler_sha, retry_sha, retry_workers_sha})
            self.assertTrue(
                all(bullet["source_candidate_ids"] for bullet in draft["bullet_candidates"])
            )
            self.assertEqual(status["evidence"], 0)

    def test_incremental_scan_only_creates_new_high_signal_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            repo = root / "sample-repo"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.test")
            self._git(repo, "config", "user.name", "Test User")
            first_sha = self._commit(repo, "src/api.py", "def first(): pass\n", "Add API endpoint")
            main(["init", "--config", str(config_path)])
            self._run(["git", "scan", str(repo), "--config", str(config_path)])

            second_sha = self._commit(
                repo, "src/api.py", "def second(): pass\n", "Add retry handling"
            )
            head_sha = self._commit(repo, "package-lock.json", "{}\n", "Refresh lockfile")

            scan_code, scan = self._run(["git", "scan", str(repo), "--config", str(config_path)])
            _, candidates = self._run(["git", "candidates", "--config", str(config_path)])

            self.assertEqual(scan_code, 0)
            self.assertEqual(scan["created"], 1)
            self.assertEqual(scan["previous_checkpoint"], first_sha)
            self.assertEqual(scan["checkpoint"], head_sha)
            self.assertEqual(
                [candidate["commit_sha"] for candidate in candidates], [first_sha, second_sha]
            )

    def test_scan_all_discovers_each_worktree_under_a_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            projects = root / "projects"
            for name in ("one", "two"):
                repo = projects / name
                repo.mkdir(parents=True)
                self._git(repo, "init")
                self._git(repo, "config", "user.email", "test@example.test")
                self._git(repo, "config", "user.name", "Test User")
                self._commit(repo, "src/main.py", "VALUE = True\n", f"Implement {name} feature")
            main(["init", "--config", str(config_path)])

            code, scan = self._run(
                ["git", "scan", "--all", "--root", str(projects), "--config", str(config_path)]
            )
            _, candidates = self._run(["git", "candidates", "--config", str(config_path)])

            self.assertEqual(code, 0)
            self.assertEqual(scan["repositories_scanned"], 2)
            self.assertEqual(scan["created"], 2)
            self.assertEqual(len(candidates), 2)

    def test_scan_rejects_non_worktree(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            main(["init", "--config", str(config_path)])

            with self.assertRaisesRegex(ValueError, "local git worktree"):
                main(["git", "scan", str(root), "--config", str(config_path)])

    def test_scan_filters_merge_commits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            repo = root / "sample-repo"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.test")
            self._git(repo, "config", "user.name", "Test User")
            self._commit(repo, "src/base.py", "BASE = True\n", "Add base implementation")
            branch = self._git(repo, "branch", "--show-current")
            self._git(repo, "checkout", "-b", "feature")
            self._commit(repo, "src/feature.py", "FEATURE = True\n", "Add feature implementation")
            self._git(repo, "checkout", branch)
            self._commit(repo, "src/main.py", "MAIN = True\n", "Add main implementation")
            self._git(repo, "merge", "--no-ff", "feature", "-m", "Merge feature branch")
            merge_sha = self._git(repo, "rev-parse", "HEAD")
            main(["init", "--config", str(config_path)])

            self._run(["git", "scan", str(repo), "--config", str(config_path)])
            _, candidates = self._run(["git", "candidates", "--config", str(config_path)])

            self.assertNotIn(merge_sha, [candidate["commit_sha"] for candidate in candidates])


if __name__ == "__main__":
    unittest.main()
