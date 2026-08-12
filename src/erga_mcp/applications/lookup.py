from __future__ import annotations

from erga_mcp.models import Application


def select_tracked_application(query: str, applications: list[Application]) -> Application:
    """Select exactly one tracked application from explicit company/role search terms."""
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        raise ValueError("notes query must include a company or role word")
    matches = [
        application
        for application in applications
        if all(term in f"{application.company} {application.role}".casefold() for term in terms)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no tracked application matches: {query}")
    choices = ", ".join(f"{item.company} - {item.role}" for item in matches)
    raise ValueError(f"multiple tracked applications match {query!r}: {choices}")
