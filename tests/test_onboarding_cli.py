from __future__ import annotations

import json
import subprocess
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.cli import main
from erga_mcp.config import load_config
from erga_mcp.store import ErgaStore


class OnboardingCliTests(unittest.TestCase):
    def _run_json(self, arguments: list[str]) -> dict[str, object] | list[object]:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(arguments), 0)
        payload = json.loads(output.getvalue())
        assert isinstance(payload, (dict, list))
        return payload

    def _git(self, repository: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_skill_inventory_commands_manage_review_state_without_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])

            configured = self._run_json(
                [
                    "onboarding",
                    "skills",
                    "set",
                    "--csv",
                    "Python, FastAPI, Python",
                    "--config",
                    str(config_path),
                ]
            )
            self.assertEqual(
                [item["normalized_skill"] for item in configured], ["python", "fastapi"]
            )

            checked = self._run_json(
                [
                    "onboarding",
                    "skills",
                    "check",
                    "fastapi",
                    "--config",
                    str(config_path),
                ]
            )
            self.assertTrue(checked["checked"])
            self._run_json(
                [
                    "onboarding",
                    "skills",
                    "uncheck",
                    "fastapi",
                    "--config",
                    str(config_path),
                ]
            )
            remaining = self._run_json(
                [
                    "onboarding",
                    "skills",
                    "remove",
                    "python",
                    "--config",
                    str(config_path),
                ]
            )

            self.assertEqual([item["normalized_skill"] for item in remaining], ["fastapi"])
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            self.assertEqual(store.list_evidence(), [])

    def test_roots_and_status_are_available_as_structured_cards(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            repository_root = root / "projects"
            repository_root.mkdir()
            main(["init", "--config", str(config_path)])

            roots = self._run_json(
                [
                    "onboarding",
                    "roots",
                    "add",
                    str(repository_root),
                    "--config",
                    str(config_path),
                ]
            )
            status = self._run_json(
                ["onboarding", "status", "--json", "--config", str(config_path)]
            )

            self.assertEqual(roots, [str(repository_root.resolve())])
            self.assertEqual(status["title"], "Erga onboarding")
            self.assertEqual(status["fields"][3]["value"], str(repository_root.resolve()))

            removed = self._run_json(
                [
                    "onboarding",
                    "roots",
                    "remove",
                    str(repository_root),
                    "--config",
                    str(config_path),
                ]
            )
            self.assertEqual(removed, [])

    def test_settings_json_is_a_redacted_shared_card(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])

            settings = self._run_json(["settings", "--json", "--config", str(config_path)])

            self.assertEqual(settings["title"], "Erga settings")
            self.assertNotIn(str(load_config(config_path).data_dir), json.dumps(settings))

    def test_configured_root_scan_uses_saved_roots_and_seed_override_is_not_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            projects = root / "projects"
            repository = projects / "sample"
            repository.mkdir(parents=True)
            self._git(repository, "init")
            self._git(repository, "config", "user.email", "synthetic@example.test")
            self._git(repository, "config", "user.name", "Synthetic User")
            (repository / "service.py").write_text("def serve(): return True\n", encoding="utf-8")
            self._git(repository, "add", "service.py")
            self._git(repository, "commit", "-m", "Implement FastAPI service")
            main(["init", "--config", str(config_path)])
            self._run_json(
                [
                    "onboarding",
                    "roots",
                    "add",
                    str(projects),
                    "--config",
                    str(config_path),
                ]
            )

            result = self._run_json(
                [
                    "git",
                    "scan",
                    "--configured-roots",
                    "--seed-csv",
                    "FastAPI, React",
                    "--config",
                    str(config_path),
                ]
            )

            self.assertEqual(result["repositories_scanned"], 1)
            self.assertEqual(result["review_seed_override"], ["FastAPI", "React"])
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            self.assertEqual(store.list_skill_seeds(), [])

    def test_invalid_csv_and_missing_root_fail_before_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            main(["init", "--config", str(config_path)])

            with self.assertRaisesRegex(ValueError, "at least one"):
                main(
                    [
                        "onboarding",
                        "skills",
                        "set",
                        "--csv",
                        "   ",
                        "--config",
                        str(config_path),
                    ]
                )
            with self.assertRaisesRegex(ValueError, "existing directory"):
                main(
                    [
                        "onboarding",
                        "roots",
                        "add",
                        str(root / "missing"),
                        "--config",
                        str(config_path),
                    ]
                )

    def test_git_skill_review_can_skip_and_restore_without_approving(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            main(["init", "--config", str(config_path)])
            self._run_json(
                [
                    "onboarding",
                    "skills",
                    "set",
                    "--csv",
                    "React",
                    "--config",
                    str(config_path),
                ]
            )

            skipped = self._run_json(
                ["git", "skills", "skip", "react", "--config", str(config_path)]
            )
            hidden = self._run_json(
                ["git", "skills", "show", "--json", "--config", str(config_path)]
            )
            restored = self._run_json(
                ["git", "skills", "restore", "react", "--config", str(config_path)]
            )

            self.assertTrue(skipped["skipped"])
            self.assertIn("No skill groups", json.dumps(hidden))
            self.assertFalse(restored["skipped"])
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            self.assertEqual(store.list_evidence(), [])


if __name__ == "__main__":
    unittest.main()
