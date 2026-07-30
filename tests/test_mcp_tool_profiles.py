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
    "resume_source_context",
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

    def test_career_profile_exposes_complete_job_workflow_without_mail_or_hermes(self) -> None:
        tool_names = self._tool_names(self._config_with_profile("career"))

        self.assertIn("erga_capabilities", tool_names)
        self.assertIn("intake_job_url", tool_names)
        self.assertIn("create_tailored_resume", tool_names)
        self.assertIn("validate_tailored_resume", tool_names)
        self.assertIn("create_cover_letter", tool_names)
        self.assertNotIn("sync_recruiting_mail", tool_names)
        self.assertNotIn("install_mail_monitor_scripts", tool_names)
        self.assertNotIn("record_token_usage", tool_names)
        self.assertNotIn("research_git_worktrees", tool_names)

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
            self.assertTrue(annotations.readOnlyHint)
            self.assertTrue(annotations.idempotentHint)
            self.assertTrue(annotations.openWorldHint)
        for name in {"intake_job_url", "validate_tailored_resume"}:
            annotations = tools[name].annotations
            self.assertIsNotNone(annotations)
            assert annotations is not None
            self.assertFalse(annotations.idempotentHint)


if __name__ == "__main__":
    unittest.main()
