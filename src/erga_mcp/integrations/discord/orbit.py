"""Discord delivery for Erga's aggregate application-flow dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from erga_mcp.config import ErgaConfig, load_config
from erga_mcp.models import OrbitDashboardBinding
from erga_mcp.operations.private_files import restrict_private_directory, restrict_private_file
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.orbit import OrbitArtifact, create_orbit_artifact

_ORBIT_COLOR = 0x7C5CFF


def _store_and_config(config_path: Path) -> tuple[ErgaConfig, ErgaStore]:
    config = load_config(config_path)
    store = ErgaStore(config.data_dir / "erga.sqlite3")
    store.initialize()
    return config, store


def _create_artifact(config: ErgaConfig, store: ErgaStore, *, cycle: str) -> OrbitArtifact:
    output_dir = config.data_dir / "orbit"
    output_dir.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(output_dir)
    artifact = create_orbit_artifact(
        applications=store.list_applications(),
        audit_events=store.audit_events(),
        output_dir=output_dir,
        tracker_dir=(
            config.tracker.tracker_dir
            if config.tracker.enabled and config.tracker.tracker_dir is not None
            else None
        ),
        cycle=cycle,
    )
    restrict_private_file(artifact.image_path)
    return artifact


def _attachment(discord: Any, artifact: OrbitArtifact) -> tuple[Any, str]:
    filename = f"erga-orbit-{artifact.snapshot.content_hash}.png"
    return discord.File(artifact.image_path, filename=filename), filename


def _embed(discord: Any, artifact: OrbitArtifact, *, filename: str) -> Any:
    embed = discord.Embed(color=_ORBIT_COLOR)
    embed.set_image(url=f"attachment://{filename}")
    return embed


async def publish_orbit_dashboard(
    *,
    discord: Any,
    source_message: Any,
    config_path: Path,
    cycle: str,
) -> OrbitDashboardBinding:
    """Create or rebind one user-requested live Orbit message in this channel."""
    config, store = _store_and_config(config_path)
    artifact = _create_artifact(config, store, cycle=cycle)
    channel_id = str(source_message.channel.id)
    existing = next(
        (item for item in store.list_orbit_dashboards() if item.channel_id == channel_id),
        None,
    )
    dashboard_message = None
    if existing is not None:
        try:
            dashboard_message = await source_message.channel.fetch_message(int(existing.message_id))
            attachment, filename = _attachment(discord, artifact)
            await dashboard_message.edit(
                embed=_embed(discord, artifact, filename=filename),
                attachments=[attachment],
            )
        except discord.NotFound:
            dashboard_message = None
    if dashboard_message is None:
        attachment, filename = _attachment(discord, artifact)
        dashboard_message = await source_message.reply(
            embed=_embed(discord, artifact, filename=filename),
            file=attachment,
            mention_author=False,
        )
    return store.upsert_orbit_dashboard(
        channel_id=channel_id,
        message_id=str(dashboard_message.id),
        owner_user_id=str(source_message.author.id),
        cycle=cycle,
        content_hash=artifact.snapshot.content_hash,
    )


async def refresh_orbit_dashboards(
    *,
    discord: Any,
    client: Any,
    config_path: Path,
) -> dict[str, int]:
    """Refresh changed dashboards in place without invoking an AI model."""
    config, store = _store_and_config(config_path)
    bindings = store.list_orbit_dashboards()
    artifacts: dict[str, OrbitArtifact] = {}
    counts = {"checked": len(bindings), "updated": 0, "disabled": 0}
    for binding in bindings:
        artifact = artifacts.get(binding.cycle)
        if artifact is None:
            artifact = _create_artifact(config, store, cycle=binding.cycle)
            artifacts[binding.cycle] = artifact
        if binding.content_hash == artifact.snapshot.content_hash:
            continue
        try:
            channel = client.get_channel(int(binding.channel_id))
            if channel is None:
                channel = await client.fetch_channel(int(binding.channel_id))
            message = await channel.fetch_message(int(binding.message_id))
            attachment, filename = _attachment(discord, artifact)
            await message.edit(
                embed=_embed(discord, artifact, filename=filename),
                attachments=[attachment],
            )
        except discord.NotFound:
            store.disable_orbit_dashboard(binding.id)
            counts["disabled"] += 1
            continue
        except Exception:
            # A transient Discord or network failure must not erase a valid binding.
            continue
        store.update_orbit_dashboard_hash(binding.id, artifact.snapshot.content_hash)
        counts["updated"] += 1
    return counts


def stop_orbit_dashboard(*, config_path: Path, channel_id: int) -> bool:
    """Disable a channel's live refresh after an explicit user request."""
    _, store = _store_and_config(config_path)
    binding = next(
        (item for item in store.list_orbit_dashboards() if item.channel_id == str(channel_id)),
        None,
    )
    if binding is None:
        return False
    store.disable_orbit_dashboard(binding.id)
    return True
