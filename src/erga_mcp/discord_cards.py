from __future__ import annotations

from .card_view import CardField, CardView
from .discord_bridge import ERGA_ORBIT_VIOLET, DiscordCard, DiscordCardField

_TITLE_LIMIT = 256
_DESCRIPTION_LIMIT = 4_096
_FIELD_NAME_LIMIT = 256
_FIELD_VALUE_LIMIT = 1_024
_FIELD_COUNT_LIMIT = 25
_EMBED_TEXT_LIMIT = 6_000


def _bounded(value: str, limit: int) -> str:
    cleaned = value.strip() or "Not configured"
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def discord_card_from_view(card: CardView) -> DiscordCard:
    """Render a client-neutral card inside Discord's documented embed limits."""
    title = _bounded(card.title, _TITLE_LIMIT)
    description = _bounded(card.summary, _DESCRIPTION_LIMIT)
    budget = _EMBED_TEXT_LIMIT - len(title) - len(description) - len(card.footer)
    fields: list[DiscordCardField] = []
    for field in card.fields[:_FIELD_COUNT_LIMIT]:
        rendered = _bounded_field(field, budget)
        if rendered is None:
            break
        fields.append(rendered)
        budget -= len(rendered.name) + len(rendered.value)
    return DiscordCard(
        title=title,
        description=description,
        color=ERGA_ORBIT_VIOLET,
        fields=tuple(fields),
        footer=_bounded(card.footer, 2_048),
    )


def _bounded_field(field: CardField, budget: int) -> DiscordCardField | None:
    if budget < 2:
        return None
    name = _bounded(field.name, min(_FIELD_NAME_LIMIT, budget - 1))
    remaining = budget - len(name)
    if remaining < 1:
        return None
    value = _bounded(field.value, min(_FIELD_VALUE_LIMIT, remaining))
    return DiscordCardField(name=name, value=value, inline=field.inline)
