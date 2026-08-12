from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from erga_mcp.applications.research import JobResearch
from erga_mcp.resumes.artifacts import normalize_cycle

_SAFE_PACKAGE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_TRACKING_QUERY_KEYS = frozenset(
    {
        "gh_src",
        "fbclid",
        "lever-source",
        "ref",
        "referrer",
        "source",
        "sourceid",
        "trk",
        "tracking",
    }
)


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80] or "job-intake"


def slug_with_identifier(label: str, identifier: str) -> str:
    """Keep the stable identifier inside the 80-character slug limit."""
    safe_identifier = safe_slug(identifier)[:20]
    safe_label = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or "job"
    label_limit = 80 - len(safe_identifier) - 1
    prefix = safe_label[:label_limit].rstrip("-") or "job"
    return f"{prefix}-{safe_identifier}"


def job_identity(job_url: str) -> str:
    """Return a stable listing identity while discarding common tracking parameters."""
    parsed = urlsplit(job_url)
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlunsplit((scheme, netloc, parsed.path or "/", urlencode(query, doseq=True), ""))


def posting_identifier(job_url: str) -> str:
    """Hash the complete canonical identity instead of a collision-prone raw ID prefix."""
    return hashlib.sha256(job_identity(job_url).encode("utf-8")).hexdigest()[:16]


def metadata_from_url(job_url: str, *, cycle: str, application_slug: str) -> tuple[str, str]:
    parsed = urlsplit(job_url)
    host_parts = [part for part in parsed.hostname.split(".") if part] if parsed.hostname else []
    path_parts = [part for part in parsed.path.split("/") if part]
    generic = {
        "apply",
        "boards",
        "careers",
        "en",
        "external",
        "job",
        "jobs",
        "openings",
        "positions",
        "us",
        "view",
        "viewjob",
        "www",
    }
    hosted_boards = {
        "ashbyhq",
        "greenhouse",
        "job-boards",
        "lever",
    }
    host_candidate = ""
    if host_parts:
        host_candidate = next(
            (part for part in host_parts if part.casefold() not in generic), host_parts[0]
        )
    company_source = host_candidate if host_candidate.casefold() not in hosted_boards else ""
    if not company_source:
        company_source = next(
            (part for part in path_parts if part.casefold() not in generic), "company"
        )
    company = re.sub(r"[-_]", " ", company_source).title()
    role_source = path_parts[-1] if path_parts else "job opportunity"
    identifier = posting_identifier(job_url)
    if (
        role_source.casefold() in generic
        or re.fullmatch(r"[0-9a-f-]{20,}", role_source.casefold())
        or re.fullmatch(r"\d{5,}", role_source)
    ):
        role_source = "job opportunity"
    role = re.sub(r"[-_]", " ", role_source).title()
    resolved_cycle = cycle.strip() or "unsorted"
    resolved_slug = application_slug.strip() or slug_with_identifier(
        f"{company}-{role}", identifier
    )
    return resolved_cycle, resolved_slug


def metadata_from_research(
    job_url: str,
    research: JobResearch,
    *,
    cycle: str,
    application_slug: str,
) -> tuple[str, str]:
    """Prefer source-derived metadata after fetch while preserving explicit overrides."""
    resolved_cycle = cycle.strip() or (research.cycles[0] if research.cycles else "unsorted")
    resolved_slug = application_slug.strip() or slug_with_identifier(
        f"{research.company}-{research.role}", posting_identifier(job_url)
    )
    return resolved_cycle, resolved_slug


def package_dir(output_root: Path, cycle: str, application_slug: str) -> Path:
    """Resolve and validate the final package location without creating it."""
    normalized_cycle = normalize_cycle(cycle)
    if not _SAFE_PACKAGE_COMPONENT.fullmatch(
        normalized_cycle
    ) or not _SAFE_PACKAGE_COMPONENT.fullmatch(application_slug):
        raise ValueError("cycle and application slug must be safe path component values")
    return output_root / normalized_cycle / application_slug


def selected_evidence_ids(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [
        item["id"] for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


def package_created_at(package_dir: Path) -> str:
    try:
        manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    if isinstance(manifest, dict) and isinstance(manifest.get("created_at"), str):
        return manifest["created_at"]
    return datetime.now(UTC).isoformat()


def cycle_from_package(package_dir: Path) -> str | None:
    value = package_dir.parent.name
    match = re.fullmatch(r"(spring|summer|fall|winter)-(20\d{2})", value, re.IGNORECASE)
    if match is None:
        return None
    return f"{match.group(1).title()} {match.group(2)}"
