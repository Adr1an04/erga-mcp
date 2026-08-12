from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from erga_mcp.config import ErgaConfig, load_config

_MAIL_TABLE = re.compile(r"(?ms)^\[mail\]\n.*?(?=^\[|\Z)")


def as_json(config: ErgaConfig) -> dict[str, str]:
    return {
        "provider": config.mail_provider,
        "gws_command": config.gws_command,
        "client_id": config.mail_client_id,
        "accounts_url": config.mail_accounts_url,
        "folder": config.mail_folder,
    }


def update_settings(config_path: Path, updates: dict[str, str | None]) -> ErgaConfig:
    """Replace the generated config's non-secret mail table atomically."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    current = load_config(config_path)
    values = as_json(current)
    values.update({key: value for key, value in updates.items() if value is not None})
    table = "\n".join(
        [
            "[mail]",
            f"provider = {json.dumps(values['provider'])}",
            f"gws_command = {json.dumps(values['gws_command'])}",
            f"client_id = {json.dumps(values['client_id'])}",
            f"accounts_url = {json.dumps(values['accounts_url'])}",
            f"folder = {json.dumps(values['folder'])}",
        ]
    )
    if _MAIL_TABLE.search(raw) is None:
        raise ValueError("config must contain a [mail] table; rerun init or add one manually")
    replaced = _MAIL_TABLE.sub(f"{table}\n\n", raw, count=1)
    if replaced == raw:
        return current
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary_path = Path(temporary.name)
    try:
        settings = load_config(temporary_path)
        temporary_path.replace(config_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return settings
