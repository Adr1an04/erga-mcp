from __future__ import annotations

import json
import os
import stat
import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.client_adapters import SUPPORTED_CLIENTS, client_adapter
from erga_mcp.onboarding import onboard, render_onboarding_report


class OnboardingTests(unittest.TestCase):
    def _executable(self, root: Path) -> Path:
        executable = root / "erga-mcp"
        executable.write_text("", encoding="utf-8")
        return executable

    def test_onboards_every_supported_client_without_a_model_api_key(self) -> None:
        for client in SUPPORTED_CLIENTS:
            with self.subTest(client=client), TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / "private" / "config.toml"

                with patch("erga_mcp.onboarding.shutil.which", return_value="/usr/bin/client"):
                    report = onboard(
                        client,  # type: ignore[arg-type]
                        config_path=config,
                        project_dir=root,
                        server_command=self._executable(root),
                    )

                self.assertEqual(report.status, "ready")
                self.assertTrue(report.core_ready)
                self.assertTrue(report.config_created)
                self.assertTrue(report.mcp_config_written)
                self.assertFalse(report.model_api_required)
                self.assertEqual(report.tool_profile, "career")
                self.assertTrue(config.is_file())
                self.assertTrue(Path(report.mcp_config_path).is_file())
                if os.name != "nt":
                    data_dir = Path(report.data_dir)
                    self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
                    self.assertEqual(stat.S_IMODE(data_dir.stat().st_mode), 0o700)
                    self.assertEqual(
                        stat.S_IMODE((data_dir / "erga.sqlite3").stat().st_mode),
                        0o600,
                    )

    def test_onboarding_is_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "private" / "config.toml"
            executable = self._executable(root)

            first = onboard(
                "codex",
                config_path=config,
                project_dir=root,
                server_command=executable,
            )
            second = onboard(
                "codex",
                config_path=config,
                project_dir=root,
                server_command=executable,
            )

            self.assertTrue(first.config_created)
            self.assertTrue(first.mcp_config_written)
            self.assertFalse(second.config_created)
            self.assertFalse(second.mcp_config_written)
            self.assertTrue(second.mcp_already_configured)

    def test_generated_client_files_are_parseable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self._executable(root)
            config = root / "private" / "config.toml"

            codex = onboard(
                "codex",
                config_path=config,
                project_dir=root,
                server_command=executable,
            )
            parsed = tomllib.loads(Path(codex.mcp_config_path).read_text(encoding="utf-8"))

            self.assertEqual(
                parsed["mcp_servers"]["erga-mcp"]["env"]["ERGA_MCP_TOOL_PROFILE"],
                "career",
            )

        for client in (
            "claude-code",
            "opencode",
            "gemini-cli",
            "cursor-agent",
            "github-copilot",
            "generic-mcp",
        ):
            with self.subTest(client=client), TemporaryDirectory() as directory:
                root = Path(directory)
                report = onboard(
                    client,  # type: ignore[arg-type]
                    config_path=root / "private" / "config.toml",
                    project_dir=root,
                    server_command=self._executable(root),
                )

                target = root / client_adapter(client).mcp_target
                parsed_json = json.loads(target.read_text(encoding="utf-8"))
                self.assertIsInstance(parsed_json, dict)
                self.assertEqual(report.mcp_config_path, str(target))

    def test_human_report_has_a_concrete_success_check(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            report = onboard(
                "codex",
                config_path=root / "private" / "config.toml",
                project_dir=root,
                server_command=self._executable(root),
            )

            rendered = render_onboarding_report(report)

            self.assertIn("Erga is ready for Codex / ChatGPT.", rendered)
            self.assertIn("Show my Erga pipeline status.", rendered)
            self.assertIn("Separate model API key required: no", rendered)


if __name__ == "__main__":
    unittest.main()
