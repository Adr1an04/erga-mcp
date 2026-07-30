from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from erga_mcp.config import load_config
from erga_mcp.setup_wizard import (
    SetupSelections,
    _parse_discord_identities,
    apply_setup,
    render_setup_review,
    write_setup_plan,
)


class SetupWizardTests(unittest.TestCase):
    def _command(self, root: Path, name: str) -> Path:
        command = root / name
        command.write_text("", encoding="utf-8")
        return command

    def test_local_setup_configures_resume_and_client(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume = root / "resume.tex"
            resume.write_text("\\documentclass{article}\n", encoding="utf-8")
            selections = SetupSelections(
                experience="local",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("resume",),
                resume_template=resume,
                output_root=root / "applications",
            )

            with patch(
                "erga_mcp.setup_wizard.verify_subscription_login",
                return_value=(True, "Logged in using ChatGPT"),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                    client_command=self._command(root, "codex"),
                )

            config = load_config(selections.config_path)
            self.assertEqual(report.status, "ready")
            self.assertTrue(report.resume_configured)
            self.assertEqual(config.resume.template_path, resume)
            self.assertTrue((root / ".codex" / "config.toml").is_file())

    def test_full_setup_configures_native_discord_without_hermes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selections = SetupSelections(
                experience="full",
                client="claude-code",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("discord",),
                discord_token="discord-secret",
                discord_user_ids=(123456789,),
                start_discord=True,
            )

            with (
                patch(
                    "erga_mcp.setup_wizard.verify_subscription_login",
                    return_value=(True, '{"subscriptionType":"max"}'),
                ),
                patch("erga_mcp.setup_wizard.store_discord_token") as store_token,
                patch(
                    "erga_mcp.setup_wizard.start_discord_bridge",
                    return_value={"running": True, "pid": 123},
                ),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                    client_command=self._command(root, "claude"),
                )

            self.assertTrue(report.discord_configured)
            self.assertTrue(report.discord_running)
            store_token.assert_called_once_with(selections.config_path, "discord-secret")
            settings = (selections.config_path.parent / "discord-bridge.json").read_text()
            self.assertNotIn("discord-secret", settings)
            self.assertNotIn("hermes", settings.casefold())

    def test_modern_discord_usernames_do_not_require_developer_mode(self) -> None:
        user_ids, usernames = _parse_discord_identities("emperor_sai, @trusted.friend, 123456789")

        self.assertEqual(user_ids, (123456789,))
        self.assertEqual(usernames, ("emperor_sai", "trusted.friend"))

    def test_invalid_discord_discriminator_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid Discord"):
            _parse_discord_identities("oldname#1234")

    def test_review_and_dry_run_redact_the_discord_token(self) -> None:
        selections = SetupSelections(
            experience="full",
            client="codex",
            project_dir=Path("/workspace"),
            config_path=Path("/private/config.toml"),
            features=("discord",),
            discord_token="super-secret-token",
            discord_user_ids=(123,),
        )

        rendered = render_setup_review(selections) + write_setup_plan(selections)

        self.assertNotIn("super-secret-token", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("Hermes", rendered)

    def test_local_review_omits_unselected_discord_fields(self) -> None:
        selections = SetupSelections(
            experience="local",
            client="codex",
            project_dir=Path("/workspace"),
            config_path=Path("/private/config.toml"),
            features=("resume",),
        )

        rendered = render_setup_review(selections)

        self.assertNotIn("Discord", rendered)
        self.assertIn("Resume template", rendered)

    def test_generic_client_persists_headless_arguments_for_discord(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = self._command(root, "another-agent")
            selections = SetupSelections(
                experience="full",
                client="generic-mcp",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("discord",),
                client_command=command,
                custom_arguments=("--headless", "{prompt}"),
                discord_token="discord-secret",
                discord_user_ids=(123,),
                start_discord=False,
            )

            with (
                patch(
                    "erga_mcp.setup_wizard.verify_subscription_login",
                    return_value=(True, "ready"),
                ),
                patch("erga_mcp.setup_wizard.store_discord_token"),
            ):
                report = apply_setup(
                    selections,
                    server_command=self._command(root, "erga-mcp"),
                )

            settings = json.loads(
                (selections.config_path.parent / "discord-bridge.json").read_text()
            )
            self.assertTrue(report.discord_configured)
            self.assertEqual(settings["custom_arguments"], ["--headless", "{prompt}"])
            self.assertTrue((root / ".mcp.json").is_file())

    def test_setup_stops_when_the_coding_subscription_is_not_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selections = SetupSelections(
                experience="local",
                client="codex",
                project_dir=root,
                config_path=root / "private" / "config.toml",
                features=("resume",),
            )

            with patch(
                "erga_mcp.setup_wizard.verify_subscription_login",
                return_value=(False, "Not logged in"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Sign in first"):
                    apply_setup(
                        selections,
                        server_command=self._command(root, "erga-mcp"),
                        client_command=self._command(root, "codex"),
                    )

            self.assertFalse(selections.config_path.exists())


if __name__ == "__main__":
    unittest.main()
