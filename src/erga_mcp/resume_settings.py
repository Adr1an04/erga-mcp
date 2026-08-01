from __future__ import annotations

import json
import os
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
    """Update owned resume keys while preserving comments and forward-compatible settings."""
    config_path = config_path.expanduser()
    raw = config_path.read_text(encoding="utf-8")
    load_config(config_path)
    selected = {key: value for key, value in updates.items() if value is not None}
    replaced = _update_resume_table(raw, selected)
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


def _toml_value(value: object) -> str:
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(value)


def _comment_suffix(value: str) -> str:
    """Return an inline TOML comment while ignoring hashes inside quoted strings."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote == '"':
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif quote == "'":
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == "#":
            whitespace = len(value[:index]) - len(value[:index].rstrip())
            return value[index - whitespace :]
    return ""


def _update_resume_table(raw: str, updates: dict[str, object]) -> str:
    if not updates:
        return raw
    lines = raw.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == "[resume]"),
        None,
    )
    if start is None:
        separator = "" if not raw or raw.endswith("\n\n") else "\n"
        assignments = "\n".join(f"{key} = {_toml_value(value)}" for key, value in updates.items())
        return f"{raw}{separator}[resume]\n{assignments}\n"
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
        len(lines),
    )
    pending = dict(updates)
    assignment = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*?)(\r?\n)?$")
    for index in range(start + 1, end):
        match = assignment.match(lines[index])
        if match is None or match.group(2) not in pending:
            continue
        key = match.group(2)
        suffix = _comment_suffix(match.group(4))
        newline = match.group(5) or ""
        lines[index] = (
            f"{match.group(1)}{key}{match.group(3)}{_toml_value(pending.pop(key))}{suffix}{newline}"
        )
    if pending:
        insertion = [f"{key} = {_toml_value(value)}\n" for key, value in pending.items()]
        lines[end:end] = insertion
    return "".join(lines)
