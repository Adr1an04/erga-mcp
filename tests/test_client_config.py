from __future__ import annotations

import json
import sys
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.client_adapters import SUPPORTED_CLIENTS
from erga_mcp.client_config import (
    ensure_client_configuration,
    render_client_configuration,
    resolve_server_command,
    write_client_configuration,
)


class ClientConfigurationTests(unittest.TestCase):
    def test_resolves_server_next_to_an_installed_erga_launcher(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "erga"
            server = root / "erga-mcp"
            launcher.write_text("", encoding="utf-8")
            server.write_text("", encoding="utf-8")

            with (
                patch("erga_mcp.client_config.shutil.which", return_value=None),
                patch.object(sys, "argv", [str(launcher)]),
            ):
                resolved = resolve_server_command()

            self.assertEqual(resolved, server.resolve())

    def _render(self, client: str, root: Path):
        config = root / "erga.toml"
        config.write_text("[paths]\ndata_dir = 'state'\n", encoding="utf-8")
        executable = root / "erga-mcp"
        executable.write_text("", encoding="utf-8")
        return render_client_configuration(
            client,  # type: ignore[arg-type]
            project_dir=root,
            config_path=config,
            server_command=executable,
        )

    def test_codex_configuration_uses_career_profile_and_no_api_key(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = self._render("codex", Path(directory))
            parsed = tomllib.loads(configuration.content)
            server = parsed["mcp_servers"]["erga-mcp"]

            self.assertEqual(server["env"]["ERGA_MCP_TOOL_PROFILE"], "career")
            self.assertEqual(server["default_tools_approval_mode"], "writes")
            self.assertNotIn("API_KEY", configuration.content)

    def test_claude_code_configuration_is_project_scoped_stdio(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = self._render("claude-code", Path(directory))
            parsed = json.loads(configuration.content)
            server = parsed["mcpServers"]["erga-mcp"]

            self.assertEqual(configuration.target_path.name, ".mcp.json")
            self.assertEqual(server["type"], "stdio")
            self.assertEqual(server["env"]["ERGA_MCP_TOOL_PROFILE"], "career")

    def test_opencode_configuration_uses_v2_local_server_shape(self) -> None:
        with TemporaryDirectory() as directory:
            configuration = self._render("opencode", Path(directory))
            parsed = json.loads(configuration.content)
            server = parsed["mcp"]["servers"]["erga-mcp"]

            self.assertEqual(parsed["$schema"], "https://opencode.ai/config.json")
            self.assertEqual(server["type"], "local")
            self.assertIsInstance(server["command"], list)
            self.assertEqual(server["environment"]["ERGA_MCP_TOOL_PROFILE"], "career")

    def test_additional_clients_use_their_native_project_paths(self) -> None:
        expected = {
            "gemini-cli": ".gemini/settings.json",
            "cursor-agent": ".cursor/mcp.json",
            "github-copilot": ".mcp.json",
            "generic-mcp": ".mcp.json",
        }
        for client, target in expected.items():
            with self.subTest(client=client), TemporaryDirectory() as directory:
                root = Path(directory)
                configuration = self._render(client, root)
                parsed = json.loads(configuration.content)
                server = parsed["mcpServers"]["erga-mcp"]

                self.assertEqual(configuration.target_path, root / target)
                self.assertEqual(server["env"]["ERGA_MCP_TOOL_PROFILE"], "career")
                self.assertNotIn("API_KEY", configuration.content)

    def test_registry_and_configuration_support_stay_aligned(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for client in SUPPORTED_CLIENTS:
                with self.subTest(client=client):
                    configuration = self._render(client, root)
                    self.assertEqual(configuration.client, client)

    def test_write_preserves_unrelated_json_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "opencode.json"
            target.write_text(
                json.dumps({"model": "local/test", "mcp": {"servers": {"other": {}}}}),
                encoding="utf-8",
            )
            configuration = self._render("opencode", root)

            result = write_client_configuration(configuration)
            parsed = json.loads(target.read_text(encoding="utf-8"))

            self.assertTrue(result["written"])
            self.assertEqual(parsed["model"], "local/test")
            self.assertIn("other", parsed["mcp"]["servers"])
            self.assertIn("erga-mcp", parsed["mcp"]["servers"])

    def test_write_refuses_to_overwrite_existing_server(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = self._render("claude-code", root)
            write_client_configuration(configuration)

            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                write_client_configuration(configuration)

    def test_ensure_reuses_an_identical_existing_server(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = self._render("codex", root)
            write_client_configuration(configuration)

            result = ensure_client_configuration(configuration)

            self.assertFalse(result["written"])
            self.assertTrue(result["already_configured"])

    def test_opencode_write_refuses_to_create_competing_json_when_jsonc_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "opencode.jsonc").write_text("{\n  // existing configuration\n}\n")
            configuration = self._render("opencode", root)

            with self.assertRaisesRegex(ValueError, "second-precedence"):
                write_client_configuration(configuration)


if __name__ == "__main__":
    unittest.main()
