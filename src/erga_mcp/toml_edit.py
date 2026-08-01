"""Small, lossless updates for Erga-owned keys in human-edited TOML tables."""

from __future__ import annotations

import json
import re

_ASSIGNMENT = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*?)(\r?\n)?$")
_TABLE_HEADER = re.compile(r"^\s*\[(?!\[)[^\]]+\]\s*(?:#.*)?(?:\r?\n)?$")


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


def update_table(raw: str, name: str, updates: dict[str, object]) -> str:
    """Update selected keys in one TOML table without replacing user-owned text."""
    if not updates:
        return raw
    lines = raw.splitlines(keepends=True)
    expected_header = f"[{name}]"
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == expected_header),
        None,
    )
    if start is None:
        separator = "" if not raw or raw.endswith("\n\n") else "\n"
        assignments = "\n".join(f"{key} = {_toml_value(value)}" for key, value in updates.items())
        return f"{raw}{separator}{expected_header}\n{assignments}\n"

    end = next(
        (index for index in range(start + 1, len(lines)) if _TABLE_HEADER.match(lines[index])),
        len(lines),
    )
    pending = dict(updates)
    for index in range(start + 1, end):
        match = _ASSIGNMENT.match(lines[index])
        if match is None or match.group(2) not in pending:
            continue
        key = match.group(2)
        suffix = _comment_suffix(match.group(4))
        newline = match.group(5) or ""
        lines[index] = (
            f"{match.group(1)}{key}{match.group(3)}{_toml_value(pending.pop(key))}{suffix}{newline}"
        )
    if pending:
        lines[end:end] = [f"{key} = {_toml_value(value)}\n" for key, value in pending.items()]
    return "".join(lines)
