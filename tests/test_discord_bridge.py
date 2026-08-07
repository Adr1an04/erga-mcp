from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.discord_backends import DiscordBackendName
from erga_mcp.discord_bridge import (
    ERGA_INK,
    ERGA_LEAF,
    ERGA_ORBIT_VIOLET,
    ERGA_SUN,
    DiscordBridgeSettings,
    DiscordProcessRecord,
    _backend_environment,
    _backend_prompt,
    _create_discord_client,
    _managed_resume_pdf,
    _progress_card,
    _record_matches_process,
    _render_resume_preview,
    _response_state,
    _result_cards,
    build_backend_command,
    discord_status,
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
    def test_resume_progress_card_uses_erga_active_color_and_truthful_status(self) -> None:
        card = _progress_card(
            "make a résumé for https://jobs.example.test/role",
            elapsed_seconds=42,
        )

        self.assertEqual(card.color, ERGA_ORBIT_VIOLET)
        self.assertEqual(card.title, "✦ Tailoring your résumé")
        self.assertIn("Evidence selection, tailoring, and validation", card.description)
        self.assertEqual(card.fields[0].value, "Working through the one-page pipeline")
        self.assertEqual(card.fields[1].value, "42s")
        self.assertIn("no submission", card.fields[2].value)

    def test_result_cards_use_semantic_orbit_colors(self) -> None:
        success = _result_cards(
            "Your validated PDF is ready at /private/resume.pdf",
            resume_request=True,
            elapsed_seconds=75,
        )
        warning = _result_cards(
            "⚠️ Résumé not ready. Validation failed.",
            resume_request=True,
            elapsed_seconds=12,
        )
        neutral = _result_cards(
            "Application tracker updated.", resume_request=False, elapsed_seconds=3
        )

        self.assertEqual(_response_state(success[0].description), "success")
        self.assertEqual(success[0].color, ERGA_LEAF)
        self.assertEqual(success[0].fields[1].value, "1m 15s")
        self.assertEqual(warning[0].color, ERGA_SUN)
        self.assertEqual(neutral[0].color, ERGA_INK)

    def test_discord_turn_never_attaches_a_model_reported_resume_path(self) -> None:
        class FakeIntents:
            message_content = False

            @classmethod
            def default(cls) -> FakeIntents:
                return cls()

        class FakeEmbed:
            def __init__(self, **kwargs: object) -> None:
                self.title = kwargs["title"]
                self.description = kwargs["description"]
                self.color = kwargs["color"]
                self.fields: list[dict[str, object]] = []
                self.footer = ""
                self.image: str | None = None

            def add_field(self, **kwargs: object) -> None:
                self.fields.append(kwargs)

            def set_footer(self, *, text: str) -> None:
                self.footer = text

            def set_image(self, *, url: str) -> None:
                self.image = url

        class FakeFile:
            def __init__(self, path: Path, *, filename: str) -> None:
                self.path = path
                self.filename = filename

        class FakeClient:
            def __init__(self, **_: object) -> None:
                self.user = SimpleNamespace(id=777)

        class Typing:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_: object) -> None:
                return None

        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume_pdf = root / "applications" / "summer-2027" / "role" / "artifacts" / "resume.pdf"
            resume_pdf.parent.mkdir(parents=True)
            resume_pdf.write_bytes(b"%PDF-1.4 synthetic fixture")
            preview = root / "preview.png"
            preview.write_bytes(b"synthetic preview")
            fake_discord = SimpleNamespace(
                Intents=FakeIntents,
                Client=FakeClient,
                Embed=FakeEmbed,
                File=FakeFile,
            )
            status_message = SimpleNamespace(edit=AsyncMock())
            reply = AsyncMock(return_value=status_message)
            message = SimpleNamespace(
                author=SimpleNamespace(id=123456789, name="student", bot=False),
                guild=None,
                mentions=[],
                content="make a resume for https://jobs.example.test/role",
                channel=SimpleNamespace(typing=lambda: Typing()),
                reply=reply,
            )

            with (
                patch("erga_mcp.discord_bridge._discord_module", return_value=fake_discord),
                patch(
                    "erga_mcp.discord_bridge.run_backend",
                    return_value=f"Validated PDF ready at {resume_pdf}",
                ),
                patch("erga_mcp.discord_bridge._render_resume_preview", return_value=preview),
            ):
                client = _create_discord_client(
                    DiscordBridgeSettings(
                        backend="codex",
                        backend_command="/private/codex",
                        project_dir=Path("/private/project"),
                        allowed_user_ids=(123456789,),
                    ),
                    attachment_roots=(root,),
                )
                asyncio.run(client.on_message(message))

        first_embed = reply.await_args_list[0].kwargs["embed"]
        final_embed = status_message.edit.await_args_list[-1].kwargs["embed"]
        self.assertEqual(reply.await_count, 1)
        self.assertEqual(first_embed.title, "✦ Tailoring your résumé")
        self.assertEqual(first_embed.color, ERGA_ORBIT_VIOLET)
        self.assertEqual(final_embed.title, "✓ Résumé ready for review")
        self.assertEqual(final_embed.color, ERGA_LEAF)
        self.assertIsNone(final_embed.image)
        self.assertNotIn("attachments", status_message.edit.await_args_list[-1].kwargs)
        self.assertNotEqual(final_embed.fields[-1]["value"], "Validated PDF attached")

    def test_model_reported_pdf_paths_are_never_auto_attached(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "cycle" / "role" / "artifacts" / "resume.pdf"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"%PDF-1.4 synthetic fixture")

            self.assertIsNone(
                _managed_resume_pdf(f"Validated PDF ready at {artifact}", attachment_roots=(root,))
            )

    @unittest.skipUnless(shutil.which("pdftoppm"), "pdftoppm is required to render previews")
    def test_resume_preview_renders_the_validated_pdf_first_page(self) -> None:
        from pypdf import PdfWriter

        with TemporaryDirectory() as directory:
            root = Path(directory)
            resume_pdf = root / "resume.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with resume_pdf.open("wb") as stream:
                writer.write(stream)

            preview = _render_resume_preview(resume_pdf, root)

            self.assertIsNotNone(preview)
            assert preview is not None
            self.assertTrue(preview.is_file())
            self.assertEqual(preview.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_backend_prompt_requires_canonical_validated_job_intake(self) -> None:
        prompt = _backend_prompt("make a resume for https://jobs.example.test/role")

        self.assertIn("use intake_job_url as the canonical end-to-end operation", prompt)
        self.assertIn("do not hand-edit proposal files", prompt)
        self.assertIn("one-page fill check", prompt)
        self.assertIn("exact PDF artifact path returned by Erga", prompt)

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

    def test_load_migrates_the_previous_client_settings_schema(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            command = root / "codex"
            command.write_text("", encoding="utf-8")
            target = root / "discord-bridge.json"
            target.write_text(
                json.dumps(
                    {
                        "client": "codex",
                        "client_command": str(command),
                        "project_dir": str(root),
                        "allowed_user_ids": [123],
                    }
                ),
                encoding="utf-8",
            )

            settings = load_discord_settings(config)
            migrated = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(settings.backend, "codex")
            self.assertEqual(migrated["backend"], "codex")
            self.assertNotIn("client", migrated)

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
            self.assertIn("--ephemeral", codex)
            self.assertEqual(codex[codex.index("--model") + 1], "gpt-5.6-terra")
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex)
            self.assertIn("workspace-write", codex)
            self.assertIn("--output-last-message", codex)
            self.assertIn("--print", claude)
            self.assertIn("acceptEdits", claude)
            self.assertEqual(opencode[1], "run")
            self.assertEqual(opencode_v2[1], "run")
            self.assertIn("--allowed-mcp-server-names", gemini)
            self.assertIn("--approve-mcps", cursor)
            self.assertIn("--allow-tool=erga-mcp", copilot)

            codex_probe = build_backend_command(
                self._settings(root, "codex"), "prompt", output, probe=True
            )
            self.assertIn("--ephemeral", codex_probe)
            self.assertEqual(
                codex_probe[codex_probe.index("--model") + 1],
                "gpt-5.6-terra",
            )
            self.assertIn("read-only", codex_probe)

    def test_backend_processes_receive_only_allowlisted_runtime_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PATH": "/safe/bin",
                "HOME": "/safe/home",
                "LC_ALL": "C.UTF-8",
                "OPENAI_API_KEY": "secret",
                "ANTHROPIC_API_KEY": "secret",
                "GEMINI_API_KEY": "secret",
                "CURSOR_API_KEY": "secret",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "DATABASE_URL": "secret",
                "ERGA_TEST_RANDOM_SECRET": "secret",
            },
            clear=True,
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
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", codex)
        self.assertNotIn("DATABASE_URL", claude)
        self.assertNotIn("ERGA_TEST_RANDOM_SECRET", gemini)
        self.assertEqual(codex["PATH"], "/safe/bin")
        self.assertEqual(codex["HOME"], "/safe/home")
        self.assertEqual(codex["LC_ALL"], "C.UTF-8")
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

    def test_status_distinguishes_running_from_gateway_ready(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text(DEFAULT_CONFIG, encoding="utf-8")
            write_discord_settings(config, self._settings(root))
            data_dir = load_config(config).data_dir
            data_dir.mkdir(parents=True)
            record = DiscordProcessRecord(
                pid=4321,
                nonce="private-nonce",
                config_path=str(config.absolute()),
            )
            (data_dir / "discord-bridge-process.json").write_text(
                json.dumps(record.__dict__), encoding="utf-8"
            )

            with (
                patch("erga_mcp.discord_bridge.os.kill"),
                patch("erga_mcp.discord_bridge._record_matches_process", return_value=True),
            ):
                starting = discord_status(config)
                (data_dir / "discord-bridge-ready.json").write_text(
                    json.dumps({"pid": 4321}), encoding="utf-8"
                )
                ready = discord_status(config)

            self.assertTrue(starting["running"])
            self.assertFalse(starting["ready"])
            self.assertTrue(ready["ready"])

    def test_settings_json_is_parseable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            target = write_discord_settings(config, self._settings(root))

            self.assertIsInstance(json.loads(target.read_text(encoding="utf-8")), dict)


if __name__ == "__main__":
    unittest.main()
