from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.github_projects import (
    connected_github_login,
    discover_github_projects,
    find_local_github_worktree,
    github_authored_commit_shas,
)


class GitHubProjectsTests(unittest.TestCase):
    def test_refreshes_private_json_project_index_from_owned_and_collaborator_repositories(
        self,
    ) -> None:
        response = json.dumps(
            [
                [
                    {
                        "full_name": "example/api-platform",
                        "name": "api-platform",
                        "description": "Typed backend APIs",
                        "language": "Python",
                        "topics": ["backend", "api"],
                        "archived": False,
                        "disabled": False,
                    },
                    {
                        "full_name": "example/old-project",
                        "name": "old-project",
                        "archived": True,
                    },
                ]
            ]
        )

        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(arguments, 0, response, "")

        with TemporaryDirectory() as directory:
            cache_path = Path(directory) / "private" / "github-project-catalogue.json"

            projects = discover_github_projects(cache_path=cache_path, runner=runner)
            cached = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual([project.repository for project in projects], ["example/api-platform"])
        self.assertEqual(projects[0].topics, ("backend", "api"))
        self.assertEqual(cached[0]["repository"], "example/api-platform")

    def test_reads_only_valid_connected_login_and_commit_shas(self) -> None:
        calls: list[list[str]] = []

        def runner(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            if arguments[:3] == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(arguments, 0, "sample-user\n", "")
            return subprocess.CompletedProcess(
                arguments,
                0,
                "a" * 40 + "\nnot-a-sha\n" + "b" * 40 + "\n",
                "",
            )

        login = connected_github_login(runner=runner)
        shas = github_authored_commit_shas("example/project", login=login, runner=runner)

        self.assertEqual(login, "sample-user")
        self.assertEqual(shas, {"a" * 40, "b" * 40})
        self.assertIn("author=sample-user", calls[1][3])
        self.assertNotIn("token", " ".join(value for call in calls for value in call).casefold())

    def test_matches_only_an_explicit_known_local_github_remote(self) -> None:
        with TemporaryDirectory() as directory:
            repo = Path(directory) / "project"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "git@github.com:example/project.git"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            matched = find_local_github_worktree("example/project", [repo])
            missing = find_local_github_worktree("example/other", [repo])

        self.assertEqual(matched, repo.resolve())
        self.assertIsNone(missing)


if __name__ == "__main__":
    unittest.main()
