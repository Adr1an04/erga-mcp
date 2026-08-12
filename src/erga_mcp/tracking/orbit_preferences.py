from __future__ import annotations

import os
import tempfile
from pathlib import Path

from erga_mcp.config import OrbitSettings, load_config
from erga_mcp.operations.private_files import restrict_private_file
from erga_mcp.operations.toml_edit import update_table


def update_orbit_preferences(config_path: Path, *, retain_generated_images: bool) -> OrbitSettings:
    """Persist the local Orbit image-retention preset without disturbing other settings."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    load_config(config_path)
    replaced = update_table(
        raw,
        "orbit",
        {"retain_generated_images": retain_generated_images},
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        settings = load_config(temporary_path).orbit
        restrict_private_file(temporary_path)
        temporary_path.replace(config_path)
        restrict_private_file(config_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return settings
