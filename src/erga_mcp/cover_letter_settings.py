from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import CoverLetterSettings, load_config


def as_json(settings: CoverLetterSettings) -> dict[str, object]:
    result = asdict(settings)
    result["template_path"] = str(settings.template_path) if settings.template_path else None
    result["writing_sample_path"] = (
        str(settings.writing_sample_path) if settings.writing_sample_path else None
    )
    return result


def update_settings(config_path: Path, updates: dict[str, object]) -> CoverLetterSettings:
    """Replace only the generated cover_letter table after validating the full config."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    current = load_config(config_path).cover_letter
    values: dict[str, object] = {
        "template_path": str(current.template_path) if current.template_path else "",
        "writing_sample_path": str(current.writing_sample_path)
        if current.writing_sample_path
        else "",
    }
    values.update({key: value for key, value in updates.items() if value is not None})
    table = "\n".join(
        [
            "[cover_letter]",
            f"template_path = {json.dumps(values['template_path'])}",
            f"writing_sample_path = {json.dumps(values['writing_sample_path'])}",
        ]
    )
    replaced = re.sub(
        r"(?ms)^\[cover_letter\]\n.*?(?=^\[|\Z)",
        lambda _match: f"{table}\n\n",
        raw,
    )
    if replaced == raw:
        raise ValueError(
            "config must contain a [cover_letter] table; rerun init or add one manually"
        )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary_path = Path(temporary.name)
    try:
        settings = load_config(temporary_path).cover_letter
    finally:
        temporary_path.unlink(missing_ok=True)
    config_path.write_text(replaced, encoding="utf-8")
    return settings
