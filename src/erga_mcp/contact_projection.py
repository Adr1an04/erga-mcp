from __future__ import annotations

import re
from collections.abc import Sequence

from .config import ContactOutputSettings
from .models import RecruiterContact

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
_MANAGED_START = "<!-- erga:recruiter-contact:start -->"
_MANAGED_END = "<!-- erga:recruiter-contact:end -->"


def project_recruiter_contacts(
    contacts: Sequence[RecruiterContact], outputs: Sequence[ContactOutputSettings]
) -> int:
    """Project canonical contacts to explicitly configured local outputs."""
    written = 0
    for output in outputs:
        if output.kind != "obsidian":
            raise ValueError(f"unsupported contact output: {output.kind}")
        output.directory.mkdir(parents=True, exist_ok=True)
        for contact in contacts:
            stem = _SAFE_FILENAME.sub("-", contact.name or contact.email).strip(" .-") or contact.id
            path = output.directory / f"{stem}.md"
            body = _render_obsidian_contact(contact)
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            path.write_text(_upsert_managed_block(existing, body), encoding="utf-8")
            written += 1
    return written


def _upsert_managed_block(existing: str, body: str) -> str:
    managed = f"{_MANAGED_START}\n{body}{_MANAGED_END}\n"
    if _MANAGED_START in existing and _MANAGED_END in existing:
        before, _, remainder = existing.partition(_MANAGED_START)
        _, _, after = remainder.partition(_MANAGED_END)
        return f"{before}{managed}{after.lstrip()}"
    prefix = existing.rstrip()
    separator = "\n\n" if prefix else ""
    return f"{prefix}{separator}{managed}"


def _render_obsidian_contact(contact: RecruiterContact) -> str:
    name = contact.name or contact.email
    company = contact.company or ""
    return (
        f"# {name}\n\n"
        "- Type: Recruiter contact\n"
        f"- Email: {contact.email}\n"
        f"- Company: {company}\n"
        f"- First seen: {contact.first_seen_at.date().isoformat()}\n"
        f"- Last seen: {contact.last_seen_at.date().isoformat()}\n"
        f"- Source message: {contact.source_message_id}\n"
    )
