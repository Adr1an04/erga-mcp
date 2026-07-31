from __future__ import annotations

import json
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.host_connections import (
    SUPPORTED_HOSTS,
    collect_optional_hosts,
    configure_hosts,
    ensure_host_configuration,
    render_host_configuration,
    resolve_server_command,
)


class HostConnectionTests(unittest.TestCase):
    def _files(self, root: Path) -> tuple[Path, Path]:
        config = root / "private" / "config.toml"
        config.parent.mkdir()
        config.write_text("[paths]\ndata_dir = 'state'\n", encoding="utf-8")
        server = root / "bin" / "erga-mcp"
        server.parent.mkdir()
        server.write_text("", encoding="utf-8")
        return config, server

    def _render(self, host: str, root: Path):
        config, server = self._files(root)
        return render_host_configuration(
            host,  # type: ignore[arg-type]
            project_dir=root,
            config_path=config,
            server_command=server,
        )

    def test_current_host_formats_are_project_scoped_and_client_neutral(self) -> None:
        expected_targets = {
            "codex": ".codex/config.toml",
            "claude-code": ".mcp.json",
            "opencode": "opencode.json",
            "opencode-v2": "opencode.json",
            "gemini-cli": ".gemini/settings.json",
            "cursor": ".cursor/mcp.json",
            "github-copilot": ".mcp.json",
            "generic-mcp": ".mcp.json",
        }
        for host in SUPPORTED_HOSTS:
            with self.subTest(host=host), TemporaryDirectory() as directory:
                root = Path(directory)
                configuration = self._render(host, root)

                self.assertEqual(configuration.target_path, root / expected_targets[host])
                self.assertIn("ERGA_MCP_CONFIG", configuration.content)
                self.assertIn("career", configuration.content)
                self.assertNotIn("API_KEY", configuration.content)

    def test_codex_uses_official_project_config_shape_and_write_approval(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = self._render("codex", Path(directory))
            parsed = tomllib.loads(configuration.content)
            server = parsed["mcp_servers"]["erga-mcp"]

            self.assertEqual(server["default_tools_approval_mode"], "writes")
            self.assertEqual(server["env"]["ERGA_MCP_TOOL_PROFILE"], "career")

    def test_opencode_classic_and_v2_use_distinct_current_schemas(self) -> None:
        for host in ("opencode", "opencode-v2"):
            with self.subTest(host=host), TemporaryDirectory() as directory:
                configuration = self._render(host, Path(directory))
                parsed = json.loads(configuration.content)
                servers = parsed["mcp"]["servers"] if host == "opencode-v2" else parsed["mcp"]
                server = servers["erga-mcp"]

                self.assertEqual(server["type"], "local")
                self.assertIsInstance(server["command"], list)
                if host == "opencode":
                    self.assertTrue(server["enabled"])
                else:
                    self.assertNotIn("enabled", server)

    def test_multiple_hosts_can_be_connected_without_any_host_login_or_executable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, server = self._files(root)

            with patch("erga_mcp.host_connections.shutil.which", return_value=None):
                results = configure_hosts(
                    ("codex", "claude-code", "gemini-cli", "cursor"),
                    project_dir=root,
                    config_path=config,
                    server_command=server,
                )

            self.assertEqual(len(results), 4)
            self.assertTrue(all(result["written"] for result in results))
            self.assertTrue(all(result["installed_on_path"] is False for result in results))
            self.assertTrue((root / ".codex" / "config.toml").is_file())
            self.assertTrue((root / ".mcp.json").is_file())
            self.assertTrue((root / ".gemini" / "settings.json").is_file())
            self.assertTrue((root / ".cursor" / "mcp.json").is_file())

    def test_shared_standard_config_is_reused_across_compatible_hosts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, server = self._files(root)

            results = configure_hosts(
                ("claude-code", "github-copilot", "generic-mcp"),
                project_dir=root,
                config_path=config,
                server_command=server,
            )

            self.assertTrue(results[0]["written"])
            self.assertTrue(results[1]["already_configured"])
            self.assertTrue(results[2]["already_configured"])
            parsed = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(list(parsed["mcpServers"]), ["erga-mcp"])

    def test_write_preserves_unrelated_host_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = self._render("gemini-cli", root)
            configuration.target_path.parent.mkdir()
            configuration.target_path.write_text(
                json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "other"}}}),
                encoding="utf-8",
            )

            ensure_host_configuration(configuration)
            parsed = json.loads(configuration.target_path.read_text(encoding="utf-8"))

            self.assertEqual(parsed["theme"], "dark")
            self.assertIn("other", parsed["mcpServers"])
            self.assertIn("erga-mcp", parsed["mcpServers"])

    def test_opencode_version_conflict_is_rejected_before_writing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, server = self._files(root)

            with self.assertRaisesRegex(ValueError, "either OpenCode or OpenCode V2"):
                configure_hosts(
                    ("opencode", "opencode-v2"),
                    project_dir=root,
                    config_path=config,
                    server_command=server,
                )

            self.assertFalse((root / "opencode.json").exists())

    def test_refuses_symlinked_or_competing_configuration_targets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            configuration = self._render("gemini-cli", root)
            configuration.target_path.parent.mkdir()
            configuration.target_path.symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "inside|symlinked"):
                ensure_host_configuration(configuration)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = self._render("opencode", root)
            (root / "opencode.jsonc").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "second-precedence"):
                ensure_host_configuration(configuration)

    def test_preview_and_skip_modes_never_write(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config, server = self._files(root)

            preview = configure_hosts(
                ("codex",),
                project_dir=root,
                config_path=config,
                server_command=server,
                write=False,
            )
            skipped = configure_hosts(
                (),
                project_dir=root,
                config_path=config,
                server_command=server,
            )

            self.assertFalse(preview[0]["written"])
            self.assertFalse((root / ".codex" / "config.toml").exists())
            self.assertEqual(skipped, [])

    def test_optional_picker_defaults_to_no_connection(self) -> None:
        prompt = unittest.mock.Mock()
        prompt.ask.return_value = False
        with patch("erga_mcp.host_connections.questionary.confirm", return_value=prompt):
            selected = collect_optional_hosts()

        self.assertEqual(selected, ())

    def test_resolves_server_beside_installed_erga_launcher(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "erga"
            server = root / "erga-mcp"
            launcher.write_text("", encoding="utf-8")
            server.write_text("", encoding="utf-8")

            with (
                patch("erga_mcp.host_connections.shutil.which", return_value=None),
                patch.object(sys, "argv", [str(launcher)]),
            ):
                resolved = resolve_server_command()

            self.assertEqual(resolved, server.resolve())


if __name__ == "__main__":
    unittest.main()
