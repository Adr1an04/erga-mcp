from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.integrations.discord.orbit import (
    publish_orbit_dashboard,
    refresh_orbit_dashboards,
    stop_orbit_dashboard,
)
from erga_mcp.store import ErgaStore


class _FakeEmbed:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.image = ""
        self.footer = ""

    def set_image(self, *, url: str) -> None:
        self.image = url

    def set_footer(self, *, text: str) -> None:
        self.footer = text


class _FakeFile:
    def __init__(self, path: Path, *, filename: str) -> None:
        self.path = Path(path)
        self.filename = filename


class _FakeNotFound(Exception):
    pass


FAKE_DISCORD = SimpleNamespace(Embed=_FakeEmbed, File=_FakeFile, NotFound=_FakeNotFound)


class DiscordOrbitTests(unittest.IsolatedAsyncioTestCase):
    def _workspace(self, root: Path) -> tuple[Path, ErgaStore]:
        config_path = root / "config.toml"
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
        config = load_config(config_path)
        store = ErgaStore(config.data_dir / "erga.sqlite3")
        application = store.create_application(
            company="Example",
            role="Engineer Intern",
            source_url="https://jobs.example.test/role",
            evidence_ids=[],
        )
        store.update_application_status(application.id, status="applied")
        return config_path, store

    async def test_explicit_orbit_request_posts_and_persists_one_live_dashboard(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            dashboard_message = SimpleNamespace(id=444, edit=AsyncMock())
            channel = SimpleNamespace(id=222, fetch_message=AsyncMock())
            source_message = SimpleNamespace(
                author=SimpleNamespace(id=111),
                channel=channel,
                reply=AsyncMock(return_value=dashboard_message),
            )

            binding = await publish_orbit_dashboard(
                discord=FAKE_DISCORD,
                source_message=source_message,
                config_path=config_path,
                cycle="",
            )

            self.assertEqual(binding.channel_id, "222")
            self.assertEqual(binding.message_id, "444")
            self.assertEqual(binding.owner_user_id, "111")
            source_message.reply.assert_awaited_once()
            sent = source_message.reply.await_args.kwargs
            self.assertTrue(sent["file"].path.is_file())
            self.assertIn(binding.content_hash, sent["file"].filename)
            self.assertEqual(sent["embed"].image, f"attachment://{sent['file'].filename}")
            self.assertEqual(store.list_orbit_dashboards(), [binding])

    async def test_repeated_request_edits_the_existing_dashboard_instead_of_spamming(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            existing_message = SimpleNamespace(id=444, edit=AsyncMock())
            channel = SimpleNamespace(
                id=222,
                fetch_message=AsyncMock(return_value=existing_message),
            )
            store.upsert_orbit_dashboard(
                channel_id="222",
                message_id="444",
                owner_user_id="111",
                content_hash="old",
            )
            source_message = SimpleNamespace(
                author=SimpleNamespace(id=111),
                channel=channel,
                reply=AsyncMock(),
            )

            binding = await publish_orbit_dashboard(
                discord=FAKE_DISCORD,
                source_message=source_message,
                config_path=config_path,
                cycle="Summer 2027",
            )

            source_message.reply.assert_not_awaited()
            existing_message.edit.assert_awaited_once()
            self.assertEqual(binding.message_id, "444")
            self.assertEqual(binding.cycle, "Summer 2027")

    async def test_deleted_dashboard_is_replaced_with_a_fresh_attachment(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            channel = SimpleNamespace(
                id=222,
                fetch_message=AsyncMock(side_effect=_FakeNotFound()),
            )
            store.upsert_orbit_dashboard(
                channel_id="222",
                message_id="333",
                owner_user_id="111",
                content_hash="old",
            )
            replacement = SimpleNamespace(id=444)
            source_message = SimpleNamespace(
                author=SimpleNamespace(id=111),
                channel=channel,
                reply=AsyncMock(return_value=replacement),
            )

            binding = await publish_orbit_dashboard(
                discord=FAKE_DISCORD,
                source_message=source_message,
                config_path=config_path,
                cycle="",
            )

            source_message.reply.assert_awaited_once()
            self.assertEqual(binding.message_id, "444")

    async def test_live_refresh_is_hash_gated_and_updates_only_after_data_changes(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            dashboard_message = SimpleNamespace(id=444, edit=AsyncMock())
            channel = SimpleNamespace(
                id=222,
                fetch_message=AsyncMock(return_value=dashboard_message),
            )
            client = SimpleNamespace(
                get_channel=lambda _channel_id: channel,
                fetch_channel=AsyncMock(return_value=channel),
            )
            source_message = SimpleNamespace(
                author=SimpleNamespace(id=111),
                channel=channel,
                reply=AsyncMock(return_value=dashboard_message),
            )
            binding = await publish_orbit_dashboard(
                discord=FAKE_DISCORD,
                source_message=source_message,
                config_path=config_path,
                cycle="",
            )

            unchanged = await refresh_orbit_dashboards(
                discord=FAKE_DISCORD,
                client=client,
                config_path=config_path,
            )
            self.assertEqual(unchanged, {"checked": 1, "updated": 0, "disabled": 0})
            dashboard_message.edit.assert_not_awaited()

            application = store.list_applications()[0]
            store.update_application_status(application.id, status="interview")
            changed = await refresh_orbit_dashboards(
                discord=FAKE_DISCORD,
                client=client,
                config_path=config_path,
            )

            self.assertEqual(changed, {"checked": 1, "updated": 1, "disabled": 0})
            dashboard_message.edit.assert_awaited_once()
            refreshed = store.list_orbit_dashboards()[0]
            self.assertNotEqual(refreshed.content_hash, binding.content_hash)

    async def test_deleted_live_message_is_disabled_during_refresh(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            store.upsert_orbit_dashboard(
                channel_id="222",
                message_id="444",
                owner_user_id="111",
                content_hash="outdated",
            )
            channel = SimpleNamespace(
                fetch_message=AsyncMock(side_effect=_FakeNotFound()),
            )
            client = SimpleNamespace(
                get_channel=lambda _channel_id: channel,
                fetch_channel=AsyncMock(),
            )

            result = await refresh_orbit_dashboards(
                discord=FAKE_DISCORD,
                client=client,
                config_path=config_path,
            )

            self.assertEqual(result, {"checked": 1, "updated": 0, "disabled": 1})
            self.assertEqual(store.list_orbit_dashboards(), [])

    def test_stop_disables_only_the_requested_channel(self) -> None:
        with TemporaryDirectory() as directory:
            config_path, store = self._workspace(Path(directory))
            store.upsert_orbit_dashboard(
                channel_id="222",
                message_id="444",
                owner_user_id="111",
            )

            self.assertTrue(stop_orbit_dashboard(config_path=config_path, channel_id=222))
            self.assertFalse(stop_orbit_dashboard(config_path=config_path, channel_id=222))
            self.assertEqual(store.list_orbit_dashboards(), [])


if __name__ == "__main__":
    unittest.main()
