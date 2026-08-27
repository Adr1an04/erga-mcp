from __future__ import annotations

import json
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from erga_mcp.store import ErgaStore

_SCHEMA_VERSION = 1


def _json_record(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"unsupported export value: {type(value).__name__}")


def _package_files(output_root: Path) -> list[Path]:
    if not output_root.exists():
        return []
    resolved_root = output_root.expanduser().resolve()
    files: list[Path] = []
    for manifest_path in resolved_root.rglob("package.json"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("status") != "complete":
            continue
        package_dir = manifest_path.parent
        for candidate in package_dir.rglob("*"):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            candidate.resolve().relative_to(resolved_root)
            files.append(candidate)
    return sorted(files)


def export_bundle(
    *,
    store: ErgaStore,
    output_root: Path,
    destination: Path,
    resume_assets: Mapping[str, Path | None] | None = None,
    exported_at: datetime | None = None,
) -> dict[str, object]:
    """Export local pipeline state and generated job packages to a new ZIP bundle."""
    destination = destination.expanduser()
    if destination.suffix.casefold() != ".zip":
        raise ValueError("export destination must use the .zip extension")
    if destination.exists():
        raise FileExistsError(f"export already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    applications = store.list_applications()
    evidence = store.list_evidence()
    mail_events = store.list_mail_events()
    audit_events = store.audit_events()
    snapshot = {
        "schema_version": _SCHEMA_VERSION,
        "exported_at": (exported_at or datetime.now(UTC)).isoformat(),
        "applications": [asdict(item) for item in applications],
        "evidence": [asdict(item) for item in evidence],
        "mail_events": [asdict(item) for item in mail_events],
        "audit_events": [asdict(item) for item in audit_events],
    }
    package_files = _package_files(output_root)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, suffix=".zip", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "erga-snapshot.json",
                json.dumps(snapshot, default=_json_record, indent=2, sort_keys=True) + "\n",
            )
            resolved_root = output_root.expanduser().resolve()
            for package_file in package_files:
                relative = package_file.relative_to(resolved_root)
                archive.write(package_file, Path("job-packages") / relative)
            for name, asset in (resume_assets or {}).items():
                if asset is None or asset.is_symlink() or not asset.is_file():
                    continue
                if not name or Path(name).name != name:
                    raise ValueError("exported resume asset names must be safe filenames")
                archive.write(asset, Path("resume-assets") / name)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return {
        "archive": str(destination.resolve()),
        "applications": len(applications),
        "evidence": len(evidence),
        "mail_events": len(mail_events),
        "audit_events": len(audit_events),
        "package_files": len(package_files),
        "resume_assets": sum(
            asset is not None and asset.is_file() for asset in (resume_assets or {}).values()
        ),
    }
