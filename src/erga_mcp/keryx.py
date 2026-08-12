"""Explicit, read-only integration with Keryx's public US opportunity index."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import questionary

from .config import ErgaConfig, load_config
from .private_files import restrict_private_directory, restrict_private_file
from .toml_edit import update_table

KERYX_INDEX_URL = "https://raw.githubusercontent.com/GodlyDonuts/keryx/main/data/jobs.json"
KERYX_CACHE_SCHEMA_VERSION = 1
_SUPPORTED_INDEX_SCHEMAS = frozenset({1, 2})
_MAX_INDEX_BYTES = 32 * 1024 * 1024
_MAX_JOBS = 50_000
_MAX_RESULT_LIMIT = 50
_LINK_STATUSES = frozenset({"ats-verified", "cross-source", "platform-structured", "unverified"})
_PROGRAMS = frozenset({"internship", "new-grad"})
_ELIGIBILITY_STATUSES = frozenset(
    {"explicit-date", "explicit-window", "student-status", "not-found", "unavailable"}
)
_REQUIREMENT_LEVELS = frozenset({"required", "preferred", "stated"})
_ELIGIBILITY_CONFIDENCE = frozenset({"direct-ats", "metadata-only"})
_JOB_ID = re.compile(r"job_[0-9a-f]{24}")
_CYCLE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")

FetchIndex = Callable[[], bytes]


@dataclass(frozen=True)
class KeryxStatus:
    enabled: bool
    cache_ready: bool
    cached_jobs: int
    fetched_at: str | None
    source_url: str

    def as_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class KeryxSyncReport:
    enabled: bool
    cached_jobs: int
    fetched_at: str
    source_url: str

    def as_json(self) -> dict[str, object]:
        return asdict(self)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _download_index() -> bytes:
    request = Request(
        KERYX_INDEX_URL,
        headers={
            "Accept": "application/json, text/plain;q=0.9",
            "User-Agent": "erga-mcp-keryx/0.1",
        },
    )
    try:
        with build_opener(_RejectRedirects).open(request, timeout=30) as response:  # noqa: S310
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > _MAX_INDEX_BYTES:
                raise ValueError("Keryx index exceeds the 32 MiB download limit")
            payload = response.read(_MAX_INDEX_BYTES + 1)
    except HTTPError as error:
        raise OSError(f"Keryx index request failed with HTTP {error.code}") from error
    if len(payload) > _MAX_INDEX_BYTES:
        raise ValueError("Keryx index exceeds the 32 MiB download limit")
    return payload


def _bounded_text(value: object, *, field: str, maximum: int = 300) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Keryx job {field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"Keryx job {field} is empty or oversized")
    return normalized


def _safe_public_url(job: Mapping[str, object]) -> str | None:
    value = job.get("url")
    status = str(job.get("link_status") or "")
    if status not in _LINK_STATUSES:
        raise ValueError("Keryx job has an invalid link status")
    if value is None:
        if status != "unverified":
            raise ValueError("Keryx withheld URL has a contradictory link status")
        return None
    if not isinstance(value, str) or len(value) > 4_096 or "\\" in value:
        raise ValueError("Keryx job URL is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Keryx job URL contains control characters")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("Keryx job URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Keryx job URL contains unsafe authority or fragment data")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Keryx job URL contains an invalid port") from error
    if port not in {None, 443}:
        raise ValueError("Keryx job URL uses a nonstandard port")
    host = parsed.hostname.rstrip(".").casefold()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("Keryx job URL must not use an IP-literal host")
    if host != str(job.get("url_host") or "").casefold():
        raise ValueError("Keryx job URL host does not match its metadata")
    fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    if fingerprint != job.get("url_fingerprint"):
        raise ValueError("Keryx job URL fingerprint does not match")
    if status == "unverified":
        raise ValueError("Keryx unverified job must not expose a URL")
    return value


def _academic_eligibility(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Keryx academic eligibility must be an object")
    result: dict[str, object] = {}
    for key in (
        "status",
        "summary",
        "requirement_level",
        "evidence",
        "graduation_evidence",
        "currently_enrolled_level",
        "currently_enrolled_evidence",
        "return_to_school_level",
        "return_to_school_evidence",
        "graduation_start",
        "graduation_end",
        "checked_at",
        "confidence",
        "source_id",
        "source_label",
    ):
        field = value.get(key)
        if field is None:
            continue
        if not isinstance(field, str) or len(field) > 300:
            raise ValueError(f"Keryx academic eligibility {key} is invalid")
        result[key] = field
    status = result.get("status")
    if status is not None and status not in _ELIGIBILITY_STATUSES:
        raise ValueError("Keryx academic eligibility status is invalid")
    for key in ("requirement_level", "currently_enrolled_level", "return_to_school_level"):
        level = result.get(key)
        if level is not None and level not in _REQUIREMENT_LEVELS:
            raise ValueError(f"Keryx academic eligibility {key} is invalid")
    confidence = result.get("confidence")
    if confidence is not None and confidence not in _ELIGIBILITY_CONFIDENCE:
        raise ValueError("Keryx academic eligibility confidence is invalid")
    for key in ("currently_enrolled", "return_to_school"):
        field = value.get(key)
        if field is not None:
            if not isinstance(field, bool):
                raise ValueError(f"Keryx academic eligibility {key} must be boolean")
            result[key] = field
    years = value.get("graduation_years")
    if years is not None:
        if not isinstance(years, list) or any(
            not isinstance(year, int) or year < 2000 or year > 2100 for year in years
        ):
            raise ValueError("Keryx academic eligibility graduation_years is invalid")
        result["graduation_years"] = years
    return result


def _validated_job(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Keryx jobs must be objects")
    identifier = str(value.get("id") or "")
    if _JOB_ID.fullmatch(identifier) is None:
        raise ValueError("Keryx job has an invalid identifier")
    program = str(value.get("program") or "")
    if program not in _PROGRAMS:
        raise ValueError("Keryx job has an invalid program")
    cycle = str(value.get("cycle") or "")
    if _CYCLE.fullmatch(cycle) is None:
        raise ValueError("Keryx job has an invalid cycle")
    status = str(value.get("status") or "")
    if status not in {"open", "closed"}:
        raise ValueError("Keryx job has an invalid status")
    posted_at = value.get("posted_at")
    if posted_at is not None and (
        not isinstance(posted_at, str) or re.fullmatch(r"20\d{2}-\d{2}-\d{2}", posted_at) is None
    ):
        raise ValueError("Keryx job has an invalid posted date")
    return {
        "id": identifier,
        "company": _bounded_text(value.get("company"), field="company"),
        "title": _bounded_text(value.get("title"), field="title"),
        "location": _bounded_text(value.get("location"), field="location"),
        "program": program,
        "cycle": cycle,
        "posted_at": posted_at,
        "status": status,
        "url": _safe_public_url(value),
        "link_status": str(value.get("link_status")),
        "academic_eligibility": _academic_eligibility(value.get("academic_eligibility")),
    }


def _validated_index(payload: bytes) -> tuple[int, list[dict[str, object]]]:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Keryx index is not valid UTF-8 JSON") from error
    if not isinstance(document, Mapping):
        raise ValueError("Keryx index must be a JSON object")
    schema_version = document.get("schema_version")
    if schema_version not in _SUPPORTED_INDEX_SCHEMAS:
        raise ValueError("Keryx index schema is not supported")
    if document.get("country") != "United States":
        raise ValueError("Keryx index is not the US-only dataset")
    raw_jobs = document.get("jobs")
    if not isinstance(raw_jobs, list) or len(raw_jobs) > _MAX_JOBS:
        raise ValueError("Keryx index jobs are missing or oversized")
    jobs = [_validated_job(job) for job in raw_jobs]
    if len({str(job["id"]) for job in jobs}) != len(jobs):
        raise ValueError("Keryx index contains duplicate job identifiers")
    return int(schema_version), jobs


def _cache_path(config: ErgaConfig) -> Path:
    return config.data_dir / "integrations" / "keryx" / "jobs.json"


def _atomic_private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    config_dir = path.parent
    restrict_private_directory(config_dir)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=config_dir, prefix=f".{path.name}-", delete=False
    ) as temporary:
        temporary.write(text)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        restrict_private_file(temporary_path)
        temporary_path.replace(path)
        restrict_private_file(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_enabled(config_path: Path, enabled: bool) -> None:
    raw = config_path.read_text(encoding="utf-8")
    rendered = update_table(raw, "keryx", {"enabled": enabled})
    _atomic_private_write(config_path, rendered)
    load_config(config_path)


def _write_cache(
    config: ErgaConfig,
    *,
    index_schema_version: int,
    jobs: list[dict[str, object]],
    fetched_at: datetime,
) -> KeryxSyncReport:
    captured = fetched_at.astimezone(UTC).replace(microsecond=0).isoformat()
    cache = {
        "cache_schema_version": KERYX_CACHE_SCHEMA_VERSION,
        "index_schema_version": index_schema_version,
        "source_url": KERYX_INDEX_URL,
        "fetched_at": captured,
        "jobs": jobs,
    }
    _atomic_private_write(_cache_path(config), json.dumps(cache, indent=2, sort_keys=True) + "\n")
    return KeryxSyncReport(
        enabled=True,
        cached_jobs=len(jobs),
        fetched_at=captured,
        source_url=KERYX_INDEX_URL,
    )


def enable_keryx(
    config_path: Path,
    *,
    fetch: FetchIndex = _download_index,
    fetched_at: datetime | None = None,
) -> KeryxSyncReport:
    """Validate and cache the public index before recording the explicit opt-in."""
    config = load_config(config_path)
    index_schema, jobs = _validated_index(fetch())
    report = _write_cache(
        config,
        index_schema_version=index_schema,
        jobs=jobs,
        fetched_at=fetched_at or datetime.now(UTC),
    )
    _write_enabled(config_path, True)
    return report


def disable_keryx(config_path: Path) -> KeryxStatus:
    """Disable discovery without deleting the harmless public cache."""
    _write_enabled(config_path, False)
    return keryx_status(load_config(config_path))


def sync_keryx(
    config: ErgaConfig,
    *,
    fetch: FetchIndex = _download_index,
    fetched_at: datetime | None = None,
) -> KeryxSyncReport:
    if not config.keryx.enabled:
        raise ValueError("Keryx is disabled; run `erga keryx enable` first")
    index_schema, jobs = _validated_index(fetch())
    return _write_cache(
        config,
        index_schema_version=index_schema,
        jobs=jobs,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def _read_cache(config: ErgaConfig) -> dict[str, object]:
    path = _cache_path(config)
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError("Keryx cache is missing; run `erga keryx sync`") from error
    except json.JSONDecodeError as error:
        raise ValueError("Keryx cache is corrupt; run `erga keryx sync`") from error
    if (
        not isinstance(cache, dict)
        or cache.get("cache_schema_version") != KERYX_CACHE_SCHEMA_VERSION
    ):
        raise ValueError("Keryx cache schema is unsupported; run `erga keryx sync`")
    if cache.get("source_url") != KERYX_INDEX_URL or not isinstance(cache.get("jobs"), list):
        raise ValueError("Keryx cache provenance is invalid; run `erga keryx sync`")
    return cache


def keryx_status(config: ErgaConfig) -> KeryxStatus:
    try:
        cache = _read_cache(config)
    except (FileNotFoundError, ValueError):
        return KeryxStatus(
            enabled=config.keryx.enabled,
            cache_ready=False,
            cached_jobs=0,
            fetched_at=None,
            source_url=KERYX_INDEX_URL,
        )
    jobs = cache["jobs"]
    assert isinstance(jobs, list)
    fetched_at = cache.get("fetched_at")
    return KeryxStatus(
        enabled=config.keryx.enabled,
        cache_ready=True,
        cached_jobs=len(jobs),
        fetched_at=str(fetched_at) if isinstance(fetched_at, str) else None,
        source_url=KERYX_INDEX_URL,
    )


def search_keryx_jobs(
    config: ErgaConfig,
    *,
    query: str = "",
    program: str = "",
    cycle: str = "",
    location: str = "",
    limit: int = 20,
) -> dict[str, object]:
    """Search only the local public cache; never send search terms or private state anywhere."""
    if not config.keryx.enabled:
        raise ValueError("Keryx is disabled; run `erga keryx enable` first")
    if program and program not in _PROGRAMS:
        raise ValueError("Keryx program must be internship or new-grad")
    if limit < 1 or limit > _MAX_RESULT_LIMIT:
        raise ValueError(f"Keryx result limit must be between 1 and {_MAX_RESULT_LIMIT}")
    terms = tuple(term.casefold() for term in query.split() if term.strip())
    expected_cycle = cycle.strip().casefold()
    expected_location = location.strip().casefold()
    cache = _read_cache(config)
    raw_jobs = cache["jobs"]
    assert isinstance(raw_jobs, list)
    matches: list[dict[str, object]] = []
    for value in raw_jobs:
        if not isinstance(value, dict) or value.get("status") != "open":
            continue
        if program and value.get("program") != program:
            continue
        if expected_cycle and str(value.get("cycle") or "").casefold() != expected_cycle:
            continue
        if (
            expected_location
            and expected_location not in str(value.get("location") or "").casefold()
        ):
            continue
        haystack = " ".join(
            str(value.get(field) or "") for field in ("company", "title", "location")
        ).casefold()
        if not all(term in haystack for term in terms):
            continue
        matches.append(value)
    matches.sort(
        key=lambda job: (
            str(job.get("posted_at") or ""),
            str(job.get("company") or "").casefold(),
            str(job.get("title") or "").casefold(),
        ),
        reverse=True,
    )
    return {
        "source": "Keryx public US opportunity index",
        "source_url": KERYX_INDEX_URL,
        "cache_fetched_at": cache.get("fetched_at"),
        "query_was_local_only": True,
        "applications_created": 0,
        "total_matches": len(matches),
        "results": matches[:limit],
    }


def collect_optional_keryx() -> bool:
    """Offer Keryx only after core setup has completed; skipping is fully supported."""
    answer = questionary.confirm(
        "Enable optional Keryx public US internship and new-grad discovery?",
        default=False,
    ).ask()
    return bool(answer)
