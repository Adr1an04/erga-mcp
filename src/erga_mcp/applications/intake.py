from __future__ import annotations

import json
import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from erga_mcp.applications.research import build_job_snapshot
from erga_mcp.applications.role_profile import RoleProfile, role_profile_from_text
from erga_mcp.integrations.http import fetch_public_page
from erga_mcp.models import Evidence

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}")
_STOP_WORDS = frozenset({"and", "for", "from", "into", "that", "the", "with", "you", "your"})


def _terms(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text) if word.casefold() not in _STOP_WORDS}


def _shopify_embedded_job_text(page: str) -> str:
    """Recover Shopify's server-delivered job description without executing page code.

    Shopify serializes the posting into an escaped hydration-data string while rendering only
    navigation in the initial HTML. Restrict this fallback to a single matching payload and
    preserve it as untrusted text for the existing deterministic parser.
    """
    for match in re.finditer(r'"((?:\\.|[^"\\])*)"', page):
        try:
            value = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            continue
        if (
            "Being a Shopify Intern" not in value
            or "Qualifications:" not in value
            or "Compensation:" not in value
        ):
            continue
        intern_start = value.find("Being a Shopify Intern")
        start = value.rfind("About the role", 0, intern_start)
        if start < 0:
            start = intern_start
        end = value.find("Description du poste", start)
        relevant = value[start:end] if end >= 0 else value[start:]
        return (
            relevant.replace("\\n", "\n")
            .replace('\\"', '"')
            .replace("\\u003c", "<")
            .replace("\\u003e", ">")
        )
    return ""


def _is_shopify_careers_url(job_url: str) -> bool:
    parsed = urlsplit(job_url)
    hostname = (parsed.hostname or "").casefold()
    return (
        hostname == "shopify.com" or hostname.endswith(".shopify.com")
    ) and parsed.path.casefold().startswith("/careers/")


def fetch_job_snapshot(job_url: str) -> str:
    """Retrieve a job page as untrusted text within one 30-second deadline.

    Direct pinned sockets intentionally ignore ambient HTTP proxy variables for SSRF safety.
    """
    page = fetch_public_page(job_url)
    text = build_job_snapshot(page)
    if _is_shopify_careers_url(job_url):
        embedded = _shopify_embedded_job_text(page)
        if embedded:
            text = "\n\n".join(part for part in (text, embedded) if part.strip())
    if not text:
        raise ValueError("job page did not contain readable text")
    return text


def select_relevant_evidence(
    job_description: str,
    evidence: Sequence[Evidence],
    *,
    role_profile: RoleProfile | None = None,
) -> list[Evidence]:
    """Rank approved evidence with explainable requirement and lexical matching.

    Requirement concepts improve recall for common paraphrases, while lexical overlap remains a
    deterministic fallback. Matching only chooses which already-approved facts enter tailoring;
    it never approves evidence or establishes a new claim.
    """
    profile = role_profile or role_profile_from_text(job_description)
    job_terms = _terms(job_description)
    scored = []
    for item in evidence:
        if not item.approved:
            continue
        lexical_score = len(job_terms & _terms(item.text))
        requirement_match = profile.match(item.text)
        score = requirement_match.score * 10 + lexical_score
        scored.append((score, requirement_match.coverage_percent, lexical_score, item))
    return [
        item
        for score, _, _, item in sorted(
            scored,
            key=lambda pair: (-pair[0], -pair[1], -pair[2], pair[3].id),
        )
        if score
    ]
