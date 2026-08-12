from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from erga_mcp.applications.research import JobResearch

_ATS_HOST_SUFFIXES = (
    "applytojob.com",
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
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
        "career-opportunities",
        "careers",
        "job",
        "job-detail",
        "job-details",
        "job-openings",
        "jobs",
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
_SOURCE_CODE_HOSTS = frozenset({"bitbucket.org", "github.com", "gitlab.com"})
_NON_JOB_HOSTS = frozenset({"calendly.com", "reddit.com"})
_ROLE_SIGNAL = re.compile(
    r"\b(?:intern(?:ship)?|engineer|developer|designer|analyst|manager|specialist|"
    r"associate|coordinator|scientist|architect|administrator|recruiter|director|fellow)\b",
    re.IGNORECASE,
)
_STRUCTURED_JOB = re.compile(
    r'["\']@type["\']\s*:\s*(?:["\']JobPosting["\']|\[[^]]*["\']JobPosting["\'])',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobSourceAssessment:
    accepted: bool
    reason_code: str
    explanation: str


def _host_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _recognized_job_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").casefold()
    path = unquote(parsed.path).casefold()
    if any(_host_matches(host, suffix) for suffix in _ATS_HOST_SUFFIXES):
        return True
    if _JOB_HOST_LABELS.intersection(host.split(".")):
        return True
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return path.startswith("/jobs/")
    if host == "indeed.com" or host.endswith(".indeed.com"):
        return path.startswith("/jobs/") or path.startswith("/viewjob")
    segments = {part for part in path.split("/") if part}
    if _JOB_PATH_SEGMENTS.intersection(segments):
        return True
    query_keys = {key.casefold() for key in parse_qs(parsed.query, keep_blank_values=True)}
    return bool(_JOB_QUERY_KEYS.intersection(query_keys))


def _obvious_non_job_reason(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").rstrip(".").casefold()
    path = unquote(parsed.path).casefold().rstrip("/")
    if path.endswith(".git"):
        return (
            "source_code_repository",
            "The supplied URL points to a Git repository, not a job posting.",
        )
    if any(_host_matches(host, source_host) for source_host in _SOURCE_CODE_HOSTS):
        if not _recognized_job_url(url):
            return (
                "source_code_repository",
                "The supplied URL points to a source-code hosting page, not a job posting.",
            )
    if any(_host_matches(host, non_job_host) for non_job_host in _NON_JOB_HOSTS):
        return ("non_job_host", "The supplied URL is hosted on a community or scheduling site.")
    if host.startswith("docs.") or "/docs/" in f"{path}/":
        return (
            "documentation_page",
            "The supplied URL points to documentation, not a job posting.",
        )
    return None


def obvious_non_job_source(url: str) -> JobSourceAssessment | None:
    """Return a deterministic rejection for URLs that cannot be job postings."""
    rejected = _obvious_non_job_reason(url)
    if rejected is None:
        return None
    return JobSourceAssessment(False, rejected[0], rejected[1])


def assess_job_source(
    *,
    url: str,
    snapshot: str,
    research: JobResearch,
) -> JobSourceAssessment:
    """Reject non-job pages before Erga creates records or résumé packages."""
    rejected = obvious_non_job_source(url)
    if rejected is not None:
        return rejected
    if _recognized_job_url(url):
        return JobSourceAssessment(
            True,
            "recognized_job_url",
            "The URL uses a recognized ATS, careers host, or job-posting route.",
        )
    if _STRUCTURED_JOB.search(snapshot):
        return JobSourceAssessment(
            True,
            "structured_job_posting",
            "The page contains structured JobPosting metadata.",
        )
    has_role = research.role != "Job Opportunity" and bool(_ROLE_SIGNAL.search(research.role))
    fact_groups = sum(
        bool(group)
        for group in (
            research.responsibilities,
            research.qualifications,
            research.logistics,
            research.application_constraints,
        )
    )
    snapshot_text = " ".join(snapshot.casefold().split())
    has_application_language = "apply" in snapshot_text and any(
        marker in snapshot_text
        for marker in ("responsibilities", "requirements", "qualifications", "job description")
    )
    if has_role and (fact_groups >= 2 or (fact_groups >= 1 and has_application_language)):
        return JobSourceAssessment(
            True,
            "content_verified_job_posting",
            "The generic page contains a role title and multiple job-posting fact groups.",
        )
    return JobSourceAssessment(
        False,
        "insufficient_job_evidence",
        "The page does not contain enough source-grounded job facts to create an application.",
    )


def require_job_source(*, url: str, snapshot: str, research: JobResearch) -> None:
    assessment = assess_job_source(url=url, snapshot=snapshot, research=research)
    if not assessment.accepted:
        raise ValueError(
            f"job intake rejected ({assessment.reason_code}): {assessment.explanation} "
            "Paste the canonical ATS or company-careers posting URL."
        )
