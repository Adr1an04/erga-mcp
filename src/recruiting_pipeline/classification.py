from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    kind: str
    confidence: float
    requires_review: bool


_DENIAL_MARKERS = (
    "will not be moving forward",
    "not moving forward",
    "decided not to proceed",
    "other candidates",
    "unfortunately",
)
_ACKNOWLEDGEMENT_MARKERS = (
    "we received your application",
    "application received",
    "thank you for applying",
    "thanks for applying",
)


def classify_application_message(*, subject: str, preview: str) -> Classification:
    """Classify a message conservatively without following message instructions."""
    content = f"{subject}\n{preview}".casefold()
    if any(marker in content for marker in _DENIAL_MARKERS):
        return Classification(kind="denial", confidence=0.95, requires_review=True)
    if any(marker in content for marker in _ACKNOWLEDGEMENT_MARKERS):
        return Classification(kind="acknowledgement", confidence=0.9, requires_review=False)
    return Classification(kind="unknown", confidence=0.0, requires_review=True)
