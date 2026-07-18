from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import ResumeSettings, load_config


def as_json(settings: ResumeSettings) -> dict[str, object]:
    result = asdict(settings)
    result["template_path"] = str(settings.template_path) if settings.template_path else None
    result["output_root"] = str(settings.output_root)
    result["editable_sections"] = list(settings.editable_sections)
    return result


def update_settings(config_path: Path, updates: dict[str, object]) -> ResumeSettings:
    """Replace the generated config's resume table without touching unrelated tables."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    current = load_config(config_path).resume
    values: dict[str, object] = {
        "template_path": str(current.template_path) if current.template_path else "",
        "editable_sections": list(current.editable_sections),
        "bullet_min_chars": current.bullet_min_chars,
        "bullet_target_chars": current.bullet_target_chars,
        "bullet_max_chars": current.bullet_max_chars,
        "max_pages": current.max_pages,
        "output_root": str(current.output_root),
        "latexmk": current.latexmk,
    }
    values.update({key: value for key, value in updates.items() if value is not None})
    table = "\n".join(
        [
            "[resume]",
            f"template_path = {json.dumps(values['template_path'])}",
            f"editable_sections = {json.dumps(values['editable_sections'])}",
            f"bullet_min_chars = {values['bullet_min_chars']}",
            f"bullet_target_chars = {values['bullet_target_chars']}",
            f"bullet_max_chars = {values['bullet_max_chars']}",
            f"max_pages = {values['max_pages']}",
            f"output_root = {json.dumps(values['output_root'])}",
            f"latexmk = {json.dumps(values['latexmk'])}",
        ]
    )
    replaced = re.sub(r"(?ms)^\[resume\]\n.*?(?=^\[|\Z)", f"{table}\n\n", raw)
    if replaced == raw:
        raise ValueError("config must contain a [resume] table; rerun init or add one manually")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary_path = Path(temporary.name)
    try:
        settings = load_config(temporary_path).resume
    finally:
        temporary_path.unlink(missing_ok=True)
    config_path.write_text(replaced, encoding="utf-8")
    return settings
