from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
