from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from erga_mcp.models import Application

_INTERVIEW_STATUSES = frozenset(
    {"interview", "interview-2", "interview-3", "final-interview", "offer", "accepted"}
)
_OFFER_STATUSES = frozenset({"offer", "accepted"})
_MINIMUM_DIRECTIONAL_SAMPLE = 3


def _private_package_manifests(output_root: Path) -> tuple[dict[str, object], ...]:
    root = output_root.expanduser().resolve()
    if not root.is_dir():
        return ()
    manifests: list[dict[str, object]] = []
    for path in sorted(root.glob("*/*/package.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            manifests.append(value)
    return tuple(manifests)


def build_resume_outcome_report(
    *,
    applications: Sequence[Application],
    output_root: Path,
) -> dict[str, object]:
    """Summarize explicit résumé use and later local statuses without claiming causation."""
    application_by_id = {item.id: item for item in applications}
    project_samples: dict[str, list[str]] = defaultdict(list)
    used_versions = 0
    for manifest in _private_package_manifests(output_root):
        used_id = manifest.get("used_resume_version_id")
        raw_versions = manifest.get("resume_versions")
        if not isinstance(used_id, str) or not isinstance(raw_versions, list):
            continue
        version = next(
            (item for item in raw_versions if isinstance(item, dict) and item.get("id") == used_id),
            None,
        )
        if version is None or not isinstance(version.get("application_id"), str):
            continue
        application = application_by_id.get(version["application_id"])
        raw_project_ids = version.get("project_ids")
        if application is None or not isinstance(raw_project_ids, list):
            continue
        used_versions += 1
        for project_id in {item for item in raw_project_ids if isinstance(item, str)}:
            project_samples[project_id].append(application.status)

    signals: list[dict[str, object]] = []
    for project_id, statuses in project_samples.items():
        interviews = sum(status in _INTERVIEW_STATUSES for status in statuses)
        offers = sum(status in _OFFER_STATUSES for status in statuses)
        sample_count = len(statuses)
        signals.append(
            {
                "project_id": project_id,
                "uses": sample_count,
                "interview_or_better": interviews,
                "offer_or_better": offers,
                "interview_rate_percent": round(100 * interviews / sample_count),
                "eligible_for_directional_personalization": (
                    sample_count >= _MINIMUM_DIRECTIONAL_SAMPLE
                ),
            }
        )

    def signal_order(item: dict[str, object]) -> tuple[int, int, int, str]:
        uses = item.get("uses")
        interviews = item.get("interview_or_better")
        return (
            -int(item.get("eligible_for_directional_personalization") is True),
            -uses if isinstance(uses, int) else 0,
            -interviews if isinstance(interviews, int) else 0,
            str(item.get("project_id", "")),
        )

    signals.sort(key=signal_order)
    return {
        "explicitly_used_resume_versions": used_versions,
        "minimum_directional_sample": _MINIMUM_DIRECTIONAL_SAMPLE,
        "project_signals": signals,
        "interpretation": (
            "Local directional correlations only; outcomes do not prove that a project or résumé "
            "caused an interview, and this report never approves claims automatically."
        ),
    }
