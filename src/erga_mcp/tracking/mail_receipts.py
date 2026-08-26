from __future__ import annotations

import html
import re
from dataclasses import dataclass
from email.utils import parseaddr

RECEIPT_PARSER_VERSION = 4


@dataclass(frozen=True)
class ApplicationReceipt:
    """Bounded application identity extracted transiently from untrusted mail content."""

    company: str = ""
    role: str = ""
    requisition_ids: tuple[str, ...] = ()
    recruiting_cycle_hints: tuple[str, ...] = ()


_HTML_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_REQUISITION_PATTERNS = (
    re.compile(r"\b(JR\d{5,})\b", re.I),
    re.compile(r"\bjob\s+(?:number|id)\s*[:#-]?\s*([a-z0-9][a-z0-9_-]{2,39})\b", re.I),
    re.compile(
        r"\breq(?:uisition)?\s*(?:number|id)?\s*[:#-]?\s*([a-z0-9][a-z0-9_-]{2,39})\b", re.I
    ),
)
_ROLE_PATTERNS = (
    re.compile(
        r"submit(?:ted)?\s+your\s+application\s+for\s+(.+?)\s*"
        r"\(\s*job\s+(?:number|id)\s*:",
        re.I,
    ),
    re.compile(r"received\s+your\s+application\s+for\s+the\s+role\s*:\s*(.+?)(?:\s+[.!]|$)", re.I),
    re.compile(
        r"(?:received|reviewing)\s+your\s+application\s+for\s+"
        r"(?:the\s+)?(?:position\s+of\s+)?(.+?)(?:\s+at\s+[A-Z][A-Za-z0-9& .'-]+|[.!]|$)",
        re.I,
    ),
    re.compile(r"interest\s+you(?:'|’)ve\s+shown\s+in\s+the\s+(.+?)\s+position\b", re.I),
    re.compile(r"application\s+for\s+the\s+position\s+of\s+(.+?)\s+has\s+been\s+received", re.I),
    re.compile(r"application\s+for\s+the\s+(.+?)\s+role\s+has\s+been\s+received", re.I),
    re.compile(r"application\s+for\s+(.+?)\s+has\s+been\s+received", re.I),
)
_SUBJECT_COMPANY_ROLE_PATTERNS = (
    re.compile(
        r"successfully\s+submitted\s+your\s+(.+?)\s+job\s+application\s*-\s*"
        r"(?:[a-z0-9_-]{3,}\s*-\s*)?(.+)$",
        re.I,
    ),
)
_SUBJECT_ROLE_PATTERNS = (
    re.compile(r"(?:thank\s+you|thanks)\s+for\s+applying\s+to\s+(.+)$", re.I),
)
_SUBJECT_APPLYING_TO_PATTERN = re.compile(
    r"(?:thank\s+you|thanks)\s+for\s+applying\s+to\s+(.+?)(?:[!.,:]|$)", re.I
)
_SUBJECT_COMPANY_PATTERNS = (
    re.compile(r"(?:thank\s+you|thanks)\s+for\s+your\s+interest\s+in\s+(.+?)(?:[!.,:]|$)", re.I),
    re.compile(r"(?:thank\s+you|thanks)\s+for\s+applying\s+at\s+(.+?)(?:[!.,:]|$)", re.I),
    re.compile(r"your\s+(.+?)\s+careers\s+application\s+is\s+in(?:[!.,:]|$)", re.I),
    re.compile(r"your\s+application\s+to\s+the\s+(.+?\s+group)(?:[!.,:]|$)", re.I),
)
_BODY_COMPANY_PATTERNS = (
    re.compile(
        r"career\s+opportunities\s+with\s+([A-Z][A-Za-z0-9& .'-]{1,80}?)(?:[!.,]|\s+and\b)",
        re.I,
    ),
    re.compile(r"interest\s+in\s+joining\s+([A-Z][A-Za-z0-9& .'-]{1,80}?)(?:[!.,]|\s+we\b)", re.I),
    re.compile(
        r"interested\s+in\s+a\s+career\s+at\s+([A-Z][A-Za-z0-9& .'-]{1,80}?)(?:[!.,]|\s+and\b)",
        re.I,
    ),
)
_ATS_RELAY_DOMAINS = (
    "eightfold.ai",
    "greenhouse-mail.io",
    "icims.com",
    "myworkday.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workday.com",
)
_ATS_COMPANY_IN_SUBJECT_DOMAINS = ("greenhouse-mail.io", "smartrecruiters.com")
_GENERIC_SENDER_PARTS = frozenset(
    {
        "applicant",
        "careers",
        "donotreply",
        "email",
        "jobs",
        "mail",
        "noreply",
        "notifications",
        "recruiting",
        "talent",
    }
)
_FULL_CYCLE_PATTERN = re.compile(r"\b(Winter|Spring|Summer|Fall)\s+(20\d{2})\b", re.I)
_REVERSED_CYCLE_PATTERN = re.compile(r"\b(20\d{2})\s+(Winter|Spring|Summer|Fall)\b", re.I)
_LOOSE_CYCLE_PATTERN = re.compile(
    r"\b(Winter|Spring|Summer|Fall)\b"
    r"(?:\s+(?:internship|intern|co-?op|program|role|position|engineering|"
    r"undergraduate|graduate|campus|software)){0,5}\s*[-–—,:]?\s*(20\d{2})\b",
    re.I,
)
_SEASON_PATTERN = re.compile(r"\b(Winter|Spring|Summer|Fall)\b", re.I)


def _plain_text(value: str) -> str:
    return _SPACE.sub(" ", html.unescape(_HTML_TAG.sub(" ", value))).strip()


def _bounded_display(value: str, *, limit: int) -> str:
    cleaned = _SPACE.sub(" ", value).strip(" \t\r\n-–—:;,.!()[]{}")
    if not cleaned or any(ord(character) < 32 for character in cleaned):
        return ""
    return cleaned[:limit].rstrip()


def _company_from_sender(sender: str) -> str:
    display_name, address = parseaddr(sender)
    display = re.sub(
        r"\b(?:careers?|recruiting|talent\s+acquisition(?:\s+team)?|jobs?)\b",
        " ",
        display_name,
        flags=re.I,
    )
    display = _bounded_display(display, limit=100)
    if display and display.casefold() not in _GENERIC_SENDER_PARTS:
        return display

    local, separator, domain = address.casefold().partition("@")
    if not separator:
        return ""
    is_ats_relay = any(
        domain == suffix or domain.endswith(f".{suffix}") for suffix in _ATS_RELAY_DOMAINS
    )
    if is_ats_relay:
        candidate = re.split(r"[+._-]", local, maxsplit=1)[0]
        if candidate and candidate not in _GENERIC_SENDER_PARTS:
            return _bounded_display(candidate.replace("-", " ").title(), limit=100)
        return ""
    labels = [label for label in domain.split(".") if label not in _GENERIC_SENDER_PARTS]
    labels = [
        label
        for label in labels
        if label not in {"ai", "app", "co", "com", "dev", "io", "jobs", "net", "org", "test"}
    ]
    if labels:
        candidate = labels[-1]
        if candidate.endswith("hq") and len(candidate) > 4:
            candidate = candidate[:-2]
        return _bounded_display(candidate.replace("-", " ").title(), limit=100)
    return ""


def _ats_subject_company(sender: str, subject: str) -> str:
    _display, address = parseaddr(sender)
    _local, separator, domain = address.casefold().partition("@")
    if not separator or not any(
        domain == suffix or domain.endswith(f".{suffix}")
        for suffix in _ATS_COMPANY_IN_SUBJECT_DOMAINS
    ):
        return ""
    match = _SUBJECT_APPLYING_TO_PATTERN.search(subject)
    return _bounded_display(match.group(1), limit=100) if match else ""


def _company_hint(sender: str, subject: str, content: str) -> str:
    if company := _ats_subject_company(sender, subject):
        return company
    for pattern in _SUBJECT_COMPANY_PATTERNS:
        if match := pattern.search(subject):
            return _bounded_display(match.group(1), limit=100)
    for pattern in _BODY_COMPANY_PATTERNS:
        if match := pattern.search(content):
            return _bounded_display(match.group(1), limit=100)
    return _company_from_sender(sender)


def _strip_receipt_prefix(value: str, *, company: str, requisitions: tuple[str, ...]) -> str:
    normalized = value
    for requisition in requisitions:
        normalized = re.sub(rf"^\s*{re.escape(requisition)}\s+", "", normalized, flags=re.I)
    if company:
        normalized = re.sub(rf"^\s*{re.escape(company)}\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^\s*(?:the\s+)?(?:position|role)\s+of\s+", "", normalized, flags=re.I)
    normalized = re.sub(r"^\s*(?:the\s+)?role\s*:\s*", "", normalized, flags=re.I)
    normalized = re.sub(r"\s+(?:position|role)\s*$", "", normalized, flags=re.I)
    return _bounded_display(normalized, limit=200)


def _recruiting_cycle_hints(*values: str) -> tuple[str, ...]:
    """Retain only recruiting terms stated by the message, never a date-based guess."""
    for value in values:
        if not value:
            continue
        hints: list[str] = []
        keys: set[str] = set()

        def add(hint: str) -> None:
            normalized = " ".join(hint.split()).title()
            if normalized.casefold() not in keys:
                hints.append(normalized)
                keys.add(normalized.casefold())

        for season, year in _FULL_CYCLE_PATTERN.findall(value):
            add(f"{season} {year}")
        for year, season in _REVERSED_CYCLE_PATTERN.findall(value):
            add(f"{season} {year}")
        for season, year in _LOOSE_CYCLE_PATTERN.findall(value):
            add(f"{season} {year}")
        if hints:
            return tuple(hints)

    for value in values:
        if not value:
            continue
        seasons = {season.title() for season in _SEASON_PATTERN.findall(value)}
        if len(seasons) == 1:
            return (seasons.pop(),)
    return ()


def parse_application_receipt(
    *, sender: str, subject: str, preview: str = "", content: str = ""
) -> ApplicationReceipt:
    """Extract company, role, and requisition while never returning body or preview text."""
    body_plain = _plain_text(f"{preview}\n{content}")
    all_plain = _plain_text(f"{subject}\n{preview}\n{content}")
    requisitions = tuple(
        sorted(
            {
                match.group(1).casefold()
                for pattern in _REQUISITION_PATTERNS
                for match in pattern.finditer(all_plain)
                if any(character.isdigit() for character in match.group(1))
            }
        )
    )[:12]
    company = ""
    role = ""
    for pattern in _SUBJECT_COMPANY_ROLE_PATTERNS:
        if match := pattern.search(subject):
            company = _bounded_display(match.group(1), limit=100)
            role = _strip_receipt_prefix(match.group(2), company=company, requisitions=requisitions)
            break
    company = company or _company_hint(sender, subject, body_plain)
    for pattern in _ROLE_PATTERNS:
        if match := pattern.search(body_plain):
            role = _strip_receipt_prefix(match.group(1), company=company, requisitions=requisitions)
            break
    if not role and not _ats_subject_company(sender, subject):
        for pattern in _SUBJECT_ROLE_PATTERNS:
            if match := pattern.search(subject):
                candidate = re.sub(r"\s+-\s+\d{6,}\s*$", "", match.group(1))
                role = _strip_receipt_prefix(candidate, company=company, requisitions=requisitions)
                break
    recruiting_cycle_hints = _recruiting_cycle_hints(role, subject, body_plain)
    return ApplicationReceipt(
        company=company,
        role=role,
        requisition_ids=requisitions,
        recruiting_cycle_hints=recruiting_cycle_hints,
    )
