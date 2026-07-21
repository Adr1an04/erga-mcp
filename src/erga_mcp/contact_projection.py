from __future__ import annotations

import re
from collections.abc import Sequence

from .config import ContactOutputSettings
from .models import RecruiterContact

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


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
            path.write_text(_render_obsidian_contact(contact), encoding="utf-8")
            written += 1
    return written


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
