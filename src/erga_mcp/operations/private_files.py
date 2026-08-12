"""Owner-only filesystem permissions for Erga's local private state."""

from __future__ import annotations

import os
from pathlib import Path


def restrict_private_directory(path: Path) -> None:
    """Restrict an existing private-state directory to its owner on POSIX."""
    if os.name != "nt":
        path.chmod(0o700)


def restrict_private_file(path: Path) -> None:
    """Restrict an existing private-state file to its owner on POSIX."""
    if os.name != "nt":
        path.chmod(0o600)
