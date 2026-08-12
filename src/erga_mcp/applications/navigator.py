from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from erga_mcp.applications.source import obvious_non_job_source
from erga_mcp.tracking.cards import CardAction, CardField, CardView
from erga_mcp.tracking.tracker import TrackerEntry

_MARKDOWN_WEB_LINK = re.compile(r"\[([^]\n]+)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_MAX_LINKS = 8
_ARTIFACT_LABELS = {
    "role-research": "Official role research",
    "secondary-research": "Secondary research (unverified)",
    "discovery-research": "Discovery research (unverified)",
    "oa-brief": "OA preparation brief",
    "oa-deep-research": "OA research dossier (unverified)",
    "interview-brief": "Interview preparation brief",
    "interview-deep-research": "Interview dossier (unverified)",
    "offer-brief": "Offer evaluation brief",
    "offer-deep-research": "Offer dossier (unverified)",
}
_STAGE_FOCUS = {
    "oa": (
        "Confirm the platform, deadline, duration, accommodations, and allowed resources. "
        "Practice the role's supported skill themes; do not use leaked questions or answer keys."
    ),
    "interview": (
        "Review the role requirements, likely process signals, and evidence-backed technical and "
        "behavioral stories. Treat community reports as patterns, never predictions."
    ),
    "offer": (
        "Verify compensation, equity mechanics, benefits, location policy, team conditions, and "
        "decision deadlines against the written offer."
    ),
}


@dataclass(frozen=True)
class ResearchLink:
    label: str
    url: str
    source: str
    unverified: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "url": self.url,
            "source": self.source,
            "unverified": self.unverified,
        }


@dataclass(frozen=True)
class ResearchArtifact:
    file_name: str
    label: str
    unverified: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "file_name": self.file_name,
            "label": self.label,
            "unverified": self.unverified,
        }


@dataclass(frozen=True)
class ResearchNavigator:
    stage: str
    links: tuple[ResearchLink, ...]
    artifacts: tuple[ResearchArtifact, ...]
    resume_available: bool
    package_available: bool
    card: CardView
    source_warning: str | None = None

    @property
    def saved_artifact_count(self) -> int:
        return len(self.artifacts)


def research_stage_for_status(status: str) -> str | None:
    """Return the active stage key once a role reaches an OA, interview, or offer."""
    normalized = " ".join(status.casefold().replace("-", " ").split())
    if normalized in {"oa", "online assessment", "assessment"}:
        return "oa"
    if "interview" in normalized:
        return "interview"
    if normalized == "offer" or normalized.startswith("offer "):
        return "offer"
    return None


def _public_url(value: str) -> str | None:
    normalized = value.strip()
    if any(character in normalized for character in "[]<>\"'"):
        return None
    parsed = urlsplit(normalized)
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return normalized


def _url_identity(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit(
        (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.path, "", "")
    )


def _compact(value: str, *, limit: int = 80) -> str:
    compact = " ".join(value.replace("`", "").split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1].rstrip()}…"


def _artifact_label(path: Path) -> str:
    return _ARTIFACT_LABELS.get(path.stem, path.stem.replace("-", " ").title())


def _artifact_is_unverified(path: Path) -> bool:
    return path.stem != "role-research"


def _saved_research(package_dir: Path | None) -> tuple[tuple[ResearchArtifact, str], ...]:
    if package_dir is None:
        return ()
    research_dir = package_dir / "research"
    if not research_dir.is_dir() or research_dir.is_symlink():
        return ()
    saved: list[tuple[ResearchArtifact, str]] = []
    for path in sorted(research_dir.glob("*.md"), key=lambda item: item.name.casefold()):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        saved.append(
            (
                ResearchArtifact(
                    file_name=path.name,
                    label=_artifact_label(path),
                    unverified=_artifact_is_unverified(path),
                ),
                content,
            )
        )
    return tuple(saved)


def _resume_available(package_dir: Path | None) -> bool:
    if package_dir is None or not package_dir.is_dir():
        return False
    candidates = (
        *package_dir.glob("*.pdf"),
        *(package_dir / "artifacts").glob("*.pdf"),
        *(package_dir / "resume").glob("*.pdf"),
    )
    return any(path.is_file() and not path.is_symlink() for path in candidates)


def _research_links(
    entry: TrackerEntry,
    saved: tuple[tuple[ResearchArtifact, str], ...],
    *,
    package_dir: Path | None,
    source_warning: str | None,
) -> tuple[ResearchLink, ...]:
    links: list[ResearchLink] = []
    seen: set[str] = set()
    official_url = _public_url(entry.source_url)
    if official_url is not None:
        links.append(
            ResearchLink(
                label=(
                    "Tracked source (not verified as a job posting)"
                    if source_warning
                    else "Official posting"
                ),
                url=official_url,
                source="tracker",
                unverified=source_warning is not None,
            )
        )
        seen.add(_url_identity(official_url))
    if source_warning is not None:
        return tuple(links)
    indexed = _indexed_research(package_dir)
    if indexed is not None:
        indexed_sources = indexed.get("sources")
        if not isinstance(indexed_sources, list):
            indexed_sources = []
        for source in indexed_sources:
            if not isinstance(source, dict):
                continue
            url = _public_url(str(source.get("url", "")))
            if url is None or _url_identity(url) in seen:
                continue
            seen.add(_url_identity(url))
            links.append(
                ResearchLink(
                    label=_compact(str(source.get("title", "Research source"))),
                    url=url,
                    source="discovery-research.json",
                    unverified=str(source.get("trust", "")) != "official",
                )
            )
            if len(links) == _MAX_LINKS:
                break
        return tuple(links)
    for artifact, content in saved:
        for match in _MARKDOWN_WEB_LINK.finditer(content):
            url = _public_url(match.group(2))
            if url is None:
                continue
            identity = _url_identity(url)
            if identity in seen:
                continue
            seen.add(identity)
            links.append(
                ResearchLink(
                    label=_compact(match.group(1)),
                    url=url,
                    source=artifact.file_name,
                    unverified=artifact.unverified,
                )
            )
            if len(links) == _MAX_LINKS:
                return tuple(links)
    return tuple(links)


def _indexed_research(package_dir: Path | None) -> dict[str, object] | None:
    if package_dir is None:
        return None
    path = package_dir / "research" / "discovery-research.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _research_quality(package_dir: Path | None) -> str | None:
    indexed = _indexed_research(package_dir)
    if indexed is None:
        return None
    statistics = indexed.get("statistics")
    coverage = indexed.get("coverage")
    if not isinstance(statistics, dict) or not isinstance(coverage, list):
        return None
    reviewed = statistics.get("candidates_reviewed", 0)
    retained = statistics.get("sources_retained", 0)
    rejected = statistics.get("sources_rejected", 0)
    lines = [f"{reviewed} candidates reviewed · {retained} retained · {rejected} rejected"]
    for item in coverage:
        if not isinstance(item, dict):
            continue
        label = _compact(str(item.get("label", "Research lane")), limit=60)
        lines.append(f"- {label}: {'covered' if item.get('covered') is True else 'gap'}")
    return "\n".join(lines)


def build_research_navigator(*, entry: TrackerEntry, package_dir: Path | None) -> ResearchNavigator:
    """Build one compact, client-neutral role-research view without exposing local paths."""
    stage = research_stage_for_status(entry.status)
    if stage is None:
        raise ValueError("research navigation becomes available when the role reaches OA")
    source_assessment = obvious_non_job_source(entry.source_url)
    source_warning = source_assessment.explanation if source_assessment is not None else None
    saved = _saved_research(package_dir)
    artifacts = tuple(artifact for artifact, _content in saved)
    if source_warning is not None:
        artifacts = tuple(
            ResearchArtifact(
                file_name=artifact.file_name,
                label=f"Legacy {artifact.label} (not trusted for this role)",
                unverified=True,
            )
            for artifact in artifacts
        )
    links = _research_links(
        entry,
        saved,
        package_dir=package_dir,
        source_warning=source_warning,
    )
    link_lines = [
        f"- [{link.label}{' (unverified)' if link.unverified else ''}]({link.url})"
        for link in links
    ]
    artifact_lines = [f"- {artifact.label}" for artifact in artifacts]
    package_available = package_dir is not None
    resume_available = _resume_available(package_dir)
    context_lines = [
        f"Status: {entry.status} · Cycle: {entry.cycle}",
        f"Tailored résumé: {'available' if resume_available else 'not found'}",
    ]
    if entry.next_action:
        context_lines.append(f"Next: {_compact(entry.next_action, limit=160)}")
    actions: list[CardAction] = []
    if package_available and source_warning is None:
        actions.extend(
            (
                CardAction(
                    "research.refresh",
                    "Refresh sources",
                    "Run bounded public research and reopen this navigator.",
                    "primary",
                ),
                CardAction(
                    "research.brief",
                    f"Create {stage.upper()} brief",
                    "Create or refresh the concise local stage-preparation checklist.",
                ),
            )
        )
    actions.append(
        CardAction("research.back", "Back to tracker", "Return to the same tracker page.")
    )
    fields: list[CardField] = []
    if source_warning is not None:
        fields.append(
            CardField(
                "Source problem",
                f"{source_warning} Replace it with the canonical ATS or company-careers URL.",
            )
        )
    fields.extend(
        (
            CardField(
                "Open on mobile",
                "\n".join(link_lines) if link_lines else "No public links are saved yet.",
            ),
            CardField(
                "Saved research",
                "\n".join(artifact_lines)
                if artifact_lines
                else "No research notes are saved yet. Refresh sources to create one.",
            ),
        )
    )
    quality = _research_quality(package_dir)
    if quality is not None:
        fields.append(CardField("Research quality", quality))
    fields.extend(
        (
            CardField(f"{stage.upper()} preparation", _STAGE_FOCUS[stage]),
            CardField("Application context", "\n".join(context_lines)),
        )
    )
    card = CardView(
        title=f"{stage.upper()} research · {_compact(entry.company)}",
        summary=(
            f"{_compact(entry.role, limit=120)} · {len(artifacts)} saved research "
            f"{'note' if len(artifacts) == 1 else 'notes'} · {len(links)} useful "
            f"{'link' if len(links) == 1 else 'links'}."
        ),
        fields=tuple(fields),
        actions=tuple(actions),
        footer=(
            "Community and secondary sources are unverified. Erga never submits an application "
            "or contacts anyone from this navigator."
        ),
    )
    return ResearchNavigator(
        stage=stage,
        links=links,
        artifacts=artifacts,
        resume_available=resume_available,
        package_available=package_available,
        card=card,
        source_warning=source_warning,
    )
