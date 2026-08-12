from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .job_research import analyze_job_snapshot
from .job_source import require_job_source
from .models import Application
from .web_scraping import ScrapedPage, scrape_page

Search = Callable[[str], list[dict[str, str]]]
Scrape = Callable[..., ScrapedPage]

_ROLE_STOPWORDS = frozenset(
    {
        "and",
        "bs",
        "cohort",
        "fall",
        "job",
        "opportunity",
        "summer",
        "the",
        "winter",
        "with",
    }
)
_COMPANY_SUFFIXES = frozenset(
    {"co", "company", "corp", "corporation", "inc", "incorporated", "llc", "ltd"}
)
_LOW_QUALITY_HOSTS = (
    "glassdoor.com",
    "finalroundai.com",
    "interviewquery.com",
    "interviewsense.com",
    "jobgether.com",
    "kdnuggets.com",
    "leonstaff.com",
    "ophyai.com",
    "quizlet.com",
    "skillnation.in",
    "snubber.ai",
    "techprep.app",
)
_SOURCE_CODE_HOSTS = ("bitbucket.org", "gist.github.com", "github.com", "gitlab.com")
_UNSUPPORTED_SOCIAL_HOSTS = (
    "facebook.com",
    "instagram.com",
    "pinterest.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
)
_REPOSITORY_CONTENT_MARKERS = (
    " github repositories",
    " github repository",
    "github topics",
    "interview handbook",
    "awesome list",
)
_SOFTWARE_ROLE_MARKERS = (
    "backend",
    "code",
    "coding",
    "developer",
    "frontend",
    "full stack",
    "programming",
    "software",
    "swe",
)
_CONFLICTING_DISCIPLINES = (
    "chemical engineering",
    "civil engineering",
    "electrical engineering",
    "industrial engineering",
    "manufacturing engineering",
    "mechanical engineering",
    "production planning",
)
_TOPIC_MARKERS = {
    "algorithms": ("algorithm",),
    "data structures": ("data structure",),
    "dynamic programming": ("dynamic programming",),
    "graphs": (" graph", "graphs"),
    "system design": ("system design",),
    "debugging": ("debug",),
    "concurrency": ("concurren", "threading"),
    "networking": ("networking", "network protocol"),
    "coding communication": ("explain your approach", "technical decisions", "pseudocode"),
}


@dataclass(frozen=True)
class DiscoveryResearchResult:
    path: Path
    sources_scraped: int
    outreach_leads: int
    index_path: Path | None = None
    candidates_reviewed: int = 0
    sources_retained: int = 0
    sources_rejected: int = 0
    coverage: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SearchLane:
    key: str
    label: str
    query: str
    trust: str


@dataclass(frozen=True)
class _RankedSource:
    category: str
    label: str
    title: str
    url: str
    snippet: str
    score: int
    trust: str
    reasons: tuple[str, ...]
    year: int | None


def _search(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    seen: set[str] = set()
    backends = ("yahoo", "bing", "google") if "site:reddit.com" in query else ("yahoo", "bing")
    for backend in backends:
        try:
            results = DDGS().text(query, max_results=max_results, backend=backend)
        except DDGSException:
            continue
        for result in results:
            if not isinstance(result, Mapping):
                continue
            url = _public_url(str(result.get("href", "")))
            if url is None or _url_identity(url) in seen:
                continue
            seen.add(_url_identity(url))
            combined.append(
                {
                    "title": str(result.get("title", "")),
                    "href": url,
                    "body": str(result.get("body", "")),
                    "date": str(result.get("date", "")),
                }
            )
    return combined


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#.]+", unquote(value).casefold()))


def _company_tokens(company: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _normalize(company).split()
        if token not in _COMPANY_SUFFIXES and len(token) > 1
    )


def _role_tokens(role: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _normalize(role).split()
        if token not in _ROLE_STOPWORDS and not re.fullmatch(r"20\d{2}", token)
    )


def _entity_matches(result: Mapping[str, str], *, company: str) -> bool:
    tokens = _company_tokens(company)
    title = _normalize(result.get("title", ""))
    url = urlsplit(result.get("href", ""))
    host = (url.hostname or "").casefold()
    url_path = _normalize(f"{url.path} {url.query}")
    if tokens == ("github",):
        if host in {"github.com", "www.github.com", "gist.github.com"}:
            return False
        if title.startswith("github -"):
            return False
        return "github" in title.split() or "github" in url_path.split()
    entity_surface = f"{title} {url_path}"
    return bool(tokens) and all(token in entity_surface.split() for token in tokens)


def _is_relevant_community_result(result: Mapping[str, str], *, company: str) -> bool:
    text = _normalize(
        " ".join((result.get("title", ""), result.get("href", ""), result.get("body", "")))
    )
    return _entity_matches(result, company=company) and any(
        token in text for token in ("intern", "internship", "software", "engineering", "swe")
    )


def _is_relevant_technical_result(result: Mapping[str, str], *, company: str) -> bool:
    text = _normalize(
        " ".join((result.get("title", ""), result.get("href", ""), result.get("body", "")))
    )
    return _is_relevant_community_result(result, company=company) and any(
        topic in text
        for topic in ("technical interview", "algorithm", "data structure", "coding interview")
    )


def _is_concrete_technical_report(result: Mapping[str, str], *, company: str) -> bool:
    text = _normalize(
        " ".join((result.get("title", ""), result.get("href", ""), result.get("body", "")))
    )
    format_markers = ("one problem", "two problem", "follow up", "medium", "hard")
    topic_markers = (
        "graph",
        "dynamic programming",
        "binary search",
        "tree",
        "string",
        "hash",
        "dictionary",
        "recursion",
    )
    return (
        _is_relevant_technical_result(result, company=company)
        and any(marker in text for marker in format_markers)
        and any(marker in text for marker in topic_markers)
    )


def _public_url(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return value.strip()


def _same_host(url: str, expected_host: str) -> bool:
    hostname = (urlsplit(url).hostname or "").casefold()
    return hostname == expected_host or hostname.endswith(f".{expected_host}")


def _url_identity(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            (parsed.hostname or "").casefold(),
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def _line(value: str) -> str:
    return " ".join(value.split()).replace("[", "\\[").replace("]", "\\]")


def _excerpt(page: ScrapedPage) -> str:
    return " ".join(page.text.split())[:1_200]


def _source_year(result: Mapping[str, str]) -> int | None:
    years = [
        int(year)
        for year in re.findall(
            r"\b(20(?:1[8-9]|2\d))\b",
            f"{result.get('date', '')} {result.get('title', '')} {result.get('body', '')}",
        )
    ]
    return max(years, default=None)


def _company_owned_host(url: str, *, company: str, source_host: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold()
    if any(_same_host(url, suffix) for suffix in _SOURCE_CODE_HOSTS):
        return False
    if _same_host(url, source_host):
        return True
    compact_host = re.sub(r"[^a-z0-9]", "", host)
    return any(token in compact_host for token in _company_tokens(company) if len(token) >= 4)


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _rank_result(
    result: Mapping[str, str],
    *,
    lane: _SearchLane,
    company: str,
    role: str,
    source_host: str,
    captured_at: datetime,
) -> tuple[_RankedSource | None, str]:
    url = _public_url(result.get("href", ""))
    if url is None:
        return None, "not a public HTTP(S) source"
    title = result.get("title", "").strip()
    snippet = result.get("body", "").strip()
    text = _normalize(f"{title} {url} {snippet}")
    parsed_url = urlsplit(url)
    host = (parsed_url.hostname or "").casefold()
    if any(_same_host(url, suffix) for suffix in _SOURCE_CODE_HOSTS):
        return None, "source-code repository or hosting index is not employer research"
    if any(_same_host(url, suffix) for suffix in _UNSUPPORTED_SOCIAL_HOSTS):
        return None, "social-media result is not a sufficiently reviewable research source"
    if re.search(r"/page/\d+/?$", parsed_url.path.casefold()):
        return None, "paginated archive is too broad to support role research"
    entity_match = _entity_matches(result, company=company)
    owned = _company_owned_host(url, company=company, source_host=source_host)
    is_reddit = host == "reddit.com" or host.endswith(".reddit.com")
    if not entity_match and not owned:
        return None, "company appears only in the snippet or not at all"
    if _company_tokens(company) == ("github",) and _has_any(
        f" {text}", _REPOSITORY_CONTENT_MARKERS
    ):
        return None, "GitHub repository content is not evidence about GitHub the employer"
    if lane.key in {"official_role", "early_career", "engineering_context"} and not owned:
        return None, "official/program/engineering lane requires a company-owned source"
    role_surface = _normalize(f"{title} {snippet} {parsed_url.path}")
    target_is_software = _has_any(
        _normalize(role), ("software", "developer", "frontend", "backend")
    )
    process_lane = lane.key in {
        "interview_process",
        "assessment",
        "community",
        "technical_preparation",
    }
    if (
        target_is_software
        and process_lane
        and not _has_any(f" {role_surface}", _SOFTWARE_ROLE_MARKERS)
    ):
        return None, "candidate experience does not match the software role family"
    title_surface = _normalize(title)
    if target_is_software and _has_any(title_surface, _CONFLICTING_DISCIPLINES):
        return None, "source title is for a different engineering discipline"

    role_tokens = _role_tokens(role)
    role_match = any(token in text for token in role_tokens)
    lane_match = False
    if lane.key == "official_role":
        lane_match = role_match or _has_any(text, ("career", "job", "opening", "position"))
    elif lane.key == "early_career":
        lane_match = _has_any(
            text,
            ("intern", "student", "university", "early career", "early in profession", "new grad"),
        )
    elif lane.key == "engineering_context":
        lane_match = _has_any(text, ("engineering", "developer", "architecture", "technical"))
    elif lane.key == "interview_process":
        lane_match = _has_any(text, ("interview", "hiring process", "recruiting process"))
    elif lane.key == "assessment":
        lane_match = _has_any(
            text,
            ("online assessment", " oa ", "coding assessment", "hackerrank", "codesignal"),
        )
    elif lane.key == "community":
        lane_match = is_reddit and _has_any(
            text, ("interview", "assessment", " oa ", "intern", "internship")
        )
    elif lane.key == "technical_preparation":
        lane_match = _has_any(
            text,
            ("technical interview", "coding interview", "coding exercise", "algorithm"),
        )
    if not lane_match:
        return None, f"does not match the {lane.label.casefold()} intent"
    if lane.key == "community" and not is_reddit:
        return None, "community lane only accepts Reddit candidate discussions"

    score = 5 + (3 if owned else 0) + (2 if role_match else 0) + 2
    reasons = ["company identity matched title/URL", f"matched {lane.label.casefold()}"]
    if owned:
        reasons[0] = "company-owned or tracked official host"
    if role_match:
        reasons.append("role family matched")
    year = _source_year(result)
    if year is not None:
        age = captured_at.year - year
        if age <= 1:
            score += 1
            reasons.append(f"recent date signal ({year})")
        elif age >= 4:
            score -= 2
            reasons.append(f"stale date signal ({year})")
    if any(_same_host(url, suffix) for suffix in _LOW_QUALITY_HOSTS) or any(
        marker in host for marker in (".glassdoor.", ".jobgether.")
    ):
        score -= 3
        reasons.append("lower-confidence aggregator/SEO source")
    if score < 8:
        return None, "source-quality score below retention threshold"
    return (
        _RankedSource(
            category=lane.key,
            label=lane.label,
            title=title,
            url=url,
            snippet=snippet,
            score=score,
            trust="official" if owned else lane.trust,
            reasons=tuple(reasons),
            year=year,
        ),
        "",
    )


def _search_lanes(
    *, company: str, role: str, source_host: str, current_year: int
) -> tuple[_SearchLane, ...]:
    role_query = role if role != "Job Opportunity" else "software engineering internship"
    return (
        _SearchLane(
            "official_role",
            "Official role source",
            f'"{company}" "{role_query}" site:{source_host}',
            "official",
        ),
        _SearchLane(
            "early_career",
            "Early-career program source",
            f'"{company}" internship early career university program',
            "secondary",
        ),
        _SearchLane(
            "engineering_context",
            "Engineering context source",
            f'"{company}" engineering blog software engineering culture',
            "secondary",
        ),
        _SearchLane(
            "interview_process",
            "Interview-process source",
            f'"{company}" "{role_query}" interview process {current_year}',
            "secondary",
        ),
        _SearchLane(
            "interview_process",
            "Interview-process source",
            f'"{company}" official take-home technical interview engineering',
            "secondary",
        ),
        _SearchLane(
            "assessment",
            "Assessment-process source",
            f'"{company}" {role_query} online assessment OA {current_year}',
            "secondary",
        ),
        _SearchLane(
            "community",
            "Community candidate report (unverified)",
            f"site:reddit.com {company} {role_query} interview internship OA {current_year}",
            "community_unverified",
        ),
        _SearchLane(
            "community",
            "Community candidate report (unverified)",
            f"site:reddit.com/r/FAANGrecruiting {company} SWE interview {current_year}",
            "community_unverified",
        ),
        _SearchLane(
            "community",
            "Community candidate report (unverified)",
            f"site:reddit.com/r/csMajors {company} SWE internship assessment {current_year}",
            "community_unverified",
        ),
        _SearchLane(
            "technical_preparation",
            "Technical interview study source (unverified)",
            f'"{company}" "{role_query}" technical interview study coding algorithms '
            f"{current_year}",
            "secondary_unverified",
        ),
    )


def _render_source(*, source: _RankedSource, excerpt: str) -> str:
    rationale = "; ".join(source.reasons)
    recency = f" · date signal: {source.year}" if source.year is not None else " · date unknown"
    search_context = (
        f"  - Search-result context: {_line(source.snippet)}\n" if source.snippet else ""
    )
    return (
        f"- **{_line(source.label)}:** [{_line(source.title) or _line(source.url)}]({source.url})\n"
        f"  - Relevance: {rationale} · score {source.score}{recency}.\n"
        f"{search_context}"
        f"  - Untrusted bounded excerpt: {_line(excerpt) or 'No visible text extracted.'}\n"
    )


def _topic_signals(sources: list[tuple[_RankedSource, str]]) -> list[str]:
    signals: list[str] = []
    for topic, markers in _TOPIC_MARKERS.items():
        source = next(
            (
                item
                for item in sources
                if any(marker in f" {item[0].snippet} {item[1]}".casefold() for marker in markers)
            ),
            None,
        )
        if source is not None:
            signals.append(
                f"- {topic.title()} — supported by [{_line(source[0].title)}]({source[0].url})."
            )
    return signals


def discover_job_research(
    *,
    application: Application,
    package_dir: Path,
    search: Callable[..., list[dict[str, str]]] = _search,
    scrape: Scrape = scrape_page,
    captured_at: datetime | None = None,
) -> DiscoveryResearchResult:
    """Review broad public search coverage, retain relevant sources, and save citations."""
    captured_at = captured_at or datetime.now(UTC)
    source_host = (urlsplit(application.source_url).hostname or "").casefold()
    pre_scraped: dict[str, ScrapedPage] = {}
    try:
        posting_page = scrape(application.source_url, max_characters=3_500, max_links=8)
    except (OSError, ValueError):
        posting_page = None
    if posting_page is not None:
        pre_scraped[application.source_url] = posting_page
    source_snapshot = (
        f"{posting_page.title}\n{posting_page.text}"
        if posting_page is not None
        else application.role
    )
    source_research = analyze_job_snapshot(source_snapshot, job_url=application.source_url)
    require_job_source(
        url=application.source_url,
        snapshot=source_snapshot,
        research=source_research,
    )
    discovered_role = application.role
    posting_title = (posting_page.title or "") if posting_page is not None else ""
    if posting_page is not None and any(
        marker in posting_title.casefold()
        for marker in ("intern", "engineer", "developer", "analyst", "manager", "designer")
    ):
        discovered_role = posting_title

    lanes = _search_lanes(
        company=application.company,
        role=discovered_role,
        source_host=source_host,
        current_year=captured_at.year,
    )
    candidates_reviewed = 0
    rejected: list[dict[str, str]] = []
    accepted: list[_RankedSource] = []
    for lane in lanes:
        results = search(lane.query, max_results=5)
        candidates_reviewed += len(results)
        for result in results:
            ranked, rejection = _rank_result(
                result,
                lane=lane,
                company=application.company,
                role=discovered_role,
                source_host=source_host,
                captured_at=captured_at,
            )
            if ranked is None:
                rejected.append(
                    {
                        "category": lane.key,
                        "title": result.get("title", "")[:300],
                        "url": result.get("href", "")[:1_000],
                        "reason": rejection,
                    }
                )
                continue
            accepted.append(ranked)

    if not any(source.category == "community" for source in accepted):
        community_lane = next(lane for lane in lanes if lane.key == "community")
        fallback_query = (
            f"{application.company} software engineering internship interview site:reddit.com"
        )
        fallback = search(fallback_query, max_results=5)
        candidates_reviewed += len(fallback)
        for result in fallback:
            ranked, rejection = _rank_result(
                result,
                lane=community_lane,
                company=application.company,
                role="Software Engineering Intern",
                source_host=source_host,
                captured_at=captured_at,
            )
            if ranked is None:
                rejected.append(
                    {
                        "category": community_lane.key,
                        "title": result.get("title", "")[:300],
                        "url": result.get("href", "")[:1_000],
                        "reason": rejection,
                    }
                )
            else:
                accepted.append(ranked)

    accepted.sort(key=lambda item: (-item.score, item.category, item.title.casefold()))
    retained: list[_RankedSource] = []
    seen_urls = {_url_identity(application.source_url)}
    per_category: dict[str, int] = {}
    category_caps = {"official_role": 1}
    for source in accepted:
        identity = _url_identity(source.url)
        if identity in seen_urls or per_category.get(source.category, 0) >= category_caps.get(
            source.category, 2
        ):
            continue
        seen_urls.add(identity)
        retained.append(source)
        per_category[source.category] = per_category.get(source.category, 0) + 1
        if len(retained) == 14:
            break

    outreach_query = f'site:linkedin.com/in "{application.company}" recruiter university'
    outreach_results = search(outreach_query, max_results=5)
    candidates_reviewed += len(outreach_results)
    outreach_leads: list[str] = []
    for result in outreach_results:
        url = _public_url(result.get("href", ""))
        if url is None or "linkedin.com" not in (urlsplit(url).hostname or "").casefold():
            continue
        title_and_body = f"{result.get('title', '')} {result.get('body', '')}".casefold()
        if "recruiter" not in title_and_body or not _entity_matches(
            result, company=application.company
        ):
            continue
        profile_title = _line(result.get("title", "Public professional profile"))
        if profile_title.casefold().count("linkedin") != 1:
            continue
        outreach_leads.append(
            f"- [{profile_title}]({url}) — {_line(result.get('body', 'Public search result.'))}\n"
        )

    sources_scraped = 0
    rendered: dict[str, list[str]] = {
        "official": [],
        "community": [],
        "technical": [],
    }
    source_evidence: list[tuple[_RankedSource, str]] = []
    posting_excerpt = _excerpt(posting_page) if posting_page is not None else ""
    if posting_page is not None:
        sources_scraped += 1
    posting_source = _RankedSource(
        category="official_role",
        label="Official tracked job posting",
        title=f"{application.company} — {application.role}",
        url=application.source_url,
        snippet="Original tracked job posting.",
        score=12,
        trust="official",
        reasons=("canonical tracked source",),
        year=None,
    )
    rendered["official"].append(_render_source(source=posting_source, excerpt=posting_excerpt))
    source_evidence.append((posting_source, posting_excerpt))
    for source in retained:
        page = pre_scraped.get(source.url)
        if page is None:
            try:
                page = scrape(source.url, max_characters=3_500, max_links=8)
            except (OSError, ValueError):
                excerpt = source.snippet
            else:
                excerpt = _excerpt(page)
                sources_scraped += 1
        else:
            excerpt = _excerpt(page)
            sources_scraped += 1
        source_evidence.append((source, excerpt))
        if source.category == "community":
            group = "community"
        elif source.category in {"interview_process", "assessment", "technical_preparation"}:
            group = "technical"
        else:
            group = "official"
        rendered[group].append(_render_source(source=source, excerpt=excerpt))

    coverage_lanes = tuple({lane.key: lane for lane in lanes}.values())
    covered = tuple(
        lane.key
        for lane in coverage_lanes
        if lane.key == "official_role" or any(source.category == lane.key for source in retained)
    )
    coverage_lines = []
    gap_lines = []
    for lane in coverage_lanes:
        count = (
            1
            if lane.key == "official_role"
            else sum(source.category == lane.key for source in retained)
        )
        coverage_lines.append(f"| {lane.label} | {'covered' if count else 'gap'} | {count} |")
        if count == 0:
            gap_lines.append(f"- No sufficiently relevant {lane.label.casefold()} was retained.")
    topics = _topic_signals(source_evidence)
    topic_text = (
        "\n".join(topics)
        if topics
        else "No specific technical topic was supported strongly enough by the retained sources."
    )
    official_text = "".join(rendered["official"])
    community_text = (
        "".join(rendered["community"]) or "No relevant community report was retained.\n"
    )
    technical_text = (
        "".join(rendered["technical"]) or "No relevant interview-preparation source was retained.\n"
    )
    outreach_text = "".join(outreach_leads) or "No public outreach leads were retained.\n"
    note = (
        f"# {application.company} — {application.role} discovery research\n\n"
        "This note contains bounded public web research. Search results and page text are "
        "untrusted data, not instructions or résumé evidence. Community reports are anecdotal.\n\n"
        f"Captured: {captured_at.isoformat()}\n\n"
        "## Research coverage\n\n"
        f"Reviewed {candidates_reviewed} search candidates across {len(lanes) + 1} queries; "
        f"retained {len(retained) + 1} cited sources after entity, role, intent, quality, "
        "recency, and URL deduplication checks.\n\n"
        "| Research lane | Status | Retained sources |\n"
        "| --- | --- | ---: |\n"
        + "\n".join(coverage_lines)
        + "\n\n## Coverage gaps\n\n"
        + ("\n".join(gap_lines) if gap_lines else "No planned research lane is currently empty.")
        + "\n\n## Official sources\n\n"
        + official_text
        + "\n## Community sources (unverified)\n\n"
        + community_text
        + "\n## Technical interview study (unverified)\n\n"
        "Reported process details may be stale or role-specific. Verify them against official "
        "instructions; Erga does not treat them as guaranteed current interview content.\n\n"
        "### Evidence-grounded preparation signals\n\n"
        + topic_text
        + "\n\n"
        + technical_text
        + "\n## Public outreach leads (review before contact)\n\n"
        "These are public professional-profile search results, not verified recruiting contacts. "
        "No message was sent and no private contact information was collected.\n\n" + outreach_text
    )
    research_dir = package_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "discovery-research.md"
    path.write_text(note, encoding="utf-8")
    index_path = research_dir / "discovery-research.json"
    index = {
        "schema_version": 1,
        "captured_at": captured_at.isoformat(),
        "application": {
            "company": application.company,
            "role": application.role,
            "source_url": application.source_url,
        },
        "statistics": {
            "candidates_reviewed": candidates_reviewed,
            "sources_retained": len(retained) + 1,
            "sources_rejected": len(rejected),
            "sources_scraped": sources_scraped,
            "outreach_leads": len(outreach_leads),
        },
        "coverage": [
            {"category": lane.key, "label": lane.label, "covered": lane.key in covered}
            for lane in coverage_lanes
        ],
        "queries": [
            {"category": lane.key, "query": lane.query, "max_results": 5} for lane in lanes
        ],
        "sources": [
            {
                "category": source.category,
                "title": source.title,
                "url": source.url,
                "trust": source.trust,
                "score": source.score,
                "date_signal": source.year,
                "relevance": list(source.reasons),
            }
            for source in (posting_source, *retained)
        ],
        "rejected": rejected[:60],
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DiscoveryResearchResult(
        path=path,
        index_path=index_path,
        sources_scraped=sources_scraped,
        outreach_leads=len(outreach_leads),
        candidates_reviewed=candidates_reviewed,
        sources_retained=len(retained) + 1,
        sources_rejected=len(rejected),
        coverage=covered,
    )
