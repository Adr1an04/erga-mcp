from __future__ import annotations

from pathlib import Path

_SETTINGS_NAME = "discord-bridge.json"


def settings_path(config_path: Path) -> Path:
    """Return the non-secret settings path for the optional standalone Discord bridge."""
    return config_path.expanduser().absolute().parent / _SETTINGS_NAME
