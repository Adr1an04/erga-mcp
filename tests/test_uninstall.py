from __future__ import annotations

import json
import sys
import tomllib
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.cli import main
from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.host_connections import configure_hosts
from erga_mcp.uninstall import apply_uninstall, build_uninstall_plan


class UninstallTests(unittest.TestCase):
    def _configured_install(self, root: Path) -> dict[str, Path]:
        config_dir = root / "private-config"
        config_dir.mkdir()
        state = config_dir / "state"
        state.mkdir()
        (state / "erga.sqlite3").write_text("private", encoding="utf-8")
        (state / "resume-sources").mkdir()
        (state / "resume-sources" / "managed.pdf").write_text("copy", encoding="utf-8")

        originals = root / "originals"
        originals.mkdir()
        master = originals / "master.pdf"
        master.write_text("original", encoding="utf-8")
        style = originals / "style.pdf"
        style.write_text("original style", encoding="utf-8")

        output = root / "erga-output"
        output.mkdir()
        (output / "generated.pdf").write_text("generated", encoding="utf-8")
        vault = root / "vault"
        (vault / "Erga" / "Applications").mkdir(parents=True)
        (vault / "Erga" / "Start Here.md").write_text("Erga", encoding="utf-8")
        (vault / "Personal.md").write_text("keep", encoding="utf-8")

        config_path = config_dir / "config.toml"
        rendered = DEFAULT_CONFIG
        rendered = rendered.replace('data_dir = "state"', f"data_dir = {json.dumps(str(state))}")
        rendered = rendered.replace('vault_path = ""', f"vault_path = {json.dumps(str(vault))}")
        rendered = rendered.replace('master_path = ""', f"master_path = {json.dumps(str(master))}")
        rendered = rendered.replace(
            'reference_path = ""', f"reference_path = {json.dumps(str(style))}"
        )
        rendered = rendered.replace(
            'output_root = "output"', f"output_root = {json.dumps(str(output))}"
        )
        config_path.write_text(rendered, encoding="utf-8")
        return {
            "config": config_path,
            "state": state,
            "master": master,
            "style": style,
            "output": output,
            "vault": vault,
        }

    def test_dry_run_is_machine_readable_and_does_not_delete(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._configured_install(root)
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["uninstall", "--config", str(paths["config"]), "--dry-run"])

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["config_path"], str(paths["config"].absolute()))
            self.assertTrue(paths["config"].is_file())
            self.assertTrue(paths["state"].is_dir())
            self.assertIn(str(paths["master"]), payload["preserved_sources"])

    def test_confirmation_must_match_exactly_before_any_mutation(self) -> None:
        with TemporaryDirectory() as directory:
            paths = self._configured_install(Path(directory))
            output = StringIO()

            with patch("builtins.input", return_value="yes"), redirect_stdout(output):
                exit_code = main(["uninstall", "--config", str(paths["config"])])

            self.assertEqual(exit_code, 130)
            self.assertIn("nothing was deleted", output.getvalue())
            self.assertTrue(paths["config"].is_file())
            self.assertTrue(paths["state"].is_dir())

    def test_apply_removes_erga_state_and_only_erga_from_shared_integrations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._configured_install(root)
            home = root / "home"
            legacy = home / ".erga"
            legacy.mkdir(parents=True)
            (legacy / "legacy.json").write_text("{}", encoding="utf-8")
            hermes_home = root / "hermes"
            scripts = hermes_home / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "erga-mcp-monitor.json").write_text("{}", encoding="utf-8")

            workspace = root / "workspace"
            workspace.mkdir()
            shared_config = workspace / ".mcp.json"
            shared_config.write_text(
                json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "other"}}}),
                encoding="utf-8",
            )
            server = root / "bin" / "erga-mcp"
            server.parent.mkdir()
            server.write_text("", encoding="utf-8")
            configure_hosts(
                ("generic-mcp", "codex"),
                project_dir=workspace,
                config_path=paths["config"],
                server_command=server,
            )
            codex_config = workspace / ".codex" / "config.toml"
            codex_config.write_text(
                "[features]\nkeep_me = true\n\n" + codex_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            checkout_environment = root / ".venv"
            checkout_environment.mkdir()
            (checkout_environment / "keep.txt").write_text("host owned", encoding="utf-8")

            plan = build_uninstall_plan(
                paths["config"],
                home=home,
                cwd=workspace,
                hermes_home=hermes_home,
            )
            with (
                patch("erga_mcp.uninstall.delete_discord_token", return_value=True),
                patch("erga_mcp.uninstall.stop_discord_bridge", return_value={"running": False}),
                patch("erga_mcp.uninstall._verified_legacy_bridge_pid", return_value=None),
            ):
                result = apply_uninstall(plan)

            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse(paths["config"].exists())
            self.assertFalse(paths["state"].exists())
            self.assertFalse(paths["output"].exists())
            self.assertFalse((paths["vault"] / "Erga").exists())
            self.assertFalse(legacy.exists())
            self.assertFalse((scripts / "erga-mcp-monitor.json").exists())
            self.assertTrue(paths["master"].is_file())
            self.assertTrue(paths["style"].is_file())
            self.assertTrue((paths["vault"] / "Personal.md").is_file())
            self.assertTrue(paths["config"].parent.is_dir())
            self.assertTrue((checkout_environment / "keep.txt").is_file())
            remaining_host_config = json.loads(shared_config.read_text(encoding="utf-8"))
            self.assertEqual(remaining_host_config["theme"], "dark")
            self.assertEqual(remaining_host_config["mcpServers"], {"other": {"command": "other"}})
            remaining_codex_config = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            self.assertTrue(remaining_codex_config["features"]["keep_me"])
            self.assertNotIn("erga-mcp", remaining_codex_config.get("mcp_servers", {}))

    def test_unsafe_broad_output_and_original_sources_are_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._configured_install(root)
            broad_output = root / "home" / "Documents"
            broad_output.mkdir(parents=True)
            personal = broad_output / "personal.txt"
            personal.write_text("keep", encoding="utf-8")
            raw = (
                paths["config"]
                .read_text(encoding="utf-8")
                .replace(json.dumps(str(paths["output"])), json.dumps(str(broad_output)))
            )
            paths["config"].write_text(raw, encoding="utf-8")

            plan = build_uninstall_plan(
                paths["config"],
                home=root / "home",
                cwd=root,
                hermes_home=root / "hermes",
            )

            self.assertNotIn(str(broad_output), {target.path for target in plan.targets})
            self.assertTrue(any("unsafe configured directory" in item for item in plan.warnings))
            self.assertIn(str(paths["master"]), plan.preserved_sources)
            self.assertTrue(personal.is_file())

    @unittest.skipUnless(sys.platform == "darwin", "macOS Library paths are platform-specific")
    def test_canonical_uninstall_includes_exact_macos_legacy_locations(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            config_path = home / ".config" / "erga-mcp" / "config.toml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            state = config_path.parent / "state"
            state.mkdir()
            (state / "erga.sqlite3").write_text("private", encoding="utf-8")
            application_support = home / "Library" / "Application Support" / "Erga"
            cache = home / "Library" / "Caches" / "erga-mcp"
            hidden_library_state = home / "Library" / ".erga"
            application_support.mkdir(parents=True)
            cache.mkdir(parents=True)
            hidden_library_state.mkdir(parents=True)
            (application_support / ".erga-state").write_text("private", encoding="utf-8")
            (cache / "cache.db").write_text("private", encoding="utf-8")
            (hidden_library_state / "state.db").write_text("private", encoding="utf-8")
            unrelated = home / "Library" / "Caches" / "other-app" / "keep.db"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("keep", encoding="utf-8")

            plan = build_uninstall_plan(
                config_path,
                home=home,
                cwd=root,
                hermes_home=root / "hermes",
            )
            planned = {target.path for target in plan.targets}
            self.assertIn(str(config_path.parent), planned)
            self.assertIn(str(application_support), planned)
            self.assertIn(str(cache), planned)
            self.assertIn(str(hidden_library_state), planned)

            with (
                patch("erga_mcp.uninstall.delete_discord_token", return_value=False),
                patch("erga_mcp.uninstall.stop_discord_bridge", return_value={"running": False}),
                patch("erga_mcp.uninstall._verified_legacy_bridge_pid", return_value=None),
            ):
                apply_uninstall(plan)

            self.assertFalse(config_path.parent.exists())
            self.assertFalse(application_support.exists())
            self.assertFalse(cache.exists())
            self.assertFalse(hidden_library_state.exists())
            self.assertTrue(unrelated.is_file())


if __name__ == "__main__":
    unittest.main()
