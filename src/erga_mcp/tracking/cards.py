from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CardField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class CardAction:
    action_id: str
    label: str
    instruction: str
    style: str = "secondary"


@dataclass(frozen=True)
class CardView:
    """Client-neutral review card shared by CLI, MCP, and Discord renderers."""

    title: str
    summary: str
    fields: tuple[CardField, ...]
    actions: tuple[CardAction, ...] = ()
    page: int = 1
    page_count: int = 1
    footer: str = "Private by default - Erga never submits applications"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def as_text(self) -> str:
        lines = [f"### {self.title}", self.summary]
        for field in self.fields:
            lines.extend(("", f"**{field.name}**", field.value))
        if self.actions:
            lines.extend(("", "**Actions**"))
            lines.extend(
                f"- `{action.action_id}` - {action.label}: {action.instruction}"
                for action in self.actions
            )
        if self.page_count > 1:
            lines.extend(("", f"Page {self.page} of {self.page_count}"))
        return "\n".join(lines)
