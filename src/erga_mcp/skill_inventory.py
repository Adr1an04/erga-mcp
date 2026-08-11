from __future__ import annotations

import csv

_MAX_SKILL_LENGTH = 80


def clean_skill_name(value: str) -> str:
    """Return one safe display value while preserving the user's chosen casing."""
    skill = " ".join(value.split())
    if not skill:
        raise ValueError("skill names cannot be empty")
    if len(skill) > _MAX_SKILL_LENGTH:
        raise ValueError(f"skill names must be at most {_MAX_SKILL_LENGTH} characters")
    if any(character in skill for character in (",", "\n", "\r", "\x00")):
        raise ValueError("skill names cannot contain commas, newlines, or NUL bytes")
    return skill


def normalize_skill_name(value: str) -> str:
    return clean_skill_name(value).casefold()


def unique_skill_names(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for value in values:
        cleaned = clean_skill_name(value)
        unique.setdefault(cleaned.casefold(), cleaned)
    return tuple(unique.values())


def parse_skill_seed_csv(value: str) -> tuple[str, ...]:
    """Parse one comma-separated, user-maintained seed list deterministically."""
    if not value.strip():
        raise ValueError("skill CSV must contain at least one skill")
    try:
        rows = list(csv.reader([value], strict=True, skipinitialspace=True))
    except csv.Error as error:
        raise ValueError("skill CSV is invalid") from error
    if len(rows) != 1:
        raise ValueError("skill CSV must be a single line")
    return unique_skill_names(tuple(rows[0]))
