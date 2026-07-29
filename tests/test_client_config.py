from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from erga_mcp.client_config import render_client_configuration, write_client_configuration


class ClientConfigurationTests(unittest.TestCase):
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

    def test_opencode_write_refuses_to_create_competing_json_when_jsonc_exists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "opencode.jsonc").write_text("{\n  // existing configuration\n}\n")
            configuration = self._render("opencode", root)

            with self.assertRaisesRegex(ValueError, "second-precedence"):
                write_client_configuration(configuration)


if __name__ == "__main__":
    unittest.main()
