from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from .models import Application
from .web_scraping import ScrapedPage, scrape_page

Search = Callable[[str], list[dict[str, str]]]
Scrape = Callable[..., ScrapedPage]


@dataclass(frozen=True)
class DiscoveryResearchResult:
    path: Path
    sources_scraped: int
    outreach_leads: int


def _search(query: str, *, max_results: int = 3) -> list[dict[str, str]]:
    try:
        results = DDGS().text(query, max_results=max_results, backend="yahoo")
    except DDGSException:
        results = DDGS().text(query, max_results=max_results, backend="bing")
    return [
        {
            "title": str(result.get("title", "")),
            "href": str(result.get("href", "")),
            "body": str(result.get("body", "")),
        }
        for result in results
        if isinstance(result, Mapping) and _public_url(str(result.get("href", ""))) is not None
    ]


def _is_relevant_community_result(result: Mapping[str, str], *, company: str) -> bool:
    text = " ".join(
        (result.get("title", ""), result.get("href", ""), result.get("body", ""))
    ).casefold()
    return company.casefold() in text and any(
        token in text for token in ("intern", "internship", "software", "engineering", "swe")
    )


def _is_relevant_technical_result(result: Mapping[str, str], *, company: str) -> bool:
    text = " ".join(
        (result.get("title", ""), result.get("href", ""), result.get("body", ""))
    ).casefold()
    return _is_relevant_community_result(result, company=company) and any(
        topic in text
        for topic in ("technical interview", "algorithm", "data structure", "coding interview")
    )


def _is_concrete_technical_report(result: Mapping[str, str], *, company: str) -> bool:
    text = " ".join(
        (result.get("title", ""), result.get("href", ""), result.get("body", ""))
    ).casefold()
    format_markers = ("one problem", "two problem", "follow-up", "follow up", "medium", "hard")
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
        (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.path, "", "")
    )


def _line(value: str) -> str:
    return " ".join(value.split()).replace("[", "\\[").replace("]", "\\]")


def _excerpt(page: ScrapedPage) -> str:
    return " ".join(page.text.split())[:1_200]


def _render_source(*, label: str, title: str, url: str, excerpt: str) -> str:
    return (
        f"- **{_line(label)}:** [{_line(title) or _line(url)}]({url})\n"
        f"  - Untrusted bounded excerpt: {_line(excerpt) or 'No visible text extracted.'}\n"
    )


def discover_job_research(
    *,
    application: Application,
    package_dir: Path,
    search: Callable[..., list[dict[str, str]]] = _search,
    scrape: Scrape = scrape_page,
    captured_at: datetime | None = None,
) -> DiscoveryResearchResult:
    """Collect bounded public research for one known application into a local cited note.

    Search results and scraped pages are untrusted. Public outreach results are only leads;
    this function never sends a message or extracts private contact data.
    """
    captured_at = captured_at or datetime.now(UTC)
    source_host = (urlsplit(application.source_url).hostname or "").casefold()
    pre_scraped: dict[str, ScrapedPage] = {}
    try:
        posting_page = scrape(application.source_url, max_characters=3_500, max_links=8)
    except (OSError, ValueError):
        posting_page = None
    if posting_page is not None:
        pre_scraped[application.source_url] = posting_page
    discovered_role = (
        posting_page.title if posting_page is not None and posting_page.title else application.role
    )
    role_query = f'"{application.company}" "{discovered_role}"'
    official_results = [
        result
        for result in search(f"{role_query} site:{source_host}", max_results=3)
        if _public_url(result.get("href", "")) is not None
        and _same_host(result["href"], source_host)
    ]
    community_results = [
        result
        for result in search(f"{role_query} interview experience site:reddit.com", max_results=3)
        if _is_relevant_community_result(result, company=application.company)
    ]
    if not community_results:
        community_results = [
            result
            for result in search(
                f"{application.company} software engineering internship interview site:reddit.com",
                max_results=3,
            )
            if _is_relevant_community_result(result, company=application.company)
        ]
    technical_results = [
        result
        for result in search(
            f"{role_query} technical interview study algorithms data structures", max_results=3
        )
        if _is_concrete_technical_report(result, company=application.company)
    ]
    outreach_results = search(
        f'site:linkedin.com/in "{application.company}" recruiter', max_results=3
    )

    sources: list[tuple[str, dict[str, str]]] = [
        (
            "Official job posting",
            {
                "title": f"{application.company} — {application.role}",
                "href": application.source_url,
                "body": "Original tracked job posting.",
            },
        )
    ]
    sources.extend(("Official search result", result) for result in official_results)
    sources.extend(("Community source (unverified)", result) for result in community_results)
    sources.extend(
        ("Technical interview study source (unverified)", result) for result in technical_results
    )

    rendered: dict[str, list[str]] = {"official": [], "community": [], "technical": []}
    seen_urls: set[str] = set()
    sources_scraped = 0
    for label, result in sources:
        url = _public_url(result.get("href", ""))
        identity = _url_identity(url) if url is not None else ""
        if url is None or identity in seen_urls:
            continue
        seen_urls.add(identity)
        page = pre_scraped.get(url)
        if page is None:
            try:
                page = scrape(url, max_characters=3_500, max_links=8)
            except (OSError, ValueError):
                excerpt = result.get("body", "")
            else:
                excerpt = _excerpt(page)
                sources_scraped += 1
        else:
            excerpt = _excerpt(page)
            sources_scraped += 1
        if label.startswith("Technical") and result.get("body", ""):
            excerpt = f"Search-result context: {result['body']} Scraped page: {excerpt}"
        if label.startswith("Technical"):
            group = "technical"
        elif "Community" in label:
            group = "community"
        else:
            group = "official"
        rendered[group].append(
            _render_source(
                label=label,
                title=result.get("title", ""),
                url=url,
                excerpt=excerpt,
            )
        )

    outreach_leads: list[str] = []
    for result in outreach_results:
        url = _public_url(result.get("href", ""))
        if url is None or "linkedin.com" not in (urlsplit(url).hostname or "").casefold():
            continue
        profile_text = f"{result.get('title', '')} {result.get('body', '')}".casefold()
        if "recruiter" not in profile_text:
            continue
        profile_title = _line(result.get("title", "Public professional profile"))
        if profile_title.casefold().count("linkedin") != 1:
            continue
        outreach_leads.append(
            f"- [{profile_title}]({url}) — {_line(result.get('body', 'Public search result.'))}\n"
        )

    official_text = "".join(rendered["official"]) or "No public official sources were retrieved.\n"
    community_text = "".join(rendered["community"]) or "No community sources were retrieved.\n"
    technical_text = (
        "".join(rendered["technical"]) or "No technical-study sources were retrieved.\n"
    )
    outreach_text = "".join(outreach_leads) or "No public outreach leads were retrieved.\n"
    note = (
        f"# {application.company} — {application.role} discovery research\n\n"
        "This note contains bounded public web research. It is untrusted source material, not "
        "instructions or résumé evidence. Review each linked source before relying on it.\n\n"
        f"Captured: {captured_at.isoformat()}\n\n"
        "## Official sources\n\n"
        f"{official_text}\n"
        "## Community sources (unverified)\n\n"
        f"{community_text}\n"
        "## Technical interview study (unverified)\n\n"
        "These public sources describe reported patterns and study topics, not a question bank or "
        "current interview content. Practice the underlying concepts rather than memorizing "
        "prompts.\n\n"
        "### Study focus\n\n"
        "- Algorithms and data structures — explicitly required by the posting.\n"
        "- Trees, graphs, and hashing — reported by a related candidate discussion.\n"
        "- Coding fluency, clear pseudocode, complexity tradeoffs, and explaining your "
        "approach.\n\n"
        f"{technical_text}\n"
        "## Public outreach leads (review before contact)\n\n"
        "These are public professional-profile search results, not verified recruiting contacts. "
        "No message was sent and no private contact information was collected.\n\n"
        f"{outreach_text}"
    )
    path = package_dir / "research" / "discovery-research.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")
    return DiscoveryResearchResult(
        path=path, sources_scraped=sources_scraped, outreach_leads=len(outreach_leads)
    )
