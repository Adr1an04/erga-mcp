from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import ResumeSettings, load_config
from .private_files import restrict_private_file
from .toml_edit import update_table


def as_json(settings: ResumeSettings) -> dict[str, object]:
    result = asdict(settings)
    result["master_path"] = str(settings.master_path) if settings.master_path else None
    result["template_path"] = str(settings.template_path) if settings.template_path else None
    result["reference_path"] = str(settings.reference_path) if settings.reference_path else None
    result["output_root"] = str(settings.output_root)
    result["editable_sections"] = list(settings.editable_sections)
    return result


def update_settings(config_path: Path, updates: dict[str, object]) -> ResumeSettings:
    """Update owned resume keys while preserving comments and forward-compatible settings."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    load_config(config_path)
    selected = {key: value for key, value in updates.items() if value is not None}
    replaced = update_table(raw, "resume", selected)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        settings = load_config(temporary_path).resume
        restrict_private_file(temporary_path)
        temporary_path.replace(config_path)
        restrict_private_file(config_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return settings
