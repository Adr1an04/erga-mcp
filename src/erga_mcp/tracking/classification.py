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
    "moving forward with other candidates",
    "pursue other candidates",
    "selected another candidate",
    "we regret to inform you",
    "you were not selected",
    "application was not selected",
)
_ASSESSMENT_MARKERS = (
    "hackerrank",
    "codesignal",
    "codility",
    "coding test",
    "online assessment",
    "assessment invitation",
    "technical assessment",
    "skills assessment",
    "take-home assignment",
    "take home assignment",
)
_INTERVIEW_MARKERS = (
    "interview invitation",
    "interview confirmation",
    "interview availability",
    "invite you to interview",
    "interview with",
    "schedule an interview",
    "schedule your interview",
    "phone screen",
    "technical interview",
    "virtual interview",
    "onsite interview",
    "on-site interview",
)
_OFFER_MARKERS = (
    "offer letter",
    "offer of employment",
    "pleased to offer",
    "extend an offer",
)
_NON_APPLICATION_MARKERS = (
    "new roles available",
    "new jobs available",
    "job alert",
    "recommended jobs",
    "report domain",
    "security alert",
    "trial for",
    "unsubscribe",
)
_STRONG_NON_APPLICATION_SUBJECT_MARKERS = (
    "job alert",
    "recommended jobs",
    "security alert",
    "trial ended",
    "trial has ended",
)
_COHERENT_NON_APPLICATION_MARKERS = tuple(
    marker for marker in _NON_APPLICATION_MARKERS if marker != "unsubscribe"
)
_ACKNOWLEDGEMENT_MARKERS = (
    "we received your application",
    "application received",
    "thank you for applying",
    "thanks for applying",
    "thank you for your interest in",
)


def classify_application_message(*, subject: str, preview: str) -> Classification:
    """Classify a message conservatively without following message instructions."""
    normalized_subject = subject.casefold()
    content = f"{subject}\n{preview}".casefold()
    if any(marker in normalized_subject for marker in _STRONG_NON_APPLICATION_SUBJECT_MARKERS):
        return Classification(kind="unknown", confidence=0.0, requires_review=False)
    if any(marker in content for marker in _COHERENT_NON_APPLICATION_MARKERS):
        return Classification(kind="unknown", confidence=0.0, requires_review=False)
    if any(marker in content for marker in _DENIAL_MARKERS):
        return Classification(kind="denial", confidence=0.95, requires_review=True)
    if any(marker in content for marker in _OFFER_MARKERS):
        return Classification(kind="offer", confidence=0.99, requires_review=True)
    if any(marker in content for marker in _INTERVIEW_MARKERS):
        return Classification(kind="interview", confidence=0.98, requires_review=True)
    if any(marker in content for marker in _ASSESSMENT_MARKERS):
        return Classification(kind="assessment", confidence=0.98, requires_review=True)
    if any(marker in content for marker in _ACKNOWLEDGEMENT_MARKERS):
        return Classification(kind="acknowledgement", confidence=0.9, requires_review=False)
    if "unsubscribe" in content:
        return Classification(kind="unknown", confidence=0.0, requires_review=False)
    # Unknown mail is not itself actionable. The provider adapter may separately recognize
    # direct recruiter outreach, but arbitrary inbox text must never become review work merely
    # because no deterministic application status matched.
    return Classification(kind="unknown", confidence=0.0, requires_review=False)
