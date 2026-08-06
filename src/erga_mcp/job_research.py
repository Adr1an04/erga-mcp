from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

_CYCLE = re.compile(r"\b(Spring|Summer|Fall|Winter)\s+(20\d{2})\b", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_SECONDARY_START = "<!-- erga-mcp:secondary-research:start -->"
_SECONDARY_END = "<!-- erga-mcp:secondary-research:end -->"
_IGNORED_HTML = frozenset(
    {"aside", "footer", "header", "nav", "noscript", "script", "style", "svg", "template"}
)
_BLOCK_HTML = frozenset(
    {"article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "main", "p", "section"}
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
_NON_JOB_HOST_SUFFIXES = (
    "calendly.com",
    "github.com",
    "modelcontextprotocol.io",
    "playwright.dev",
    "reddit.com",
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
_JOB_ROLE_SIGNAL = re.compile(
    r"\b(?:intern(?:ship)?|engineer|developer|designer|analyst|manager|specialist|"
    r"associate|coordinator|scientist|architect|administrator|recruiter|director|"
    r"owner|writer|counsel|technician|representative|fellow|trader)\b",
    re.IGNORECASE,
)
_JOB_CONTENT_SIGNALS = (
    re.compile(
        r"\b(?:job description|responsibilities|what you(?:'|’)ll do|you will\s*:)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:qualifications|requirements|what we(?:'|’)re looking for|you are\s*:)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:apply now|apply for this (?:job|role|position)|submit application)\b", re.IGNORECASE
    ),
)


@dataclass(frozen=True)
class JobResearch:
    company: str
    role: str
    cycles: tuple[str, ...]
    location: str | None
    employment_type: str | None
    compensation: str | None
    date_posted: str | None
    highlights: tuple[str, ...]
    responsibilities: tuple[str, ...]
    qualifications: tuple[str, ...]
    skills: tuple[str, ...]
    logistics: tuple[str, ...]
    ambiguities: tuple[str, ...]
    application_constraints: tuple[str, ...]
    source_url: str


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", unescape(_TAG.sub(" ", value))).strip()


class _VisibleJobTextParser(HTMLParser):
    """Collect visible page text while excluding scripts, navigation, and page chrome."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._main_depth = 0
        self.saw_main = False
        self.visible: list[str] = []
        self.main: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized in _IGNORED_HTML:
            self._ignored_depth += 1
            return
        if self._ignored_depth > 0:
            return
        if normalized == "main":
            self.saw_main = True
            self._main_depth += 1
        if normalized in _BLOCK_HTML:
            self._append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "br" and self._ignored_depth == 0:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in _IGNORED_HTML and self._ignored_depth > 0:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth > 0:
            return
        if normalized in _BLOCK_HTML:
            self._append("\n")
        if normalized == "main":
            self._main_depth = max(0, self._main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._append(data)

    def _append(self, value: str) -> None:
        self.visible.append(value)
        if self._main_depth > 0:
            self.main.append(value)


def _visible_job_text(snapshot: str) -> str:
    parser = _VisibleJobTextParser()
    parser.feed(snapshot)
    selected = parser.main if parser.saw_main and "".join(parser.main).strip() else parser.visible
    lines = [_SPACE.sub(" ", line).strip() for line in "".join(selected).splitlines()]
    return "\n".join(line for line in lines if line)


def _find_job_posting(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        posting_type = value.get("@type")
        if posting_type == "JobPosting" or (
            isinstance(posting_type, list) and "JobPosting" in posting_type
        ):
            return value
        for child in value.values():
            found = _find_job_posting(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_job_posting(child)
            if found is not None:
                return found
    return None


def _find_greenhouse_posting(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if (
            value.get("post_type") == "job_post"
            and isinstance(value.get("title"), str)
            and isinstance(value.get("content"), str)
        ):
            return value
        for child in value.values():
            found = _find_greenhouse_posting(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_greenhouse_posting(child)
            if found is not None:
                return found
    return None


def _embedded_object(
    snapshot: str,
    marker_pattern: str,
    finder: Any,
) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for marker in re.finditer(marker_pattern, snapshot):
        starts = [match.start() for match in re.finditer(r"\{", snapshot[: marker.start()])]
        for start in reversed(starts[-128:]):
            try:
                value, _ = decoder.raw_decode(snapshot[start:])
            except json.JSONDecodeError:
                continue
            found = finder(value)
            if found is not None:
                return found
    return None


def _structured_job_posting(snapshot: str) -> dict[str, Any] | None:
    """Recover schema.org JobPosting JSON embedded in an untrusted text snapshot."""
    return _embedded_object(snapshot, r'"@type"\s*:\s*"JobPosting"', _find_job_posting)


def _greenhouse_job_posting(snapshot: str) -> dict[str, Any] | None:
    """Recover Greenhouse's server-rendered jobPost object without executing page code."""
    return _embedded_object(
        snapshot,
        r'"post_type"\s*:\s*"job_post"',
        _find_greenhouse_posting,
    )


def _hostname_matches(hostname: str, suffixes: tuple[str, ...]) -> bool:
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


def _is_non_job_host(job_url: str) -> bool:
    return _hostname_matches((urlsplit(job_url).hostname or "").casefold(), _NON_JOB_HOST_SUFFIXES)


def _is_specific_ats_url(job_url: str) -> bool:
    parsed = urlsplit(job_url)
    hostname = (parsed.hostname or "").casefold()
    path_parts = [part.casefold() for part in parsed.path.split("/") if part]
    return len(path_parts) >= 2 and _hostname_matches(hostname, _JOB_HOST_SUFFIXES)


def _has_career_host_and_detail_path(job_url: str) -> bool:
    parsed = urlsplit(job_url)
    hostname_parts = set((parsed.hostname or "").casefold().split("."))
    path_parts = {part.casefold() for part in parsed.path.split("/") if part}
    return bool(hostname_parts & _JOB_HOST_LABELS) and bool(path_parts & _JOB_PATH_SEGMENTS)


def _is_shopify_career_detail_url(job_url: str) -> bool:
    parsed = urlsplit(job_url)
    hostname = (parsed.hostname or "").casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    return (
        (hostname == "shopify.com" or hostname.endswith(".shopify.com"))
        and len(path_parts) >= 2
        and path_parts[0].casefold() == "careers"
    )


def _credible_structured_job_posting(snapshot: str) -> bool:
    posting = _structured_job_posting(snapshot)
    if posting is None:
        return False
    title = posting.get("title")
    description = posting.get("description")
    organization = posting.get("hiringOrganization")
    has_organization = isinstance(organization, dict) and bool(
        str(organization.get("name", "")).strip()
    )
    has_identifier = bool(str(posting.get("identifier", "")).strip())
    return bool(str(title or "").strip() and str(description or "").strip()) and (
        has_organization or has_identifier
    )


def require_job_posting(snapshot: str, *, job_url: str) -> None:
    """Reject generic public pages before intake can create an application package."""
    if _is_non_job_host(job_url):
        raise ValueError(
            "page does not contain enough evidence of a specific job posting; "
            "use scrape_public_page for non-job research links"
        )
    if _credible_structured_job_posting(snapshot) or _greenhouse_job_posting(snapshot) is not None:
        return
    if _is_specific_ats_url(job_url):
        return
    content = official_job_text(snapshot)
    has_role = _JOB_ROLE_SIGNAL.search(content) is not None
    if (
        _is_shopify_career_detail_url(job_url)
        and has_role
        and all(
            marker in content
            for marker in ("Being a Shopify Intern", "Qualifications:", "Compensation:")
        )
    ):
        return
    has_all_posting_sections = all(
        signal.search(content) is not None for signal in _JOB_CONTENT_SIGNALS
    )
    if _has_career_host_and_detail_path(job_url) and has_role and has_all_posting_sections:
        return
    raise ValueError(
        "page does not contain enough evidence of a specific job posting; "
        "use scrape_public_page for non-job research links"
    )


def build_job_snapshot(page: str) -> str:
    """Create a stable snapshot from visible text plus bounded structured job metadata."""
    visible = " ".join(_visible_job_text(page).split())
    structured: list[str] = []
    posting = _structured_job_posting(page)
    if posting is not None:
        structured.append(json.dumps(posting, ensure_ascii=False, sort_keys=True))
    greenhouse = _greenhouse_job_posting(page)
    if greenhouse is not None:
        structured.append(json.dumps(greenhouse, ensure_ascii=False, sort_keys=True))
    snapshot = "\n\n".join(part for part in (visible, *structured) if part.strip())
    return snapshot.strip()


def official_job_text(snapshot: str) -> str:
    """Return official posting content without executable/page-chrome text."""
    posting = _structured_job_posting(snapshot)
    if posting is not None:
        description = posting.get("description")
        if isinstance(description, str) and _clean_text(description):
            return _visible_job_text(description)
    greenhouse = _greenhouse_job_posting(snapshot)
    if greenhouse is not None:
        content = greenhouse.get("content")
        if isinstance(content, str) and _clean_text(content):
            return _visible_job_text(content)
    return _visible_job_text(snapshot)


def _cycles(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for season, year in _CYCLE.findall(text):
        cycle = f"{season.title()} {year}"
        if cycle not in found:
            found.append(cycle)
    return tuple(found)


def _fallback_company(job_url: str) -> str:
    parsed = urlsplit(job_url)
    hostname = (parsed.hostname or "").casefold()
    path_parts = [part for part in parsed.path.split("/") if part]
    if (
        hostname
        in {
            "job-boards.greenhouse.io",
            "jobs.ashbyhq.com",
            "jobs.lever.co",
        }
        and path_parts
    ):
        return re.sub(r"[-_]", " ", path_parts[0]).title()
    labels = hostname.split(".")
    generic = {
        "apply",
        "boards",
        "careers",
        "greenhouse",
        "io",
        "job-boards",
        "jobs",
        "www",
    }
    label = next((part for part in labels if part not in generic), "Company")
    return re.sub(r"[-_]", " ", label).title()


def _fallback_title_and_company(snapshot: str) -> tuple[str, str]:
    match = re.match(
        r"\s*Job Application for (.+?) at (.+?) Back to jobs\b",
        snapshot,
        re.IGNORECASE,
    )
    if match is not None:
        return _clean_text(match.group(1)), _clean_text(match.group(2))
    cohort_heading = re.match(
        r"\s*(\[(?:Spring|Summer|Fall|Winter)\s+20\d{2}\]\s+"
        r".{1,100}?\b(?:Intern(?:ship)?|Co-?op))\b",
        snapshot,
        re.IGNORECASE,
    )
    if cohort_heading is not None:
        return _clean_text(cohort_heading.group(1)), ""
    prefix = snapshot.split(" {", 1)[0].strip()
    if " @ " in prefix:
        title, company = prefix.rsplit(" @ ", 1)
        return _clean_text(title), _clean_text(company)
    return "", ""


def _display_role(title: str) -> str:
    without_cohorts = re.sub(
        r"^\s*\[(?:Spring|Summer|Fall|Winter)\s+20\d{2}\]\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    without_cohorts = re.sub(
        r"\s*\([^)]*(?:Spring|Summer|Fall|Winter)\s+20\d{2}[^)]*\)",
        "",
        without_cohorts,
        flags=re.IGNORECASE,
    )
    return _SPACE.sub(" ", without_cohorts).strip(" -–—") or "Job Opportunity"


def _location(posting: dict[str, Any]) -> str | None:
    remote = str(posting.get("jobLocationType", "")).casefold() == "telecommute"
    requirement = posting.get("applicantLocationRequirements")
    if isinstance(requirement, list):
        requirement = requirement[0] if requirement else None
    country = _clean_text(requirement.get("name")) if isinstance(requirement, dict) else ""
    if remote:
        return f"Remote — {country}" if country else "Remote"

    plain_location = _clean_text(posting.get("_plain_location"))
    if plain_location:
        return plain_location
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    address = location.get("address") if isinstance(location, dict) else None
    if not isinstance(address, dict):
        return None
    parts = [
        _clean_text(address.get(key))
        for key in ("addressLocality", "addressRegion", "addressCountry")
    ]
    rendered = ", ".join(part for part in parts if part)
    return rendered or None


def _employment_type(posting: dict[str, Any], title: str) -> str | None:
    raw = posting.get("employmentType")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    value = _clean_text(raw)
    if value and value.casefold() != "hidden":
        return value.replace("_", " ").title()
    if re.search(r"\bintern(?:ship)?\b", title, re.IGNORECASE):
        return "Internship"
    return None


def _number(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return f"{value:g}"


def _compensation(posting: dict[str, Any]) -> str | None:
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    value = salary.get("value")
    if not isinstance(value, dict):
        return None
    minimum = _number(value.get("minValue"))
    maximum = _number(value.get("maxValue"))
    if minimum is None and maximum is None:
        return None
    currency = _clean_text(salary.get("currency")).upper()
    symbol = "$" if currency == "USD" else f"{currency} " if currency else ""
    amount = (
        f"{symbol}{minimum}–{symbol}{maximum}"
        if minimum is not None and maximum is not None
        else f"{symbol}{minimum or maximum}"
    )
    unit = _clean_text(value.get("unitText")).casefold()
    unit_labels = {"hour": "hour", "day": "day", "week": "week", "month": "month", "year": "year"}
    return f"{amount}/{unit_labels.get(unit, unit)}" if unit else amount


def _compensation_from_text(text: str) -> str | None:
    match = re.search(
        r"\$\s*(\d+(?:\.\d+)?)\s*(?:to|[-–—])\s*\$?\s*(\d+(?:\.\d+)?)"
        r"\s*(?:per|/)\s*(hour|year|month|week|day)\b",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return f"${match.group(1)}–${match.group(2)}/{match.group(3).casefold()}"


_HIGHLIGHT_RULES = (
    (
        r"\bend[ -]to[ -]end\b|\bfrom design through .*?(?:staging|production)\b|"
        r"\breal ownership\b",
        "The role expects ownership of visible work from implementation through delivery.",
    ),
    (
        r"\bproduction (?:codebase|code)\b|\blanding merged pr|\bmanage deployments\b",
        "Production delivery, deployment, and operational reliability are part of the work.",
    ),
    (
        r"\b(?:speech|audio)\b|\basr\b|\btts\b|\bvoice ai\b",
        "The work touches voice/audio AI, including speech systems such as ASR or TTS.",
    ),
    (
        r"\breal[- ]time systems?\b|\blow latency\b",
        "The posting emphasizes real-time or latency-sensitive systems.",
    ),
    (
        r"\b(?:agentic tooling|claude code|codex)\b|\bai-assisted workflow\b|\bai tools daily\b",
        "AI-assisted tools are expected in the normal development or content workflow.",
    ),
    (
        r"\bfirst principles\b|\bdig into why\b",
        "First-principles debugging and clear reasoning about failures are explicit signals.",
    ),
    (
        r"\b(?:projects|tools|scripts|automations)\b|\bbuilt and shipped\b|\bportfolio\b",
        "A portfolio of independent projects or shipped work is valued evidence.",
    ),
    (
        r"\bpage speed\b|\bmobile responsiveness\b|\bon-page seo\b|\bstructured data\b",
        "Web performance, accessibility across devices, structured data, and SEO are "
        "core outcomes.",
    ),
    (
        r"\blanding page copy\b|\blong-form content\b|\bbrand voice\b",
        "The role combines implementation with clear, brand-aligned technical marketing writing.",
    ),
    (
        r"\bn8n\b|\bzapier\b|\bmake\b.*\bautomation",
        "Lightweight content and publishing automation is an explicit responsibility.",
    ),
    (
        r"\bconversion optimization\b|\banalytics and reporting\b|\bqualified leads\b",
        "The team expects evidence-based conversion and analytics work, not only page creation.",
    ),
)


def _highlights(description: str) -> tuple[str, ...]:
    return tuple(
        summary for pattern, summary in _HIGHLIGHT_RULES if re.search(pattern, description, re.I)
    )


def _content_lines(content: str) -> list[str]:
    rendered = re.sub(r"<li\b[^>]*>", "\n- ", content, flags=re.IGNORECASE)
    rendered = re.sub(
        r"</(?:li|p|h[1-6]|ul|ol)>",
        "\n",
        rendered,
        flags=re.IGNORECASE,
    )
    rendered = unescape(_TAG.sub(" ", rendered))
    return [_SPACE.sub(" ", line).strip() for line in rendered.splitlines() if line.strip()]


def _section_items(
    lines: list[str],
    *,
    starts: tuple[str, ...],
    ends: tuple[str, ...],
) -> tuple[str, ...]:
    active = False
    items: list[str] = []
    for line in lines:
        is_item = line.startswith("- ")
        label = line.lstrip("- ").strip().casefold()
        if not active and any(label == start.casefold() for start in starts):
            active = True
            continue
        if active and (
            any(label == end.casefold() for end in ends)
            or (not is_item and label.startswith("about "))
        ):
            break
        if active and is_item:
            item = line[2:].strip()
            if item and item not in items:
                items.append(item)
    return tuple(items)


_SKILL_PATTERNS = (
    (r"(?<!\w)c\+\+(?!\w)|\bcpp\b", "C++"),
    (r"\bgolang\b|\bgo programming\b|\bgo language\b", "Go"),
    (r"\bpython\b", "Python"),
    (r"\bjava\b", "Java"),
    (r"\btypescript\b", "TypeScript"),
    (r"\bhtml\b", "HTML"),
    (r"\bcss\b", "CSS"),
    (r"\bjavascript\b", "JavaScript"),
    (r"\breact\b", "React"),
    (r"\bnext\.js\b", "Next.js"),
    (r"\bwordpress\b", "WordPress"),
    (r"\bwebflow\b", "Webflow"),
    (r"\bseo\b", "SEO"),
    (r"\bstructured data\b", "structured data"),
    (r"\banalytics\b", "analytics"),
    (r"\bn8n\b", "n8n"),
    (r"\bzapier\b", "Zapier"),
    (r"(?:\bn8n\b|\bzapier\b).{0,40}\bmake\b", "Make"),
    (r"\brust\b", "Rust"),
    (r"\blinux\b", "Linux"),
    (r"\baws\b|\bamazon web services\b", "AWS"),
    (r"\bazure\b", "Azure"),
    (r"\bgcp\b|\bgoogle cloud\b", "GCP"),
    (r"\bdocker\b", "Docker"),
    (r"\bkubernetes\b", "Kubernetes"),
    (r"\bredis\b", "Redis"),
    (r"\bpostgres(?:ql)?\b", "PostgreSQL"),
    (r"\bsql\b", "SQL"),
    (r"\basr\b", "ASR"),
    (r"\btts\b", "TTS"),
    (r"\bvoice ai\b", "Voice AI"),
)


def _skills(description: str) -> tuple[str, ...]:
    return tuple(
        label for pattern, label in _SKILL_PATTERNS if re.search(pattern, description, re.I)
    )


def _application_constraints(snapshot: str, description: str) -> tuple[str, ...]:
    constraints: list[str] = []
    for match in re.finditer(r'"applicationLimitCalloutHtml"\s*:\s*("(?:\\.|[^"\\])*")', snapshot):
        try:
            value = _clean_text(json.loads(match.group(1)))
        except (json.JSONDecodeError, TypeError):
            continue
        if value and value not in constraints:
            constraints.append(value)

    form = re.search(r"https://forms\.gle/[A-Za-z0-9_-]+", description)
    if form is None:
        form = re.search(r"https://forms\.gle/[A-Za-z0-9_-]+", snapshot)
    if form is not None and re.search(
        r"(?:complete|fill|submit).{0,80}(?:application|form)|"
        r"(?:application|form).{0,80}(?:complete|fill|submit)",
        description,
        re.IGNORECASE,
    ):
        constraints.append(
            "The posting says a separate Google Form must also be completed for the "
            f"application to be considered: {form.group(0)}"
        )
    return tuple(dict.fromkeys(constraints))


def _ambiguities(description: str) -> tuple[str, ...]:
    office_days = {
        int(value)
        for value in re.findall(
            r"\b(\d+)\s+days?\s+per\s+week(?:\s+in\s+(?:the\s+)?office|\s+in\s+person)?",
            description,
            re.IGNORECASE,
        )
    }
    if len(office_days) > 1:
        rendered = " and ".join(str(value) for value in sorted(office_days))
        return (
            "The posting gives conflicting in-office expectations "
            f"({rendered} days per week); verify the current requirement before applying.",
        )
    return ()


def analyze_job_snapshot(snapshot: str, *, job_url: str) -> JobResearch:
    """Extract source-grounded role facts without inventing missing fields."""
    posting = _structured_job_posting(snapshot)
    greenhouse = _greenhouse_job_posting(snapshot)
    if posting is None and greenhouse is not None:
        posting = {
            "title": greenhouse.get("title"),
            "description": greenhouse.get("content"),
            "datePosted": greenhouse.get("published_at"),
            "employmentType": greenhouse.get("employment"),
            "hiringOrganization": {"name": greenhouse.get("company_name")},
            "_plain_location": greenhouse.get("job_post_location"),
        }
    posting = posting or {}

    fallback_title, fallback_company = _fallback_title_and_company(snapshot)
    title = _clean_text(posting.get("title")) or fallback_title or "Job Opportunity"
    organization = posting.get("hiringOrganization")
    company = _clean_text(organization.get("name")) if isinstance(organization, dict) else ""
    company = company or fallback_company or _fallback_company(job_url)
    description_value = posting.get("description")
    raw_description = (
        description_value
        if isinstance(description_value, str) and _clean_text(description_value)
        else official_job_text(snapshot)
    )
    description = _clean_text(raw_description)
    content_lines = _content_lines(raw_description)
    responsibilities = _section_items(
        content_lines,
        starts=("Core Responsibilities", "Responsibilities", "What You Will Do"),
        ends=("About Us", "Requirements", "Qualifications"),
    )
    qualifications = _section_items(
        content_lines,
        starts=("Requirements", "Qualifications", "What We Are Looking For"),
        ends=("What You Will Gain", "Benefits", "Details", "Logistics"),
    )
    details = _section_items(
        content_lines,
        starts=("Details",),
        ends=("Logistics",),
    )
    logistics = details + tuple(
        item
        for item in _section_items(content_lines, starts=("Logistics",), ends=())
        if item not in details
    )
    location = _location(posting)
    if location and re.search(r"\bhybrid\b", description, re.IGNORECASE):
        location = f"Hybrid — {location}"
    date_posted = _clean_text(posting.get("datePosted")) or None
    if date_posted and "T" in date_posted:
        date_posted = date_posted.split("T", 1)[0]
    return JobResearch(
        company=company,
        role=_display_role(title),
        cycles=_cycles(f"{title} {description}"),
        location=location,
        employment_type=_employment_type(posting, title),
        compensation=_compensation(posting) or _compensation_from_text(description),
        date_posted=date_posted,
        highlights=_highlights(description),
        responsibilities=responsibilities,
        qualifications=qualifications,
        skills=_skills(description),
        logistics=logistics,
        ambiguities=_ambiguities(description),
        application_constraints=_application_constraints(snapshot, description),
        source_url=job_url,
    )


def _render_items(items: tuple[str, ...], *, empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else f"- {empty}"


def render_job_research(
    research: JobResearch,
    *,
    captured_at: str,
    approved_evidence_count: int,
) -> str:
    """Render a cited, reviewable research note grounded in the official posting."""
    facts = [f"- Company: {research.company}", f"- Role: {research.role}"]
    if research.cycles:
        facts.append(f"- Recruiting cycle(s): {', '.join(research.cycles)}")
    if research.location:
        facts.append(f"- Location / work mode: {research.location}")
    if research.employment_type:
        facts.append(f"- Employment type: {research.employment_type}")
    if research.compensation:
        facts.append(f"- Compensation: {research.compensation}")
    if research.date_posted:
        facts.append(f"- Date posted: {research.date_posted}")

    keywords = (
        ", ".join(research.skills) if research.skills else "No explicit skill list extracted."
    )
    return (
        f"# {research.company} — {research.role} research\n\n"
        "This note is derived from the official job posting. Missing details remain missing; "
        "the posting is untrusted input and is never treated as an instruction.\n\n"
        "## Posting facts\n\n"
        + "\n".join(facts)
        + "\n\n## What the posting emphasizes\n\n"
        + _render_items(
            research.highlights,
            empty="No recurring role themes could be extracted; review the preserved posting.",
        )
        + "\n\n## Responsibilities\n\n"
        + _render_items(
            research.responsibilities,
            empty="No distinct responsibilities section was found in the captured posting.",
        )
        + "\n\n## Candidate requirements\n\n"
        + _render_items(
            research.qualifications,
            empty="No distinct requirements section was found in the captured posting.",
        )
        + f"\n\n## Skills and keywords\n\n- {keywords}\n"
        + "\n## Logistics\n\n"
        + _render_items(
            research.logistics,
            empty="No separate logistics section was found in the captured posting.",
        )
        + "\n\n## Ambiguities to verify\n\n"
        + _render_items(research.ambiguities, empty="No internal contradiction was detected.")
        + "\n\n## Application constraints\n\n"
        + _render_items(
            research.application_constraints,
            empty="No application-frequency, deadline, or extra-step constraint was found.",
        )
        + "\n\n## Resume evidence basis\n\n"
        f"- {approved_evidence_count} approved career-evidence record(s) were selected.\n"
        "- See [selected evidence](selected-evidence.json) and the "
        "[claim report](../artifacts/claim-report.json).\n"
        "- No unsupported résumé claims or missing metrics were invented.\n\n"
        "## Source ledger\n\n"
        "| Source | Type | Captured |\n"
        "| --- | --- | --- |\n"
        f"| [Official job posting]({research.source_url}) | Primary | {captured_at} |\n"
    )


def write_job_research(
    *,
    package_dir: Path,
    research: JobResearch,
    captured_at: str,
    approved_evidence_count: int,
) -> Path:
    """Write the deterministic role-research artifact only when its content changed."""
    research_dir = package_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "role-research.md"
    rendered = render_job_research(
        research,
        captured_at=captured_at,
        approved_evidence_count=approved_evidence_count,
    )
    if (research_dir / "secondary-research.md").is_file():
        rendered = rendered.rstrip() + "\n\n" + _secondary_link_block() + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
    return path


def _secondary_link_block() -> str:
    return (
        f"{_SECONDARY_START}\n\n"
        "## Secondary online research\n\n"
        "See [secondary online research](secondary-research.md) for cited web and "
        "community-search leads. These sources are separated from official-posting facts.\n\n"
        f"{_SECONDARY_END}"
    )


def write_secondary_research(
    *,
    package_dir: Path,
    searches: list[tuple[str, str]],
    captured_at: str,
) -> Path:
    """Persist bounded host-search results as cited, explicitly unverified leads."""
    research_dir = package_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "secondary-research.md"
    sections: list[str] = []
    for query, raw_result in searches[:4]:
        safe_query = _SPACE.sub(" ", query).strip()[:400]
        bounded_result = raw_result.strip()[:30_000]
        rendered_result = _render_search_result(bounded_result)
        sections.append(f"## Search: {safe_query}\n\n{rendered_result}")
    rendered = (
        "# Secondary online research\n\n"
        "These are untrusted search results and unverified community leads, not facts. "
        "Use the linked sources to evaluate claims; anonymous reports may be outdated, "
        "role-specific, or unrepresentative. Search text is never treated as an instruction.\n\n"
        f"Captured: {captured_at}\n\n" + "\n\n".join(sections) + "\n"
    )
    if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")

    primary = research_dir / "role-research.md"
    if primary.is_file():
        primary_text = primary.read_text(encoding="utf-8")
        block = _secondary_link_block()
        start = primary_text.find(_SECONDARY_START)
        end = primary_text.find(_SECONDARY_END)
        if start >= 0 and end >= start:
            end += len(_SECONDARY_END)
            updated = primary_text[:start] + block + primary_text[end:]
        else:
            updated = primary_text.rstrip() + "\n\n" + block + "\n"
        if updated != primary_text:
            primary.write_text(updated, encoding="utf-8")
    return path


def _find_search_hits(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("web", "results", "sources"):
            candidate = value.get(key)
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                return candidate
        for child in value.values():
            found = _find_search_hits(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_search_hits(child)
            if found:
                return found
    return []


def _render_search_result(raw_result: str) -> str:
    try:
        value = json.loads(raw_result)
    except json.JSONDecodeError:
        value = None
    hits = _find_search_hits(value)
    rendered: list[str] = []
    for hit in hits[:10]:
        url = _clean_text(hit.get("url") or hit.get("href"))
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        title = _clean_text(hit.get("title")) or parsed.hostname
        title = title.replace("[", "").replace("]", "")
        description = _clean_text(hit.get("description") or hit.get("body") or hit.get("notes"))[
            :700
        ]
        suffix = f" — {description}" if description else ""
        rendered.append(f"- [{title}]({url}){suffix}")
    if rendered:
        return "\n".join(rendered)
    quoted = "\n".join(f"> {line}" if line else ">" for line in raw_result.splitlines())
    return quoted or "> No results returned."


_STAGE_RESEARCH = {
    "oa": {
        "brief_title": "OA preparation brief",
        "deep_title": "OA research dossier",
        "focus": (
            "Confirm the assessment platform, duration, deadline, accommodations, and whether the "
            "assessment is role-specific. Prepare from legitimate themes and skills—not leaked "
            "questions, answer keys, or other restricted assessment content."
        ),
        "queries": (
            '"{company}" "{role}" online assessment',
            'site:reddit.com "{company}" OA "{role}"',
            'site:reddit.com "{company}" CodeSignal OR HackerRank OR assessment',
        ),
        "sections": (
            "## Assessment format and logistics",
            "## Repeated community patterns (unverified)",
            "## Preparation plan",
            "## What not to overfit to",
        ),
    },
    "interview": {
        "brief_title": "Interview preparation brief",
        "deep_title": "Interview dossier",
        "focus": (
            "Review the official role requirements, business context, likely process signals, and "
            "the concrete experiences to prepare. Community reports can suggest themes, but do not "
            "treat any single report as a prediction."
        ),
        "queries": (
            '"{company}" "{role}" interview process',
            'site:reddit.com "{company}" interview "{role}"',
            'site:reddit.com "{company}" interview loop recruiter screen',
        ),
        "sections": (
            "## Company and role context",
            "## Process and evaluation signals (unverified)",
            "## Technical and behavioral preparation",
            "## Questions to ask the team",
        ),
    },
    "offer": {
        "brief_title": "Offer evaluation brief",
        "deep_title": "Offer evaluation dossier",
        "focus": (
            "Gather the facts needed to compare total compensation, equity mechanics, benefits, "
            "team conditions, and business risk. Community discussion is context only; "
            "verify terms with the written offer and the employer."
        ),
        "queries": (
            '"{company}" "{role}" compensation',
            'site:reddit.com "{company}" offer compensation benefits',
            '"{company}" funding revenue layoffs news',
        ),
        "sections": (
            "## Compensation and equity questions",
            "## Benefits, location, and work-policy questions",
            "## Company and team risk signals (unverified)",
            "## Negotiation and decision checklist",
        ),
    },
}


def _stage_research_config(stage: str) -> tuple[str, dict[str, Any]]:
    normalized = stage.strip().casefold()
    aliases = {"assessment": "oa", "online assessment": "oa", "interviews": "interview"}
    normalized = aliases.get(normalized, normalized)
    config = _STAGE_RESEARCH.get(normalized)
    if config is None:
        allowed = ", ".join(_STAGE_RESEARCH)
        raise ValueError(f"research stage must be one of: {allowed}")
    return normalized, config


def _stage_research_subject(package_dir: Path) -> tuple[str, str]:
    role_research = package_dir / "research" / "role-research.md"
    if role_research.is_file():
        match = re.search(
            r"^#\s+(.+?)\s+—\s+(.+?)\s+research\s*$",
            role_research.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match is not None:
            return match.group(1).strip(), match.group(2).strip()
    return "company", "role"


def write_stage_research(
    *,
    package_dir: Path,
    stage: str,
    depth: str,
    captured_at: str,
    searches: Sequence[tuple[str, str]] = (),
) -> Path:
    """Write a fast stage brief or a cited deep dossier for an OA, interview, or offer."""
    normalized_stage, config = _stage_research_config(stage)
    normalized_depth = depth.strip().casefold()
    if normalized_depth not in {"brief", "deep"}:
        raise ValueError("research depth must be 'brief' or 'deep'")
    research_dir = package_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    role_research = research_dir / "role-research.md"
    role_link = (
        "[official role research](role-research.md)"
        if role_research.is_file()
        else "official role research"
    )
    title_key = f"{normalized_depth}_title"
    title = str(config[title_key])
    company, role = _stage_research_subject(package_dir)
    configured_queries = cast(tuple[str, ...], config["queries"])
    queries = tuple(query.format(company=company, role=role) for query in configured_queries)
    sections = cast(tuple[str, ...], config["sections"])
    query_list = "\n".join(f"- `{query}`" for query in queries)
    rendered = (
        f"# {title}\n\n"
        f"Stage: {normalized_stage.upper()} · Generated: {captured_at}\n\n"
        "This artifact is stage-gated: it is intended only after an application progresses to an "
        "OA, interview, or offer. It keeps official facts separate from unverified web and "
        "community reports.\n\n"
        "## Official grounding\n\n"
        f"- Start with the {role_link}; it remains the source of record for role requirements.\n"
        f"- Focus: {config['focus']}\n\n"
        "## Suggested research queries\n\n"
        f"{query_list}\n"
    )
    if normalized_depth == "brief":
        rendered += (
            "\n## Fast checklist\n\n"
            "- Confirm the current stage, deadline, recruiter instructions, and official job "
            "scope.\n"
            "- Run the suggested searches only if the result will affect preparation or a "
            "decision.\n"
            "- Upgrade to a Deep dossier when the user wants cited community and market context.\n"
        )
        filename = f"{normalized_stage}-brief.md"
    else:
        source_sections: list[str] = []
        for query, raw_result in searches[:8]:
            safe_query = _SPACE.sub(" ", query).strip()[:400]
            source_sections.append(
                f"### Search: {safe_query}\n\n{_render_search_result(raw_result.strip()[:30_000])}"
            )
        rendered += "\n\n".join(
            f"{section}\n\n"
            "- Synthesize only recurring, recent signals from the cited leads below. "
            "Mark conflicts and uncertainty rather than forcing a conclusion."
            for section in sections
        )
        rendered += (
            "\n\n## Cited web and community leads (unverified)\n\n"
            "These leads may be stale, role-specific, or unrepresentative. Verify material "
            "claims against official sources, direct recruiter answers, and the written offer "
            "where applicable.\n\n"
            + (
                "\n\n".join(source_sections)
                if source_sections
                else "- No host-provided searches recorded yet."
            )
            + "\n"
        )
        filename = f"{normalized_stage}-deep-research.md"
    path = research_dir / filename
    if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
        path.write_text(rendered, encoding="utf-8")
    return path
