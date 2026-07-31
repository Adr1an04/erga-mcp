from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.discord_backends import DiscordBackendName
from erga_mcp.discord_bridge import (
    DiscordBridgeSettings,
    DiscordProcessRecord,
    _backend_environment,
    _record_matches_process,
    build_backend_command,
    is_authorized_discord_user,
    load_discord_settings,
    resolve_backend_command,
    run_backend,
    split_discord_message,
    verify_backend_login,
    write_discord_settings,
)
from erga_mcp.discord_setup import parse_discord_identities


class DiscordBridgeTests(unittest.TestCase):
    def _settings(
        self,
        root: Path,
        backend: DiscordBackendName = "codex",
    ) -> DiscordBridgeSettings:
        command = root / backend
        command.write_text("", encoding="utf-8")
        return DiscordBridgeSettings(
            backend=backend,
            backend_command=str(command),
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
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_resolves_codex_bundled_inside_a_desktop_app(self) -> None:
        with TemporaryDirectory() as directory:
            bundled = Path(directory) / "ChatGPT.app" / "Contents" / "Resources" / "codex"
            bundled.parent.mkdir(parents=True)
            bundled.write_text("#!/bin/sh\n", encoding="utf-8")
            bundled.chmod(0o755)

            with (
                patch("erga_mcp.discord_bridge.shutil.which", return_value=None),
                patch(
                    "erga_mcp.discord_bridge._bundled_backend_candidates",
                    return_value=(bundled,),
                ),
            ):
                resolved = resolve_backend_command("codex")

            self.assertEqual(resolved, bundled.absolute())

    def test_missing_backend_does_not_claim_core_failed(self) -> None:
        with (
            patch("erga_mcp.discord_bridge.shutil.which", return_value=None),
            patch(
                "erga_mcp.discord_bridge._bundled_backend_candidates",
                return_value=(),
            ),
        ):
            with self.assertRaisesRegex(FileNotFoundError, "core remains ready"):
                resolve_backend_command("codex")

    def test_accepts_modern_discord_usernames_and_numeric_ids(self) -> None:
        user_ids, usernames = parse_discord_identities(
            "emperor_sai, @student.dev, 123456789, EMPEROR_SAI"
        )

        self.assertEqual(user_ids, (123456789,))
        self.assertEqual(usernames, ("emperor_sai", "student.dev"))

    def test_rejects_obsolete_discriminator_names_with_guidance(self) -> None:
        with self.assertRaisesRegex(ValueError, "obsolete"):
            parse_discord_identities("student#1234")

    def test_authorizes_current_username_or_stable_id_but_never_a_bot(self) -> None:
        settings = DiscordBridgeSettings(
            backend="codex",
            backend_command="/tmp/codex",
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

    def test_builds_headless_commands_for_every_preset(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "response.txt"

            codex = build_backend_command(self._settings(root, "codex"), "prompt", output)
            claude = build_backend_command(self._settings(root, "claude-code"), "prompt", output)
            opencode = build_backend_command(self._settings(root, "opencode"), "prompt", output)
            opencode_v2 = build_backend_command(
                self._settings(root, "opencode-v2"), "prompt", output
            )
            gemini = build_backend_command(self._settings(root, "gemini-cli"), "prompt", output)
            cursor = build_backend_command(self._settings(root, "cursor"), "prompt", output)
            copilot = build_backend_command(
                self._settings(root, "github-copilot"),
                "prompt",
                output,
            )

            self.assertEqual(codex[1], "exec")
            self.assertIn("--output-last-message", codex)
            self.assertIn("--print", claude)
            self.assertIn("acceptEdits", claude)
            self.assertEqual(opencode[1], "run")
            self.assertEqual(opencode_v2[1], "run")
            self.assertIn("--allowed-mcp-server-names", gemini)
            self.assertIn("--approve-mcps", cursor)
            self.assertIn("--allow-tool=erga-mcp", copilot)

    def test_subscription_backends_do_not_inherit_model_api_keys(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "ANTHROPIC_API_KEY": "secret",
                "GEMINI_API_KEY": "secret",
                "CURSOR_API_KEY": "secret",
            },
        ):
            codex = _backend_environment("codex")
            claude = _backend_environment("claude-code")
            gemini = _backend_environment("gemini-cli")
            cursor = _backend_environment("cursor")
            copilot = _backend_environment("github-copilot")

        self.assertNotIn("OPENAI_API_KEY", codex)
        self.assertNotIn("ANTHROPIC_API_KEY", claude)
        self.assertNotIn("GEMINI_API_KEY", gemini)
        self.assertNotIn("CURSOR_API_KEY", cursor)
        self.assertEqual(copilot["GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP"], "true")

    def test_custom_backend_passes_arguments_without_a_shell(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "agent"
            executable.write_text("", encoding="utf-8")
            output = root / "response.txt"
            settings = DiscordBridgeSettings(
                backend="custom",
                backend_command=str(executable),
                project_dir=root,
                allowed_user_ids=(123,),
                custom_arguments=(
                    "--headless",
                    "--prompt={prompt}",
                    "--workspace",
                    "{project_dir}",
                ),
            )

            command = build_backend_command(settings, "hello; rm -rf /", output)

            self.assertEqual(
                command,
                [
                    str(executable),
                    "--headless",
                    "--prompt=hello; rm -rf /",
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
                rendered = run_backend(settings, "hello")

            self.assertEqual(rendered, "Final answer")

    def test_login_verification_includes_an_exact_live_readiness_turn(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            calls: list[list[str]] = []

            def fake_run(
                invoked: list[str],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                calls.append(invoked)
                if invoked[1:3] == ["login", "status"]:
                    return subprocess.CompletedProcess(invoked, 0, "Logged in", "")
                output = Path(invoked[invoked.index("--output-last-message") + 1])
                output.write_text("ERGA_READY", encoding="utf-8")
                return subprocess.CompletedProcess(invoked, 0, "", "")

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=fake_run):
                ready, detail = verify_backend_login(settings)

            self.assertTrue(ready)
            self.assertEqual(detail, "existing coding-host login is ready")
            self.assertEqual(len(calls), 2)
            self.assertIn("read-only", calls[1])

    def test_login_verification_rejects_a_nonexact_marker(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)

            def fake_run(
                invoked: list[str],
                **_kwargs: object,
            ) -> subprocess.CompletedProcess[str]:
                if invoked[1:3] == ["login", "status"]:
                    return subprocess.CompletedProcess(invoked, 0, "Logged in", "")
                output = Path(invoked[invoked.index("--output-last-message") + 1])
                output.write_text("ERGA_READY plus explanation", encoding="utf-8")
                return subprocess.CompletedProcess(invoked, 0, "", "")

            with patch("erga_mcp.discord_bridge.subprocess.run", side_effect=fake_run):
                ready, detail = verify_backend_login(settings)

            self.assertFalse(ready)
            self.assertIn("exact ERGA_READY", detail)

    def test_long_responses_are_split_for_discord(self) -> None:
        chunks = split_discord_message("a" * 4_000)

        self.assertEqual([len(chunk) for chunk in chunks], [1_900, 1_900, 200])

    def test_process_record_requires_module_nonce_and_exact_config(self) -> None:
        record = DiscordProcessRecord(
            pid=123,
            nonce="private-nonce",
            config_path="/private/config.toml",
        )

        with patch(
            "erga_mcp.discord_bridge._process_command",
            return_value=(
                "python -m erga_mcp.discord_bridge --config /private/config.toml "
                "--runtime-nonce private-nonce"
            ),
        ):
            self.assertTrue(_record_matches_process(record))

        for command in (
            "python -m erga_mcp.discord_bridge --config /private/config.toml",
            "python -m erga_mcp.discord_bridge --config /other/config.toml "
            "--runtime-nonce private-nonce",
            "python unrelated.py --runtime-nonce private-nonce /private/config.toml",
        ):
            with patch(
                "erga_mcp.discord_bridge._process_command",
                return_value=command,
            ):
                self.assertFalse(_record_matches_process(record))

    def test_settings_json_is_parseable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            target = write_discord_settings(config, self._settings(root))

            self.assertIsInstance(json.loads(target.read_text(encoding="utf-8")), dict)


if __name__ == "__main__":
    unittest.main()
