"""Hermes job-link router for deterministic first-turn Recruiting Pipeline intake."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

_DEFAULT_TOOL_NAME = "mcp__recruiting_pipeline__intake_job_url"
_MIN_HERMES_VERSION = (0, 18, 2)
_DEFAULT_READY_TIMEOUT_SECONDS = 30.0
_MAX_READY_TIMEOUT_SECONDS = 30.0
_DEFAULT_RETRY_INTERVAL_SECONDS = 0.25
_MAX_RETRY_INTERVAL_SECONDS = 5.0
_READY_TIMEOUT_ENV = "RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS"
_RETRY_INTERVAL_ENV = "RECRUITING_PIPELINE_MCP_READY_RETRY_SECONDS"
_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_NEGATED_SUMMARY = re.compile(
    r"\b(?:do\s+not|don't|dont|don’t|not|never)\s+"
    r"(?:(?:just|only)\s+)?summari[sz]e\b",
    re.IGNORECASE,
)
_OPT_OUT = re.compile(
    r"(?:\b(?:just|only)\s+summari[sz]e\b|"
    r"\bsummari[sz]e\s+only\b|"
    r"\b(?:do\s+not|don't|dont|don’t|not|never|skip)\s+(?:run\s+)?(?:the\s+)?"
    r"(?:job\s+)?(?:intake|pipeline)\b)",
    re.IGNORECASE,
)
_JOB_CONTEXT = re.compile(
    r"\b(?:apply|company overview|employment|full[- ]time|hiring|internship|job|"
    r"qualifications|responsibilities|role|salary|software engineer|work with us)\b",
    re.IGNORECASE,
)
_JOB_HOST_SUFFIXES = (
    "applytojob.com",
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "careers-page.com",
    "eightfold.ai",
    "greenhouse.io",
    "icims.com",
    "jobvite.com",
    "lever.co",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "oraclecloud.com",
    "phenompeople.com",
    "pinpointhq.com",
    "recruitee.com",
    "rippling-ats.com",
    "smartrecruiters.com",
    "successfactors.com",
    "teamtailor.com",
    "workable.com",
)
_JOB_HOST_LABELS = frozenset({"apply", "career", "careers", "jobs", "recruiting"})
_JOB_PATH_SEGMENTS = frozenset(
    {
        "apply",
        "career",
        "career-opportunities",
        "careers",
        "job",
        "job-detail",
        "job-details",
        "job-openings",
        "jobs",
        "join-us",
        "open-roles",
        "opening",
        "openings",
        "opportunities",
        "opportunity",
        "position",
        "positions",
        "roles",
        "vacancies",
        "vacancy",
    }
)
_JOB_QUERY_KEYS = frozenset({"gh_jid", "jk", "job", "job_id", "jobid", "posting_id", "position"})
_NON_PAGE_SUFFIXES = (
    ".avif",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp4",
    ".pdf",
    ".png",
    ".svg",
    ".webp",
)
_MAX_REMEMBERED_TURNS = 1024
_ROUTED_TURNS: OrderedDict[tuple[str, str, str], str | None] = OrderedDict()
_ROUTED_TURNS_LOCK = threading.Lock()


def supports_hermes_version(version: str) -> bool:
    """Return whether a Hermes version provides the plugin APIs used here."""
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", version or "")
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= _MIN_HERMES_VERSION


def _require_compatible_hermes() -> None:
    """Fail with an actionable message when loaded by an older Hermes host."""
    try:
        import hermes_cli
    except ModuleNotFoundError:
        # The standalone unit tests load the plugin without installing Hermes.
        return
    hermes_version = getattr(hermes_cli, "__version__", "unknown")
    if not supports_hermes_version(str(hermes_version)):
        required = ".".join(str(part) for part in _MIN_HERMES_VERSION)
        raise RuntimeError(
            f"recruiting-pipeline-router requires Hermes >= {required}; "
            f"found {hermes_version!s}. Run `hermes update` before enabling it."
        )


def _bounded_env_seconds(name: str, *, default: float, maximum: float) -> float:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, 0.0), maximum)


def _readiness_settings() -> tuple[float, float]:
    timeout = _bounded_env_seconds(
        _READY_TIMEOUT_ENV,
        default=_DEFAULT_READY_TIMEOUT_SECONDS,
        maximum=_MAX_READY_TIMEOUT_SECONDS,
    )
    retry_interval = _bounded_env_seconds(
        _RETRY_INTERVAL_ENV,
        default=_DEFAULT_RETRY_INTERVAL_SECONDS,
        maximum=_MAX_RETRY_INTERVAL_SECONDS,
    )
    # Avoid a busy loop while still allowing tests and operators to request a short interval.
    retry_interval = max(retry_interval, 0.01)
    return timeout, retry_interval


def _dispatch_error_text(result: object) -> str:
    if not isinstance(result, str):
        return ""
    try:
        payload = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result.strip()
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"].strip()
    return ""


def _is_retryable_startup_error(error_text: str, *, tool_name: str) -> bool:
    """Recognize only the two transient errors emitted during MCP startup."""
    if error_text == f"Unknown tool: {tool_name}":
        return True
    return bool(re.fullmatch(r"MCP server ['\"][^'\"]+['\"] is not connected", error_text))


def _candidate_urls(message: str) -> list[str]:
    candidates: list[str] = []
    for match in _URL.finditer(message):
        candidate = match.group(0).rstrip(".,;)]}")
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _looks_like_job_url(candidate: str) -> bool:
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").rstrip(".").casefold()
    if not host or parsed.path.casefold().endswith(_NON_PAGE_SUFFIXES):
        return False
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in _JOB_HOST_SUFFIXES):
        return True
    if (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ) and parsed.path.casefold().startswith("/jobs/"):
        return True
    if (host == "indeed.com" or host.endswith(".indeed.com")) and (
        parsed.path.casefold().startswith("/jobs/") or parsed.path.casefold().startswith("/viewjob")
    ):
        return True
    if (host == "wellfound.com" or host.endswith(".wellfound.com")) and "/jobs/" in (
        parsed.path.casefold() + "/"
    ):
        return True
    if (host == "ziprecruiter.com" or host.endswith(".ziprecruiter.com")) and "/jobs" in (
        parsed.path.casefold() + "/"
    ):
        return True
    if _JOB_HOST_LABELS.intersection(host.split(".")):
        return True
    segments = {part for part in unquote(parsed.path).casefold().split("/") if part}
    if _JOB_PATH_SEGMENTS.intersection(segments):
        return True
    query_keys = {key.casefold() for key in parse_qs(parsed.query, keep_blank_values=True)}
    return bool(_JOB_QUERY_KEYS.intersection(query_keys))


def extract_job_url(message: str) -> str | None:
    """Return the first job URL unless the user explicitly opts out of intake."""
    if not message:
        return None
    # A phrase such as "don't just summarize—run the pipeline" is positive intake intent,
    # not the "just summarize" opt-out embedded inside it. Remove only that negated clause
    # before evaluating the explicit opt-out patterns.
    opt_out_text = _NEGATED_SUMMARY.sub("", message)
    if _OPT_OUT.search(opt_out_text):
        return None
    candidates = _candidate_urls(message)
    for candidate in candidates:
        if _looks_like_job_url(candidate):
            return candidate
    if _JOB_CONTEXT.search(message):
        return next(
            (
                candidate
                for candidate in candidates
                if not urlsplit(candidate).path.casefold().endswith(_NON_PAGE_SUFFIXES)
            ),
            None,
        )
    return None


def register(
    ctx: Any,
    *,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Register deterministic job-link routing and an explicit slash-command fallback."""
    _require_compatible_hermes()
    monotonic_clock = monotonic or time.monotonic
    sleep_for = sleep or time.sleep
    tool_name = os.getenv("RECRUITING_PIPELINE_MCP_TOOL", _DEFAULT_TOOL_NAME).strip()
    ready_timeout, retry_interval = _readiness_settings()

    def dispatch(job_url: str) -> str:
        deadline = monotonic_clock() + ready_timeout
        attempts = 0
        while True:
            attempts += 1
            try:
                # Hermes >=0.18.2 documents this exact synchronous dispatch signature.
                result = ctx.dispatch_tool(tool_name, {"job_url": job_url})
            except Exception as error:  # Hermes isolates plugin exceptions; surface them safely.
                error_text = str(error).strip()
                if not _is_retryable_startup_error(error_text, tool_name=tool_name):
                    return f"Recruiting Pipeline intake failed: {type(error).__name__}: {error}"
                rendered_error = f"{type(error).__name__}: {error}"
            else:
                error_text = _dispatch_error_text(result)
                if not _is_retryable_startup_error(error_text, tool_name=tool_name):
                    return str(result)
                rendered_error = str(result)

            remaining = deadline - monotonic_clock()
            if remaining <= 0:
                return (
                    "Recruiting Pipeline intake failed after waiting "
                    f"{ready_timeout:g}s for MCP readiness ({attempts} attempts): "
                    f"{rendered_error}"
                )
            sleep_for(min(retry_interval, remaining))

    def route_job_link(
        user_message: str | None = None,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        platform: str = "",
        **_: Any,
    ) -> dict[str, str] | None:
        job_url = extract_job_url(user_message or "")
        if job_url is None:
            return None
        route_key = (session_id, turn_id, job_url)
        should_dispatch = True
        if turn_id:
            with _ROUTED_TURNS_LOCK:
                if route_key in _ROUTED_TURNS:
                    should_dispatch = False
                    result = _ROUTED_TURNS[route_key] or "Intake is already running for this turn."
                else:
                    _ROUTED_TURNS[route_key] = None
                    while len(_ROUTED_TURNS) > _MAX_REMEMBERED_TURNS:
                        _ROUTED_TURNS.popitem(last=False)
        if should_dispatch:
            result = dispatch(job_url)
            if turn_id:
                with _ROUTED_TURNS_LOCK:
                    _ROUTED_TURNS[route_key] = result
                    _ROUTED_TURNS.move_to_end(route_key)
        return {
            "context": (
                "Trusted Recruiting Pipeline router result: the user supplied a job link, so "
                f"the local intake tool was called before this model turn with {job_url!r}.\n"
                f"Tool result:\n{result}\n"
                "Do not call a browser or the intake tool again for this URL in this turn. "
                "Report the intake outcome and any actionable setup error to the user."
            )
        }

    def intake_command(raw_args: str) -> str:
        job_url = extract_job_url(raw_args)
        if job_url is None:
            return "Usage: /intake-job <job-posting-url>"
        return dispatch(job_url)

    ctx.register_hook("pre_llm_call", route_job_link)
    ctx.register_command(
        "intake-job",
        handler=intake_command,
        description="Run local Recruiting Pipeline intake for one job-posting URL.",
        args_hint="<job-posting-url>",
    )
