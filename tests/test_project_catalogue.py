from __future__ import annotations

import asyncio
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.cli import main
from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.mcp.server import build_server
from erga_mcp.portfolio.catalogue import build_project_catalogue
from erga_mcp.portfolio.github import GitHubProject
from erga_mcp.store import ErgaStore


class ProjectCatalogueTests(unittest.TestCase):
    def _git(self, repo: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def test_catalogue_merges_approved_inventory_cached_github_and_local_activity(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            alpha = projects / "alpha"
            alpha.mkdir(parents=True)
            self._git(alpha, "init")
            self._git(alpha, "config", "user.email", "test@example.test")
            self._git(alpha, "config", "user.name", "Test User")
            self._git(alpha, "remote", "add", "origin", "https://github.com/example/alpha.git")
            (alpha / "app.py").write_text("print('alpha')\n", encoding="utf-8")
            self._git(alpha, "add", "app.py")
            self._git(alpha, "commit", "-m", "Build Python API")

            config_path = root / "config.toml"
            inventory_path = root / "projects.json"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    "portfolio_roots = []",
                    f"portfolio_roots = [{json.dumps(str(projects))}]",
                ).replace(
                    'project_inventory_path = ""',
                    f"project_inventory_path = {json.dumps(str(inventory_path))}",
                ),
                encoding="utf-8",
            )
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")
            evidence = store.add_evidence(
                source_ref="fixture:alpha",
                text="Built the Alpha Python API.",
                approved=True,
            )
            inventory_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "alpha",
                            "title": "Alpha",
                            "latex": (
                                r"\resumeProjectHeading{\textbf{Alpha}}{}"
                                "\n"
                                r"\resumeItemListStart"
                                "\n"
                                r"\resumeItem{Built a Python API for synthetic tests.}"
                                "\n"
                                r"\resumeItemListEnd"
                            ),
                            "evidence_ids": [evidence.id],
                            "bullet_evidence_ids": [[evidence.id]],
                            "tags": ["Python", "FastAPI"],
                            "git_repositories": ["example/alpha"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            cache = config.data_dir / "github-project-catalogue.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    [
                        {
                            "repository": "example/alpha",
                            "name": "alpha",
                            "description": "Approved API",
                            "language": "Python",
                            "topics": ["fastapi"],
                        },
                        {
                            "repository": "example/beta",
                            "name": "beta",
                            "description": "Unreviewed frontend",
                            "language": "TypeScript",
                            "topics": ["react"],
                        },
                    ]
                ),
                encoding="utf-8",
            )

            catalogue = build_project_catalogue(config, store, page_size=10)

            by_id = {entry.id: entry for entry in catalogue.entries}
            self.assertEqual(catalogue.total_entries, 2)
            self.assertEqual(catalogue.eligible_entries, 1)
            self.assertEqual(catalogue.needs_evidence_entries, 1)
            self.assertEqual(by_id["alpha"].resume_eligibility, "Eligible")
            self.assertEqual(by_id["alpha"].approved_evidence_count, 1)
            self.assertEqual(by_id["alpha"].supported_bullet_count, 1)
            self.assertGreater(by_id["alpha"].bullet_quality_score, 0)
            self.assertEqual(by_id["alpha"].evidence_tier, "B")
            self.assertEqual(by_id["alpha"].metric_categories, ())
            self.assertEqual(by_id["alpha"].repository_url, "https://github.com/example/alpha")
            self.assertEqual(by_id["alpha"].repositories, ("example/alpha",))
            self.assertTrue(by_id["alpha"].local_clone)
            self.assertIsNotNone(by_id["alpha"].last_activity)
            alpha_field = next(field for field in catalogue.card.fields if field.name == "Alpha")
            self.assertIn("Quality:", alpha_field.value)
            self.assertIn("Evidence tier B", alpha_field.value)
            beta = next(entry for entry in catalogue.entries if entry.title == "Beta")
            self.assertEqual(beta.resume_eligibility, "Needs approved evidence")
            self.assertEqual(beta.source, "GitHub discovery")
            self.assertIn("typescript", beta.technologies)
            self.assertEqual(beta.bullet_quality_score, 0)
            self.assertEqual(beta.evidence_tier, "C")
            self.assertEqual(store.list_evidence(), [evidence])

    def test_catalogue_search_and_pagination_are_stable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            cache = config.data_dir / "github-project-catalogue.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    [
                        {
                            "repository": f"example/project-{index}",
                            "name": f"project-{index}",
                            "description": "",
                            "language": "Python" if index == 4 else "TypeScript",
                            "topics": [],
                        }
                        for index in range(5)
                    ]
                ),
                encoding="utf-8",
            )
            store = ErgaStore(config.data_dir / "erga.sqlite3")

            second = build_project_catalogue(config, store, page=2, page_size=2)
            searched = build_project_catalogue(config, store, query="python", page_size=6)

            self.assertEqual(second.page_count, 3)
            self.assertEqual(len(second.entries), 2)
            self.assertEqual(second.card.actions[-1].action_id, "project.catalogue.next")
            self.assertEqual(searched.total_entries, 1)
            self.assertEqual(searched.eligible_entries, 0)
            self.assertEqual(searched.needs_evidence_entries, 1)
            self.assertEqual(searched.needs_review_entries, 0)
            self.assertEqual(searched.entries[0].repository, "example/project-4")

    def test_mcp_project_catalogue_returns_the_shared_card(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            cache = config.data_dir / "github-project-catalogue.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    [
                        {
                            "repository": "example/catalogue",
                            "name": "catalogue",
                            "description": "Project browser",
                            "language": "Python",
                            "topics": ["mcp"],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = asyncio.run(
                build_server(config_path).call_tool(
                    "project_catalogue", {"page": 1, "page_size": 6, "query": "catalogue"}
                )
            )
            payload = result.structured_content

            self.assertEqual(payload["total_entries"], 1)
            self.assertEqual(payload["entries"][0]["repository"], "example/catalogue")
            self.assertEqual(payload["card"]["title"], "Project catalogue")

    def test_cli_project_catalogue_outputs_machine_readable_page(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            cache = config.data_dir / "github-project-catalogue.json"
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(
                json.dumps(
                    [
                        {
                            "repository": "example/cli-project",
                            "name": "cli-project",
                            "description": "Command-line project catalogue",
                            "language": "Python",
                            "topics": ["cli"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "git",
                        "projects",
                        "--config",
                        str(config_path),
                        "--query",
                        "cli",
                        "--json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["total_entries"], 1)
            self.assertEqual(payload["entries"][0]["repository"], "example/cli-project")

    def test_mcp_refresh_updates_only_the_private_cache(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            config = load_config(config_path)
            store = ErgaStore(config.data_dir / "erga.sqlite3")

            def discover(*, cache_path: Path) -> tuple[GitHubProject, ...]:
                projects = (
                    GitHubProject(
                        repository="example/refreshed",
                        name="refreshed",
                        description="Refreshed catalogue entry",
                        language="Rust",
                        topics=("cli",),
                    ),
                )
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps(
                        [
                            {
                                "repository": project.repository,
                                "name": project.name,
                                "description": project.description,
                                "language": project.language,
                                "topics": list(project.topics),
                            }
                            for project in projects
                        ]
                    ),
                    encoding="utf-8",
                )
                return projects

            with patch(
                "erga_mcp.mcp.workspace_tools.discover_github_projects", side_effect=discover
            ):
                result = asyncio.run(
                    build_server(config_path).call_tool(
                        "refresh_project_catalogue",
                        {"page": 1, "page_size": 6, "query": ""},
                    )
                )
            payload = result.structured_content

            self.assertEqual(payload["github_projects_refreshed"], 1)
            self.assertFalse(payload["evidence_created"])
            self.assertFalse(payload["resume_changed"])
            self.assertEqual(payload["entries"][0]["repository"], "example/refreshed")
            self.assertEqual(store.list_evidence(), [])


if __name__ == "__main__":
    unittest.main()
