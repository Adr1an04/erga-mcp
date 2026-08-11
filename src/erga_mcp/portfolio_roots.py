from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .config import load_config
from .private_files import restrict_private_file
from .toml_edit import update_table

_CONVENTIONAL_PROJECT_DIRECTORIES = (
    ("hermesworkspace", "projects"),
    ("Projects",),
    ("projects",),
    ("Developer",),
    ("dev",),
    ("workspace",),
    ("Documents", "GitHub"),
)


def detected_portfolio_root(home: Path | None = None) -> Path | None:
    """Find one conservative conventional project root without crawling the home directory."""
    base = (home or Path.home()).expanduser().resolve()
    for parts in _CONVENTIONAL_PROJECT_DIRECTORIES:
        candidate = base.joinpath(*parts)
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            contains_repository = (candidate / ".git").exists() or any(
                child.is_dir() and not child.is_symlink() and (child / ".git").exists()
                for child in candidate.iterdir()
            )
        except OSError:
            continue
        if contains_repository:
            return candidate.resolve(strict=True)
    return None


def canonical_portfolio_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    canonical: list[Path] = []
    for root in roots:
        candidate = root.expanduser().absolute()
        if candidate.is_symlink():
            raise ValueError("portfolio roots cannot be symlinked directories")
        if not candidate.is_dir():
            raise ValueError(f"portfolio root must be an existing directory: {candidate}")
        resolved = candidate.resolve(strict=True)
        if resolved not in canonical:
            canonical.append(resolved)
    return tuple(canonical)


def update_portfolio_roots(config_path: Path, roots: Sequence[Path]) -> tuple[Path, ...]:
    """Atomically persist only explicit, existing local roots."""
    config_path = config_path.expanduser()
    canonical = canonical_portfolio_roots(roots)
    raw = config_path.read_text(encoding="utf-8")
    load_config(config_path)
    replaced = update_table(raw, "paths", {"portfolio_roots": [str(root) for root in canonical]})
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_path.parent, delete=False
    ) as temporary:
        temporary.write(replaced)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        loaded = load_config(temporary_path).portfolio_roots
        restrict_private_file(temporary_path)
        temporary_path.replace(config_path)
        restrict_private_file(config_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return loaded
