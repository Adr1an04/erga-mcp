from __future__ import annotations

import re
from collections.abc import Sequence
from html import unescape
from urllib.request import Request, urlopen

from .models import Evidence

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}")
_TAG = re.compile(r"<[^>]+>")
_STOP_WORDS = frozenset({"and", "for", "from", "into", "that", "the", "with", "you", "your"})


def _terms(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(text) if word.casefold() not in _STOP_WORDS}


def fetch_job_snapshot(job_url: str) -> str:
    """Retrieve a job page as untrusted plain text for local review and snapshotting."""
    if not job_url.startswith(("https://", "http://")):
        raise ValueError("job URL must use HTTP(S)")
    request = Request(job_url, headers={"User-Agent": "recruiting-pipeline/0.1"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - user-supplied job URL
        html = response.read().decode("utf-8", errors="replace")
    text = " ".join(unescape(_TAG.sub(" ", html)).split())
    if not text:
        raise ValueError("job page did not contain readable text")
    return text


def select_relevant_evidence(job_description: str, evidence: Sequence[Evidence]) -> list[Evidence]:
    """Rank approved, user-provided evidence by transparent lexical overlap only."""
    job_terms = _terms(job_description)
    scored = [(len(job_terms & _terms(item.text)), item) for item in evidence if item.approved]
    return [
        item for score, item in sorted(scored, key=lambda pair: (-pair[0], pair[1].id)) if score
    ]
