from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import ResumeSettings, load_config
from .private_files import restrict_private_file


def as_json(settings: ResumeSettings) -> dict[str, object]:
    result = asdict(settings)
    result["master_path"] = str(settings.master_path) if settings.master_path else None
    result["template_path"] = str(settings.template_path) if settings.template_path else None
    result["reference_path"] = str(settings.reference_path) if settings.reference_path else None
    result["output_root"] = str(settings.output_root)
    result["editable_sections"] = list(settings.editable_sections)
    return result


def update_settings(config_path: Path, updates: dict[str, object]) -> ResumeSettings:
    """Replace the generated config's resume table without touching unrelated tables."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    current = load_config(config_path).resume
    values: dict[str, object] = {
        "master_path": str(current.master_path) if current.master_path else "",
        "template_path": str(current.template_path) if current.template_path else "",
        "reference_path": str(current.reference_path) if current.reference_path else "",
        "editable_sections": list(current.editable_sections),
        "bullet_min_chars": current.bullet_min_chars,
        "bullet_target_chars": current.bullet_target_chars,
        "bullet_max_chars": current.bullet_max_chars,
        "max_pages": current.max_pages,
        "output_root": str(current.output_root),
        "output_pdf_name": current.output_pdf_name,
        "latexmk": current.latexmk,
    }
    values.update({key: value for key, value in updates.items() if value is not None})
    table = "\n".join(
        [
            "[resume]",
            f"master_path = {json.dumps(values['master_path'])}",
            f"template_path = {json.dumps(values['template_path'])}",
            f"reference_path = {json.dumps(values['reference_path'])}",
            f"editable_sections = {json.dumps(values['editable_sections'])}",
            f"bullet_min_chars = {values['bullet_min_chars']}",
            f"bullet_target_chars = {values['bullet_target_chars']}",
            f"bullet_max_chars = {values['bullet_max_chars']}",
            f"max_pages = {values['max_pages']}",
            f"output_root = {json.dumps(values['output_root'])}",
            f"output_pdf_name = {json.dumps(values['output_pdf_name'])}",
            f"latexmk = {json.dumps(values['latexmk'])}",
        ]
    )
    replaced = re.sub(
        r"(?ms)^\[resume\]\n.*?(?=^\[|\Z)",
        lambda _match: f"{table}\n\n",
        raw,
    )
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
    restrict_private_file(config_path)
    return settings
