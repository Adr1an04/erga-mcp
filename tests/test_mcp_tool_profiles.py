from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.config import DEFAULT_CONFIG
from erga_mcp.mcp_server import build_server

_READ_TOOLS = {
    "erga_capabilities",
    "pipeline_status",
    "list_applications",
    "application_tracker",
    "list_evidence",
    "list_mail_events",
    "token_usage",
}


class McpToolProfileTests(unittest.TestCase):
    def _config_with_profile(self, profile: str) -> str:
        return DEFAULT_CONFIG.replace('tool_profile = "default"', f'tool_profile = "{profile}"')

    def _tool_names(self, config: str, environment: dict[str, str] | None = None) -> set[str]:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(config, encoding="utf-8")
            with patch.dict(os.environ, environment or {}, clear=False):
                return {tool.name for tool in asyncio.run(build_server(config_path).list_tools())}

    def test_read_profile_exposes_only_local_read_tools(self) -> None:
        tool_names = self._tool_names(self._config_with_profile("read"))

        self.assertEqual(tool_names, _READ_TOOLS)

    def test_research_profile_adds_only_network_read_tools(self) -> None:
        tool_names = self._tool_names(self._config_with_profile("research"))

        self.assertEqual(tool_names, _READ_TOOLS | {"scrape_public_page", "extract_public_page"})

    def test_write_profile_excludes_network_and_hermes_tools(self) -> None:
        tool_names = self._tool_names(self._config_with_profile("write"))

        self.assertEqual(
            tool_names,
            _READ_TOOLS
            | {
                "record_token_usage",
                "export_data",
                "record_secondary_research",
                "create_research_brief",
                "record_deep_research",
                "create_tailored_resume",
                "create_cover_letter",
                "validate_tailored_resume",
                "cover_letter_style_context",
                "research_git_worktrees",
                "review_git_drafts",
            },
        )

    def test_hermes_profile_exposes_only_hermes_integration_tools_beyond_reads(self) -> None:
        tool_names = self._tool_names(self._config_with_profile("hermes"))

        self.assertEqual(
            tool_names, _READ_TOOLS | {"sync_recruiting_mail", "install_mail_monitor_scripts"}
        )

    def test_environment_profile_overrides_nonsecret_config_selection(self) -> None:
        tool_names = self._tool_names(
            self._config_with_profile("read"),
            {"ERGA_MCP_TOOL_PROFILE": "research"},
        )

        self.assertEqual(tool_names, _READ_TOOLS | {"scrape_public_page", "extract_public_page"})

    def test_default_profile_preserves_the_complete_legacy_surface(self) -> None:
        tool_names = self._tool_names(DEFAULT_CONFIG)

        self.assertIn("intake_job_url", tool_names)
        self.assertIn("prepare_job_workspace", tool_names)
        self.assertIn("sync_recruiting_mail", tool_names)
        self.assertIn("install_mail_monitor_scripts", tool_names)

    def test_network_read_and_mutating_tool_annotations_are_accurate(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            tools = {
                tool.name: tool for tool in asyncio.run(build_server(config_path).list_tools())
            }

        for name in {"scrape_public_page", "extract_public_page"}:
            annotations = tools[name].annotations
            self.assertIsNotNone(annotations)
            assert annotations is not None
            self.assertTrue(annotations.read_only_hint)
            self.assertTrue(annotations.idempotent_hint)
            self.assertTrue(annotations.open_world_hint)
        for name in {"intake_job_url", "validate_tailored_resume"}:
            annotations = tools[name].annotations
            self.assertIsNotNone(annotations)
            assert annotations is not None
            self.assertFalse(annotations.idempotent_hint)


if __name__ == "__main__":
    unittest.main()
