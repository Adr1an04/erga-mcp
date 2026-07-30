from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.discord_bridge import (
    DiscordBridgeSettings,
    _agent_environment,
    build_agent_command,
    is_authorized_discord_user,
    load_discord_settings,
    resolve_client_command,
    run_agent,
    split_discord_message,
    verify_subscription_login,
    write_discord_settings,
)


class DiscordBridgeTests(unittest.TestCase):
    def _settings(self, root: Path, client: str = "codex") -> DiscordBridgeSettings:
        command = root / client
        command.write_text("", encoding="utf-8")
        return DiscordBridgeSettings(
            client=client,  # type: ignore[arg-type]
            client_command=str(command),
            project_dir=root,
            allowed_user_ids=(123456789,),
        )

    def test_settings_never_persist_a_bot_token(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            settings = self._settings(root)

            target = write_discord_settings(config, settings)
            loaded = load_discord_settings(config)
            content = target.read_text(encoding="utf-8")

            self.assertEqual(loaded, settings)
            self.assertNotIn("token", content.casefold())

    def test_resolves_codex_bundled_inside_a_desktop_app(self) -> None:
        with TemporaryDirectory() as directory:
            bundled = Path(directory) / "ChatGPT.app" / "Contents" / "Resources" / "codex"
            bundled.parent.mkdir(parents=True)
            bundled.write_text("#!/bin/sh\n", encoding="utf-8")
            bundled.chmod(0o755)

            with (
                patch("erga_mcp.discord_bridge.shutil.which", return_value=None),
                patch(
                    "erga_mcp.discord_bridge._bundled_client_candidates",
                    return_value=(bundled,),
                ),
            ):
                resolved = resolve_client_command("codex")

            self.assertEqual(resolved, bundled.absolute())

    def test_missing_client_error_mentions_path_and_desktop_apps(self) -> None:
        with (
            patch("erga_mcp.discord_bridge.shutil.which", return_value=None),
            patch(
                "erga_mcp.discord_bridge._bundled_client_candidates",
                return_value=(),
            ),
        ):
            with self.assertRaisesRegex(FileNotFoundError, "PATH or in a supported desktop app"):
                resolve_client_command("codex")

    def test_settings_accept_modern_discord_usernames(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            original = DiscordBridgeSettings(
                client="codex",
                client_command=str(root / "codex"),
                project_dir=root,
                allowed_user_ids=(),
                allowed_usernames=("student.dev",),
            )

            write_discord_settings(config, original)

            self.assertEqual(load_discord_settings(config), original)

    def test_authorizes_current_username_or_stable_id_but_never_a_bot(self) -> None:
        settings = DiscordBridgeSettings(
            client="codex",
            client_command="/tmp/codex",
            project_dir=Path("/tmp"),
            allowed_user_ids=(123,),
            allowed_usernames=("student.dev",),
        )

        self.assertTrue(
            is_authorized_discord_user(
                settings,
                user_id=999,
                username="Student.Dev",
                is_bot=False,
            )
        )
        self.assertTrue(
            is_authorized_discord_user(
                settings,
                user_id=123,
                username="renamed_user",
                is_bot=False,
            )
        )
        self.assertFalse(
            is_authorized_discord_user(
                settings,
                user_id=123,
                username="student.dev",
                is_bot=True,
            )
        )

    def test_builds_subscription_cli_commands_for_every_client(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "response.txt"

            codex = build_agent_command(self._settings(root, "codex"), "prompt", output)
            claude = build_agent_command(self._settings(root, "claude-code"), "prompt", output)
            opencode = build_agent_command(self._settings(root, "opencode"), "prompt", output)
            gemini = build_agent_command(self._settings(root, "gemini-cli"), "prompt", output)
            cursor = build_agent_command(self._settings(root, "cursor-agent"), "prompt", output)
            copilot = build_agent_command(
                self._settings(root, "github-copilot"),
                "prompt",
                output,
            )

            self.assertEqual(codex[1], "exec")
            self.assertIn("--output-last-message", codex)
            self.assertIn("--print", claude)
            self.assertIn("acceptEdits", claude)
            self.assertEqual(opencode[1], "run")
            self.assertIn("--auto", opencode)
            self.assertIn("--allowed-mcp-server-names", gemini)
            self.assertIn("--approve-mcps", cursor)
            self.assertIn("--allow-tool=erga-mcp", copilot)
            self.assertIn("--no-ask-user", copilot)

    def test_subscription_clients_do_not_inherit_model_api_keys(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret"},
        ):
            codex = _agent_environment("codex")
            claude = _agent_environment("claude-code")
            gemini = _agent_environment("gemini-cli")
            cursor = _agent_environment("cursor-agent")
            copilot = _agent_environment("github-copilot")

        self.assertNotIn("OPENAI_API_KEY", codex)
        self.assertNotIn("ANTHROPIC_API_KEY", claude)
        self.assertNotIn("GEMINI_API_KEY", gemini)
        self.assertNotIn("CURSOR_API_KEY", cursor)
        self.assertEqual(copilot["GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP"], "true")

    def test_generic_adapter_passes_a_safe_argument_array_without_a_shell(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "response.txt"
            settings = DiscordBridgeSettings(
                client="generic-mcp",
                client_command=str(root / "agent"),
                project_dir=root,
                allowed_user_ids=(123,),
                custom_arguments=(
                    "--headless",
                    "{prompt}",
                    "--workspace",
                    "{project_dir}",
                ),
            )

            command = build_agent_command(settings, "hello; rm -rf /", output)

            self.assertEqual(
                command,
                [
                    str(root / "agent"),
                    "--headless",
                    "hello; rm -rf /",
                    "--workspace",
                    str(root),
                ],
            )

    def test_codex_uses_the_explicit_final_message_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)

            def fake_run(
                command: list[str],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text("Final answer", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "events", "")

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=fake_run):
                rendered = run_agent(settings, "hello")

            self.assertEqual(rendered, "Final answer")

    def test_login_verification_includes_a_live_readiness_turn(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._settings(root).client_command
            calls: list[list[str]] = []

            def fake_run(
                invoked: list[str],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(invoked)
                if invoked[1:3] == ["login", "status"]:
                    return subprocess.CompletedProcess(invoked, 0, "Logged in using ChatGPT", "")
                output = Path(invoked[invoked.index("--output-last-message") + 1])
                output.write_text("ERGA_READY", encoding="utf-8")
                return subprocess.CompletedProcess(invoked, 0, "", "")

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=fake_run):
                ready, detail = verify_subscription_login("codex", Path(command))

            self.assertTrue(ready)
            self.assertEqual(detail, "coding-agent subscription is ready")
            self.assertEqual(len(calls), 2)
            self.assertIn("read-only", calls[1])

    def test_login_verification_rejects_a_stale_recorded_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = Path(self._settings(root, "claude-code").client_command)
            results = [
                subprocess.CompletedProcess(
                    [str(command), "auth", "status"],
                    0,
                    '{"loggedIn":true}',
                    "",
                ),
                subprocess.CompletedProcess(
                    [str(command), "--print"],
                    1,
                    "",
                    "OAuth access token has been revoked.",
                ),
            ]

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=results):
                ready, detail = verify_subscription_login("claude-code", command)

            self.assertFalse(ready)
            self.assertIn("revoked", detail)

    def test_login_verification_requires_the_exact_readiness_marker(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._settings(root).client_command

            def fake_run(
                invoked: list[str],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                if invoked[1:3] == ["login", "status"]:
                    return subprocess.CompletedProcess(invoked, 0, "Logged in using ChatGPT", "")
                output = Path(invoked[invoked.index("--output-last-message") + 1])
                output.write_text("You need to sign in first.", encoding="utf-8")
                return subprocess.CompletedProcess(invoked, 0, "", "")

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=fake_run):
                ready, detail = verify_subscription_login("codex", Path(command))

            self.assertFalse(ready)
            self.assertIn("expected ERGA_READY", detail)

    def test_long_responses_are_split_for_discord(self) -> None:
        chunks = split_discord_message("a" * 4_000)

        self.assertEqual([len(chunk) for chunk in chunks], [1_900, 1_900, 200])

    def test_settings_json_is_parseable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            target = write_discord_settings(config, self._settings(root))

            self.assertIsInstance(json.loads(target.read_text(encoding="utf-8")), dict)


if __name__ == "__main__":
    unittest.main()
