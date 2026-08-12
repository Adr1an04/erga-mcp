from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.mcp.server import build_server
from erga_mcp.store import ErgaStore


class OnboardingMcpTests(unittest.TestCase):
    def _call(self, config_path: Path, tool: str, arguments: dict[str, object]) -> dict[str, Any]:
        result: Any = asyncio.run(build_server(config_path).call_tool(tool, arguments))
        return cast(dict[str, Any], result.structured_content)

    def test_onboarding_skill_root_and_settings_tools_share_safe_cards(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            projects.mkdir()
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            skills = self._call(
                config_path,
                "update_skill_inventory",
                {"operation": "set", "skill_csv": "Python, FastAPI"},
            )
            roots = self._call(
                config_path,
                "manage_portfolio_roots",
                {"operation": "add", "root": str(projects)},
            )
            onboarding = self._call(config_path, "onboarding_status", {})
            settings = self._call(config_path, "erga_settings_card", {})

            self.assertEqual(len(skills["skills"]), 2)
            self.assertEqual(roots["roots"], [str(projects.resolve())])
            self.assertEqual(onboarding["title"], "Erga onboarding")
            self.assertEqual(onboarding["fields"][3]["value"], str(projects.resolve()))
            rendered_settings = json.dumps(settings)
            self.assertNotIn(str(load_config(config_path).data_dir), rendered_settings)
            self.assertNotIn("client_id", rendered_settings)

    def test_git_skill_review_card_is_paginated_and_does_not_approve_on_render(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            detected_projects = root / "detected-projects"
            detected_projects.mkdir()
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            store.set_skill_seeds(["React", "PostgreSQL", "FastAPI"])
            store.add_git_candidate(
                repo_path="/synthetic/web",
                commit_sha="a" * 40,
                commit_range="a" * 40,
                text="Git commit: Add React view\nChanged files: package.json",
            )

            with patch(
                "erga_mcp.mcp.read_tools.detected_portfolio_root",
                return_value=detected_projects,
            ):
                card = self._call(
                    config_path,
                    "git_skill_review_card",
                    {"page": 2, "page_size": 2},
                )

            self.assertEqual(card["page"], 2)
            self.assertEqual(card["page_count"], 2)
            self.assertEqual(card["actions"][0]["action_id"], "onboarding.roots.use_detected")
            self.assertEqual(card["actions"][0]["label"], "Use detected folder and scan")
            self.assertEqual(store.list_evidence(), [])

    def test_mobile_quick_setup_imports_only_approved_explicit_skills_and_detected_root(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            projects = root / "projects"
            projects.mkdir()
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            store.add_evidence(
                source_ref="fixture:approved",
                text="Implemented Python and FastAPI services with Docker.",
                approved=True,
            )
            store.add_evidence(
                source_ref="fixture:unapproved",
                text="Experimented with Rust.",
                approved=False,
            )

            imported = self._call(
                config_path,
                "update_skill_inventory",
                {"operation": "import_approved"},
            )
            with patch(
                "erga_mcp.mcp.workspace_tools.detected_portfolio_root",
                return_value=projects,
            ):
                detected = self._call(
                    config_path,
                    "manage_portfolio_roots",
                    {"operation": "add_detected"},
                )

            skills = {item["normalized_skill"]: item for item in imported["skills"]}
            self.assertEqual(set(skills), {"python", "fastapi", "docker"})
            self.assertTrue(all(item["source"] == "approved_evidence" for item in skills.values()))
            self.assertNotIn("rust", skills)
            self.assertEqual(detected["roots"], [str(projects.resolve())])
            self.assertEqual(store.list_evidence()[0].approved, True)

    def test_explicit_group_review_approves_only_corroborated_candidates(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            store.set_skill_seeds(["PostgreSQL", "FastAPI"])
            store.add_git_candidate(
                repo_path="/synthetic/api",
                commit_sha="b" * 40,
                commit_range="b" * 40,
                text="Git commit: Add FastAPI route\nChanged files: src/api.py",
            )

            with self.assertRaisesRegex(Exception, "corroborated"):
                self._call(
                    config_path,
                    "review_git_skill_group",
                    {"operation": "approve", "skill": "postgresql"},
                )
            approved = self._call(
                config_path,
                "review_git_skill_group",
                {"operation": "approve", "skill": "fastapi"},
            )

            self.assertEqual(approved["approved_evidence_count"], 1)
            self.assertEqual(len(store.list_evidence()), 1)


if __name__ == "__main__":
    unittest.main()
