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
from unittest.mock import AsyncMock, Mock, patch

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.integrations.discord.backends import DiscordBackendName
from erga_mcp.integrations.discord.bridge import (
    ERGA_INK,
    ERGA_LEAF,
    ERGA_ORBIT_VIOLET,
    ERGA_SUN,
    DiscordBridgeSettings,
    DiscordProcessRecord,
    ErgaUpdateError,
    ErgaUpdateResult,
    _backend_environment,
    _backend_prompt,
    _create_discord_client,
    _help_card,
    _is_help_request,
    _is_resume_request,
    _managed_resume_pdf,
    _progress_card,
    _record_matches_process,
    _render_resume_preview,
    _response_state,
    _result_cards,
    _update_failure_card,
    _update_result_card,
    build_backend_command,
    discord_status,
    is_authorized_discord_user,
    load_discord_settings,
    resolve_backend_command,
    run_backend,
    split_discord_message,
    update_erga_checkout,
    verify_backend_login,
    write_discord_settings,
)
from erga_mcp.integrations.discord.cards import discord_card_from_view
from erga_mcp.integrations.discord.resume_preferences import (
    is_resume_preference_query,
    parse_resume_preference_update,
    render_resume_preferences,
)
from erga_mcp.integrations.discord.setup import (
    DiscordSetupReport,
    detected_discord_backends,
    first_ready_discord_backend,
    parse_discord_identities,
    render_discord_setup_report,
)
from erga_mcp.tracking.cards import CardField, CardView


class DiscordBridgeTests(unittest.TestCase):
    def test_plain_language_help_needs_no_model_or_tool_vocabulary(self) -> None:
        self.assertTrue(_is_help_request("hi"))
        self.assertTrue(_is_help_request("what can you do"))
        self.assertFalse(_is_help_request("tailor my resume"))

        rendered = _help_card()
        self.assertIn("You do not need commands", rendered.description)
        self.assertIn("Tailor my résumé", rendered.description)
        self.assertNotIn("MCP", rendered.description)
        self.assertNotIn("tool", rendered.description.casefold())

    def test_plain_language_resume_defaults_are_parsed_without_capturing_tailoring(self) -> None:
        self.assertEqual(
            parse_resume_preference_update(
                "Change my resume defaults to two pages with 3-5 bullets per experience "
                "and 2-3 per project"
            ),
            {
                "max_pages": 2,
                "experience_min_bullets": 3,
                "experience_max_bullets": 5,
                "project_min_bullets": 2,
                "project_max_bullets": 3,
            },
        )
        self.assertEqual(
            parse_resume_preference_update(
                "Set my resume defaults to 2-4 bullets per experience/project"
            ),
            {
                "experience_min_bullets": 2,
                "experience_max_bullets": 4,
                "project_min_bullets": 2,
                "project_max_bullets": 4,
            },
        )
        self.assertIsNone(
            parse_resume_preference_update(
                "Tailor my resume for this role and use two pages if necessary"
            )
        )
        self.assertTrue(is_resume_preference_query("Show my current resume defaults"))

    def test_discord_updates_resume_defaults_without_starting_the_model_backend(self) -> None:
        class FakeIntents:
            message_content = False

            @classmethod
            def default(cls) -> FakeIntents:
                return cls()

        class FakeClient:
            def __init__(self, **_: object) -> None:
                self.user = SimpleNamespace(id=777)

        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            config.write_text(DEFAULT_CONFIG, encoding="utf-8")
            message = SimpleNamespace(
                author=SimpleNamespace(id=123456789, name="student", bot=False),
                guild=None,
                mentions=[],
                content=(
                    "Set my resume defaults to 2 pages with 3-5 bullets per experience "
                    "and 2-3 per project"
                ),
                channel=SimpleNamespace(id=123),
                reply=AsyncMock(),
            )
            fake_discord = SimpleNamespace(Intents=FakeIntents, Client=FakeClient)

            with (
                patch(
                    "erga_mcp.integrations.discord.bridge._discord_module",
                    return_value=fake_discord,
                ),
                patch("erga_mcp.integrations.discord.bridge.run_backend") as backend,
            ):
                client = _create_discord_client(
                    DiscordBridgeSettings(
                        backend="codex",
                        backend_command="/private/codex",
                        project_dir=Path("/private/project"),
                        allowed_user_ids=(123456789,),
                    ),
                    config_path=config,
                )
                asyncio.run(client.on_message(message))

            settings = load_config(config).resume
            self.assertEqual(settings.max_pages, 2)
            self.assertEqual(settings.experience_min_bullets, 3)
            self.assertEqual(settings.experience_max_bullets, 5)
            self.assertEqual(settings.project_min_bullets, 2)
            self.assertEqual(settings.project_max_bullets, 3)
            backend.assert_not_called()
            message.reply.assert_awaited_once_with(
                render_resume_preferences(settings, updated=True),
                mention_author=False,
            )

    def test_a_bare_job_link_defaults_to_resume_tailoring(self) -> None:
        self.assertTrue(_is_resume_request("https://jobs.example.test/platform-engineer"))
        self.assertFalse(_is_resume_request("research this https://company.example.test/about"))

    def test_discord_setup_auto_detects_runtime_and_reports_a_human_next_step(self) -> None:
        def resolve(name: str) -> Path:
            if name == "codex":
                return Path("/safe/codex")
            raise FileNotFoundError

        with patch(
            "erga_mcp.integrations.discord.setup.resolve_backend_command",
            side_effect=resolve,
        ):
            detected = detected_discord_backends()

        self.assertEqual(detected, (("codex", Path("/safe/codex")),))
        report = render_discord_setup_report(
            DiscordSetupReport(
                status="configured",
                settings_path="/private/settings",
                backend="codex",
                project_dir="/private/workspace",
                authorized_identities=1,
                token_storage="OS credential store",
                login_verified=True,
                host_connection_written=True,
                running=True,
                next_steps=["Open Discord and send: Tailor my résumé for this job: <paste link>"],
            )
        )
        self.assertIn("Discord is online", report)
        self.assertIn("Tailor my résumé", report)
        self.assertNotIn("backend", report.casefold())
        self.assertNotIn("MCP", report)

    def test_discord_setup_skips_a_broken_detected_runtime(self) -> None:
        candidates = (
            ("codex", Path("/broken/codex")),
            ("claude-code", Path("/safe/claude")),
        )
        with patch(
            "erga_mcp.integrations.discord.setup.verify_backend_login",
            side_effect=((False, "internal stack trace"), (True, "ready")),
        ) as verify:
            selected, detail = first_ready_discord_backend(
                candidates,
                project_dir=Path("/safe/project"),
            )

        self.assertEqual(selected, candidates[1])
        self.assertEqual(detail, "ready")
        self.assertEqual(verify.call_count, 2)

    def test_shared_card_renderer_stays_inside_discord_embed_limits(self) -> None:
        rendered = discord_card_from_view(
            CardView(
                title="T" * 400,
                summary="S" * 5_000,
                fields=tuple(
                    CardField(f"Field {index}" + "N" * 300, "V" * 2_000) for index in range(30)
                ),
            )
        )

        self.assertLessEqual(len(rendered.title), 256)
        self.assertLessEqual(len(rendered.description), 4_096)
        self.assertLessEqual(len(rendered.fields), 25)
        self.assertTrue(all(len(field.name) <= 256 for field in rendered.fields))
        self.assertTrue(all(len(field.value) <= 1_024 for field in rendered.fields))
        self.assertLessEqual(
            len(rendered.title)
            + len(rendered.description)
            + len(rendered.footer)
            + sum(len(field.name) + len(field.value) for field in rendered.fields),
            6_000,
        )

    def test_resume_progress_card_uses_erga_active_color_and_truthful_status(self) -> None:
        card = _progress_card(
            "make a résumé for https://jobs.example.test/role",
            elapsed_seconds=42,
        )

        self.assertEqual(card.color, ERGA_ORBIT_VIOLET)
        self.assertEqual(card.title, "✦ Tailoring your résumé")
        self.assertIn("choosing your strongest experience", card.description)
        self.assertEqual(card.fields[0].value, "Working through the one-page pipeline")
        self.assertEqual(card.fields[1].value, "42s")
        self.assertIn("no submission", card.fields[2].value)

    def test_update_outcome_cards_are_small_and_semantic(self) -> None:
        current = _update_result_card(
            ErgaUpdateResult(
                updated=False,
                previous_revision="a" * 40,
                current_revision="a" * 40,
            )
        )
        failure = _update_failure_card()

        self.assertEqual(current.title, "✓ Erga is current")
        self.assertEqual(current.description, "No update available.")
        self.assertEqual(current.color, ERGA_LEAF)
        self.assertEqual(current.fields, ())
        self.assertEqual(current.footer, "")
        self.assertEqual(failure.title, "↻ Try again")
        self.assertEqual(failure.color, ERGA_SUN)
        self.assertEqual(failure.fields, ())
        self.assertEqual(failure.footer, "")

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
                patch(
                    "erga_mcp.integrations.discord.bridge._discord_module",
                    return_value=fake_discord,
                ),
                patch(
                    "erga_mcp.integrations.discord.bridge.run_backend",
                    return_value=f"Validated PDF ready at {resume_pdf}",
                ),
                patch(
                    "erga_mcp.integrations.discord.bridge._render_resume_preview",
                    return_value=preview,
                ),
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
        self.assertIn("make the smallest change the user requested", prompt)
        self.assertIn("preserve every unmentioned section", prompt)
        self.assertIn("does not require a job description", prompt)
        self.assertIn("Never claim a file changed", prompt)
        self.assertIn("one-page fill check", prompt)
        self.assertIn("exact PDF artifact path returned by Erga", prompt)

    def test_discord_update_fast_forwards_only_official_clean_main_checkout(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'erga-mcp'\n")
            old_revision = "a" * 40
            new_revision = "b" * 40
            calls: list[list[str]] = []
            results = iter(
                (
                    subprocess.CompletedProcess([], 0, "true\n", ""),
                    subprocess.CompletedProcess([], 0, "main\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess(
                        [], 0, "https://github.com/Adr1an04/erga-mcp.git\n", ""
                    ),
                    subprocess.CompletedProcess([], 0, f"{old_revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, f"{new_revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, "Fast-forward\n", ""),
                    subprocess.CompletedProcess([], 0, f"{new_revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "Synced\n", ""),
                )
            )

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return next(results)

            result = update_erga_checkout(
                checkout_root=root,
                runner=fake_run,
                uv_command="/safe/uv",
            )

        self.assertTrue(result.updated)
        self.assertEqual(result.previous_revision, old_revision)
        self.assertEqual(result.current_revision, new_revision)
        self.assertIn(
            [
                "git",
                "fetch",
                "--quiet",
                "origin",
                "refs/heads/main:refs/remotes/origin/main",
            ],
            calls,
        )
        self.assertIn(["git", "merge", "--ff-only", "refs/remotes/origin/main"], calls)
        self.assertIn(["/safe/uv", "sync", "--extra", "discord", "--frozen"], calls)

    def test_discord_update_refuses_tracked_local_changes_before_fetching(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'erga-mcp'\n")
            calls: list[list[str]] = []
            results = iter(
                (
                    subprocess.CompletedProcess([], 0, "true\n", ""),
                    subprocess.CompletedProcess([], 0, "main\n", ""),
                    subprocess.CompletedProcess(
                        [], 0, " M src/erga_mcp/integrations/discord/bridge.py\n", ""
                    ),
                )
            )

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return next(results)

            with self.assertRaisesRegex(ErgaUpdateError, "tracked local changes"):
                update_erga_checkout(checkout_root=root, runner=fake_run, uv_command="/safe/uv")

        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in calls))

    def test_current_checkout_still_revalidates_frozen_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'erga-mcp'\n")
            revision = "a" * 40
            calls: list[list[str]] = []
            results = iter(
                (
                    subprocess.CompletedProcess([], 0, "true\n", ""),
                    subprocess.CompletedProcess([], 0, "main\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess(
                        [], 0, "https://github.com/Adr1an04/erga-mcp.git\n", ""
                    ),
                    subprocess.CompletedProcess([], 0, f"{revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, f"{revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "Synced\n", ""),
                )
            )

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return next(results)

            result = update_erga_checkout(
                checkout_root=root,
                runner=fake_run,
                uv_command="/safe/uv",
            )

        self.assertFalse(result.updated)
        self.assertEqual(result.upstream_revision, revision)
        self.assertIn(["/safe/uv", "sync", "--extra", "discord", "--frozen"], calls)

    def test_discord_update_treats_a_clean_local_main_ahead_of_github_as_current(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text("[project]\nname = 'erga-mcp'\n")
            local_revision = "b" * 40
            upstream_revision = "a" * 40
            calls: list[list[str]] = []
            results = iter(
                (
                    subprocess.CompletedProcess([], 0, "true\n", ""),
                    subprocess.CompletedProcess([], 0, "main\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess(
                        [], 0, "https://github.com/Adr1an04/erga-mcp.git\n", ""
                    ),
                    subprocess.CompletedProcess([], 0, f"{local_revision}\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                    subprocess.CompletedProcess([], 0, f"{upstream_revision}\n", ""),
                    subprocess.CompletedProcess([], 1, "", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                )
            )

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                return next(results)

            result = update_erga_checkout(
                checkout_root=root,
                runner=fake_run,
                uv_command="/safe/uv",
            )

        self.assertFalse(result.updated)
        self.assertEqual(result.current_revision, local_revision)
        self.assertIn(
            ["git", "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"],
            calls,
        )
        self.assertFalse(any(command[:2] == ["git", "merge"] for command in calls))

    def test_discord_update_command_restarts_only_after_a_successful_update(self) -> None:
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

            def add_field(self, **kwargs: object) -> None:
                self.fields.append(kwargs)

            def set_footer(self, **kwargs: object) -> None:
                self.footer = kwargs.get("text", "")

            def set_image(self, **_kwargs: object) -> None:
                return None

        class FakeClient:
            def __init__(self, **_: object) -> None:
                self.user = SimpleNamespace(id=777)
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_discord = SimpleNamespace(Intents=FakeIntents, Client=FakeClient, Embed=FakeEmbed)
            status_message = SimpleNamespace(edit=AsyncMock())
            message = SimpleNamespace(
                author=SimpleNamespace(id=123456789, name="student", bot=False),
                guild=SimpleNamespace(id=1),
                mentions=[SimpleNamespace(id=777)],
                content="<@!777> update",
                reply=AsyncMock(return_value=status_message),
            )
            restart = Mock()
            with (
                patch(
                    "erga_mcp.integrations.discord.bridge._discord_module",
                    return_value=fake_discord,
                ),
                patch(
                    "erga_mcp.integrations.discord.bridge.update_erga_checkout",
                    return_value=ErgaUpdateResult(
                        updated=True,
                        previous_revision="a" * 40,
                        current_revision="b" * 40,
                    ),
                ),
            ):
                client = _create_discord_client(
                    self._settings(root),
                    config_path=root / "config.toml",
                    runtime_nonce="private-nonce",
                    restart_bridge=restart,
                )
                asyncio.run(client.on_message(message))

        self.assertEqual(message.reply.await_count, 1)
        initial_embed = message.reply.await_args.kwargs["embed"]
        self.assertEqual(initial_embed.title, "✦ Checking for updates")
        self.assertEqual(initial_embed.description, "One moment.")
        self.assertEqual(initial_embed.fields, [])
        self.assertEqual(initial_embed.footer, "")
        self.assertEqual(status_message.edit.await_count, 1)
        final_embed = status_message.edit.await_args.kwargs["embed"]
        self.assertIsNone(status_message.edit.await_args.kwargs["content"])
        self.assertEqual(final_embed.title, "✓ Erga updated")
        self.assertEqual(final_embed.description, "Restarting with the latest version.")
        self.assertEqual(final_embed.fields, [])
        self.assertEqual(final_embed.footer, "")
        self.assertTrue(client.closed)
        restart.assert_called_once_with(root / "config.toml", "private-nonce")

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
                patch("erga_mcp.integrations.discord.bridge.shutil.which", return_value=None),
                patch(
                    "erga_mcp.integrations.discord.bridge._bundled_backend_candidates",
                    return_value=(bundled,),
                ),
            ):
                resolved = resolve_backend_command("codex")

            self.assertEqual(resolved, bundled.absolute())

    def test_missing_backend_does_not_claim_core_failed(self) -> None:
        with (
            patch("erga_mcp.integrations.discord.bridge.shutil.which", return_value=None),
            patch(
                "erga_mcp.integrations.discord.bridge._bundled_backend_candidates",
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

            with patch("erga_mcp.integrations.discord.bridge.subprocess.run", side_effect=fake_run):
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

            with patch("erga_mcp.integrations.discord.bridge.subprocess.run", side_effect=fake_run):
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

            with patch("erga_mcp.integrations.discord.bridge.subprocess.run", side_effect=fake_run):
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
            "erga_mcp.integrations.discord.bridge._process_command",
            return_value=(
                "python -m erga_mcp.integrations.discord.bridge --config /private/config.toml "
                "--runtime-nonce private-nonce"
            ),
        ):
            self.assertTrue(_record_matches_process(record))

        with patch(
            "erga_mcp.integrations.discord.bridge._process_command",
            return_value=(
                "python -m erga_mcp.discord_bridge --config /private/config.toml "
                "--runtime-nonce private-nonce"
            ),
        ):
            self.assertTrue(_record_matches_process(record))

        for command in (
            "python -m erga_mcp.integrations.discord.bridge --config /private/config.toml",
            "python -m erga_mcp.integrations.discord.bridge --config /other/config.toml "
            "--runtime-nonce private-nonce",
            "python unrelated.py --runtime-nonce private-nonce /private/config.toml",
        ):
            with patch(
                "erga_mcp.integrations.discord.bridge._process_command",
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
                patch("erga_mcp.integrations.discord.bridge.os.kill"),
                patch(
                    "erga_mcp.integrations.discord.bridge._record_matches_process",
                    return_value=True,
                ),
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
