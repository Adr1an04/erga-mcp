from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import TypedDict
from urllib.parse import parse_qs, urlsplit, urlunsplit

from erga_mcp.models import (
    Application,
    MailEvent,
    MailMatchCandidate,
    MailReconciliation,
    MailReconciliationSummary,
)
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.cards import CardAction, CardField, CardView

_TARGET_STATUS = {
    "application.acknowledgement": "applied",
    "application.assessment": "oa",
    "application.interview": "interview",
    "application.offer": "offer",
    "application.denial": "rejected",
}
_TERMINAL_STATUSES = frozenset({"offer", "accepted", "rejected", "withdrawn"})
_STATUS_PROGRESS = {
    "draft": 0,
    "applied": 1,
    "oa": 2,
    "assessment": 2,
    "interview": 3,
    "interview-2": 4,
    "interview-3": 5,
    "final-interview": 6,
    "offer": 7,
    "accepted": 8,
}
_ATS_HOSTS = (
    "applytojob.com",
    "ashbyhq.com",
    "bamboohr.com",
    "breezy.hr",
    "eightfold.ai",
    "greenhouse.io",
    "icims.com",
    "jobvite.com",
    "lever.co",
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "oraclecloud.com",
    "smartrecruiters.com",
    "workable.com",
)
_JOB_ROUTE = re.compile(r"/(?:apply|career|careers|job|jobs|position|positions)(?:/|$)", re.I)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_REQUISITION = re.compile(
    r"\b(?:req(?:uisition)?|job|posting|position)[\s:#_-]*(?:id[\s:#_-]*)?"
    r"([a-z0-9][a-z0-9_-]{2,39})\b",
    re.I,
)
_IDENTIFIER_QUERY_KEYS = frozenset({"gh_jid", "job_id", "jobid", "posting_id", "position"})
_TOKEN = re.compile(r"[a-z0-9]+")
_ROLE_STOPWORDS = frozenset(
    {
        "and",
        "associate",
        "co",
        "engineering",
        "intern",
        "internship",
        "junior",
        "of",
        "role",
        "senior",
        "the",
    }
)
_OPAQUE_DIGEST = re.compile(r"^sha256:[0-9a-f]{24}$")
_MAX_PROVIDER_CLOCK_SKEW = timedelta(hours=24)


class MailSignals(TypedDict):
    sender_domain: str
    job_urls: tuple[str, ...]
    requisition_ids: tuple[str, ...]
    thread_id: str
    reference_ids: tuple[str, ...]


def opaque_identifier_digest(value: str) -> str:
    """Return a bounded one-way identifier suitable for private local correlation."""
    normalized = value.strip().strip("<>")
    if not normalized:
        return ""
    if _OPAQUE_DIGEST.fullmatch(normalized):
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"sha256:{digest}"


def canonical_public_job_url(value: str) -> str:
    """Keep only a recognized public job route, dropping every query and fragment."""
    candidate = value.rstrip(".,);]")
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() not in {"http", "https"}:
        return ""
    host = (parsed.hostname or "").rstrip(".").casefold()
    if not host:
        return ""
    recognized_ats = any(host == suffix or host.endswith(f".{suffix}") for suffix in _ATS_HOSTS)
    recognized_route = bool(_JOB_ROUTE.search(parsed.path)) or bool(
        {key.casefold() for key in parse_qs(parsed.query)}.intersection(_IDENTIFIER_QUERY_KEYS)
    )
    if not recognized_ats and not recognized_route:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", "", ""))


def sanitized_mail_signals(
    *,
    sender: str,
    subject: str,
    preview: str = "",
    content: str = "",
    thread_id: str = "",
    reference_ids: Iterable[str] = (),
) -> MailSignals:
    """Extract bounded matching signals while retaining no preview/body or tracking query."""
    transient_text = f"{subject}\n{preview}\n{content}"
    urls: set[str] = set()
    requisitions: set[str] = set()
    for raw_url in _URL.findall(transient_text):
        canonical = canonical_public_job_url(raw_url)
        if not canonical:
            continue
        urls.add(canonical)
        parsed_raw = urlsplit(raw_url.rstrip(".,);]"))
        for key, values in parse_qs(parsed_raw.query).items():
            if key.casefold() in _IDENTIFIER_QUERY_KEYS:
                requisitions.update(_valid_requisition(value) for value in values)
        requisitions.update(_requisition_candidates_from_path(urlsplit(canonical).path))
    requisitions.update(_valid_requisition(match) for match in _REQUISITION.findall(transient_text))
    _, sender_address = parseaddr(sender)
    domain = sender_address.rsplit("@", 1)[-1].casefold() if "@" in sender_address else ""
    return {
        "sender_domain": domain[:253],
        "job_urls": tuple(sorted(urls))[:8],
        "requisition_ids": tuple(sorted(value for value in requisitions if value))[:12],
        "thread_id": opaque_identifier_digest(thread_id),
        "reference_ids": tuple(
            sorted(
                {digest for value in reference_ids if (digest := opaque_identifier_digest(value))}
            )
        )[:20],
    }


def _valid_requisition(value: str) -> str:
    normalized = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,39}", normalized):
        return ""
    if not any(character.isdigit() for character in normalized):
        return ""
    return normalized


def _requisition_candidates_from_path(path: str) -> set[str]:
    return {normalized for part in path.split("/") if (normalized := _valid_requisition(part))}


def _application_signals(application: Application) -> tuple[str, set[str]]:
    canonical = canonical_public_job_url(application.source_url)
    requisitions = (
        _requisition_candidates_from_path(urlsplit(canonical).path) if canonical else set()
    )
    parsed = urlsplit(application.source_url)
    for key, values in parse_qs(parsed.query).items():
        if key.casefold() in _IDENTIFIER_QUERY_KEYS:
            requisitions.update(_valid_requisition(value) for value in values)
    return canonical, {value for value in requisitions if value}


def _tokens(value: str) -> set[str]:
    return set(_TOKEN.findall(value.casefold()))


def _role_tokens(value: str) -> set[str]:
    return _tokens(value) - _ROLE_STOPWORDS


def _applications_fingerprint(applications: Sequence[Application]) -> str:
    payload = [
        {
            "company": item.company.casefold(),
            "created_at": item.created_at.isoformat(),
            "id": item.id,
            "role": item.role.casefold(),
            "source_url": canonical_public_job_url(item.source_url),
            "status": item.status,
        }
        for item in sorted(applications, key=lambda value: value.id)
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _confidence(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _linked_application_ids(store: ErgaStore, event: MailEvent) -> tuple[set[str], set[str]]:
    thread_matches: set[str] = set()
    reference_matches: set[str] = set()
    events = {item.message_id: item for item in store.list_mail_events()}
    for reconciliation in store.list_mail_reconciliations():
        application_id = reconciliation.matched_application_id
        linked_event = events.get(reconciliation.message_id)
        if application_id is None or linked_event is None:
            continue
        if event.thread_id and event.thread_id == linked_event.thread_id:
            thread_matches.add(application_id)
        if set(event.reference_ids).intersection(linked_event.reference_ids):
            reference_matches.add(application_id)
    return thread_matches, reference_matches


def _rank_candidates(
    store: ErgaStore,
    event: MailEvent,
    applications: Sequence[Application],
) -> tuple[MailMatchCandidate, ...]:
    event_urls = set(event.job_urls)
    event_requisitions = set(event.requisition_ids)
    subject_tokens = _tokens(event.subject)
    subject_role_tokens = _role_tokens(event.subject)
    receipt_company_tokens = _tokens(event.company_hint)
    receipt_role_tokens = _role_tokens(event.role_hint)
    thread_matches, reference_matches = _linked_application_ids(store, event)
    candidates: list[MailMatchCandidate] = []
    for application in applications:
        predates_application = event.received_at < application.created_at
        score = 0.0
        provenance: list[str] = []
        canonical_url, application_requisitions = _application_signals(application)
        requisition_conflict = bool(
            event_requisitions
            and application_requisitions
            and event_requisitions.isdisjoint(application_requisitions)
        )
        if canonical_url and canonical_url in event_urls and not requisition_conflict:
            score += 100
            provenance.append("canonical_job_url")
        elif canonical_url and canonical_url in event_urls and requisition_conflict:
            provenance.append("conflicting_requisition_id")
        if event_requisitions.intersection(application_requisitions):
            score += 75
            provenance.append("requisition_id")
        if application.id in thread_matches:
            score += 90
            provenance.append("thread_id")
        if application.id in reference_matches:
            score += 85
            provenance.append("reference_id")
        if predates_application:
            if not {
                "canonical_job_url",
                "requisition_id",
                "thread_id",
                "reference_id",
            }.intersection(provenance):
                continue
            provenance.append("event_predates_application")
        else:
            company_tokens = _tokens(application.company)
            if receipt_company_tokens and (
                receipt_company_tokens.issubset(company_tokens)
                or company_tokens.issubset(receipt_company_tokens)
            ):
                score += 60
                provenance.append("receipt_company")
            if company_tokens and company_tokens.issubset(subject_tokens):
                score += 35
                provenance.append("company_tokens")
            domain_labels = {
                token
                for label in event.sender_domain.casefold().split(".")
                if (token := re.sub(r"[^a-z0-9]", "", label))
            }
            company_domain_tokens = {token for token in company_tokens if len(token) >= 4}
            compact_company = "".join(company_tokens)
            if len(compact_company) >= 4:
                company_domain_tokens.add(compact_company)
            if company_domain_tokens.intersection(domain_labels):
                score += 35
                provenance.append("sender_domain")
            role_tokens = _role_tokens(application.role)
            receipt_role_overlap = role_tokens.intersection(receipt_role_tokens)
            if role_tokens and receipt_role_overlap:
                score += 25 + 45 * (len(receipt_role_overlap) / len(role_tokens))
                provenance.append("receipt_role")
            role_overlap = role_tokens.intersection(subject_role_tokens)
            if role_tokens and role_overlap:
                score += 10 + 20 * (len(role_overlap) / len(role_tokens))
                provenance.append("role_tokens")
        if score > 0:
            if not predates_application:
                score += 5
                provenance.append("received_after_application")
            candidates.append(
                MailMatchCandidate(
                    application_id=application.id,
                    company=application.company,
                    role=application.role,
                    score=round(score, 2),
                    confidence=_confidence(score),
                    provenance=tuple(provenance),
                )
            )
    return tuple(sorted(candidates, key=lambda item: (-item.score, item.application_id))[:5])


def _review_id(message_id: str) -> str:
    return f"mail_review_{hashlib.sha256(message_id.encode('utf-8')).hexdigest()[:20]}"


def _latest_mail_transition_at(store: ErgaStore, application_id: str) -> datetime | None:
    observed: list[datetime] = []
    for audit in store.audit_events():
        if (
            audit.action != "application.status_updated_from_mail"
            or audit.subject_id != application_id
        ):
            continue
        value = audit.payload.get("event_received_at")
        if isinstance(value, str):
            try:
                observed.append(datetime.fromisoformat(value))
            except ValueError:
                continue
    return max(observed) if observed else None


def _transition_guard(
    store: ErgaStore,
    application: Application,
    event: MailEvent,
    *,
    explicit_review: bool,
) -> str:
    target = _TARGET_STATUS.get(event.kind)
    if target is None:
        return "non_status_event"
    if event.received_at < application.created_at:
        return "event_predates_application"
    if not explicit_review and event.requires_review:
        return "classification_requires_review"
    if not explicit_review and event.confidence < 0.9:
        return "classification_confidence_too_low"
    if application.status == target:
        return "already_at_target"
    if application.status in _TERMINAL_STATUSES:
        return "application_is_terminal"
    latest_mail = _latest_mail_transition_at(store, application.id)
    if latest_mail is not None and event.received_at <= latest_mail:
        return "older_than_latest_mail_transition"
    manual_updates = [
        audit.created_at
        for audit in store.audit_events()
        if audit.action == "application.status_updated" and audit.subject_id == application.id
    ]
    if manual_updates and max(manual_updates) > event.received_at:
        return "older_than_manual_status_update"
    current_progress = _STATUS_PROGRESS.get(application.status)
    target_progress = _STATUS_PROGRESS.get(target)
    if (
        current_progress is not None
        and target_progress is not None
        and target_progress < current_progress
    ):
        return "status_regression_prevented"
    if event.kind == "application.acknowledgement" and application.status != "draft":
        return "acknowledgement_requires_draft"
    return "transition_allowed"


def _apply_transition(
    store: ErgaStore,
    application: Application,
    event: MailEvent,
    *,
    explicit_review: bool,
) -> tuple[bool, str]:
    reason = _transition_guard(store, application, event, explicit_review=explicit_review)
    if reason != "transition_allowed":
        return False, reason
    store.update_application_status_from_mail(
        application.id,
        status=_TARGET_STATUS[event.kind],
        event=event,
    )
    return True, "status_transitioned"


def reconcile_mail_events(
    store: ErgaStore, events: Sequence[MailEvent]
) -> MailReconciliationSummary:
    """Retry every retained event against current applications using deterministic signals."""
    applications = store.list_applications()
    fingerprint = _applications_fingerprint(applications)
    counts = {"matched": 0, "review": 0, "unmatched": 0, "ignored": 0}
    transitions = 0
    automatic_matches: list[tuple[MailEvent, Application, MailReconciliation]] = []
    for event in sorted(events, key=lambda item: (item.received_at, item.message_id)):
        existing = store.get_mail_reconciliation(_review_id(event.message_id))
        if existing is not None and (
            existing.state == "resolved"
            or (existing.state == "ignored" and existing.reason == "explicitly_ignored")
        ):
            counts[existing.state if existing.state == "ignored" else "matched"] += 1
            continue
        candidates = _rank_candidates(store, event, applications)
        top = candidates[0] if candidates else None
        runner_up = candidates[1] if len(candidates) > 1 else None
        target = _TARGET_STATUS.get(event.kind)
        future_timestamp = event.received_at > datetime.now(UTC) + _MAX_PROVIDER_CLOCK_SKEW
        uniquely_high = bool(
            top is not None
            and top.confidence == "high"
            and (runner_up is None or top.score - runner_up.score >= 20)
        )
        automatic_identity = bool(
            top is not None
            and "conflicting_requisition_id" not in top.provenance
            and (
                {"canonical_job_url", "thread_id", "reference_id"}.intersection(top.provenance)
                or (
                    "requisition_id" in top.provenance
                    and {"company_tokens", "sender_domain"}.intersection(top.provenance)
                )
            )
        )
        matched_application_id: str | None = None
        score = top.score if top else 0.0
        confidence = top.confidence if top else "low"
        provenance = top.provenance if top else ()
        if target is None:
            state = "ignored"
            action = "none"
            reason = "non_status_event"
        elif future_timestamp:
            state = "review"
            action = "confirm_match_or_ignore"
            reason = "provider_timestamp_in_future"
        elif not candidates:
            state = "unmatched"
            action = "retry_after_application_change"
            reason = "no_candidate_signals"
        elif (
            not uniquely_high
            or not automatic_identity
            or event.requires_review
            or event.confidence < 0.9
            or (top is not None and "event_predates_application" in top.provenance)
        ):
            state = "review"
            action = "confirm_match_or_ignore"
            if top is not None and "event_predates_application" in top.provenance:
                reason = "event_predates_application"
            elif event.requires_review:
                reason = "classification_requires_review"
            elif event.confidence < 0.9:
                reason = "classification_confidence_too_low"
            else:
                reason = "ambiguous_candidates"
        else:
            assert top is not None
            application = next(item for item in applications if item.id == top.application_id)
            state = "matched"
            action = "none"
            matched_application_id = application.id
            reason = "automatic_match_pending_transition"
        persisted_state = "automatic_pending" if state == "matched" else state
        reconciliation = MailReconciliation(
            id=_review_id(event.message_id),
            message_id=event.message_id,
            event_kind=event.kind,
            event_received_at=event.received_at,
            state=persisted_state,
            matched_application_id=matched_application_id,
            score=score,
            confidence=confidence,
            candidates=candidates,
            provenance=provenance,
            recommended_action=action,
            reason=reason,
            application_fingerprint=fingerprint,
            updated_at=datetime.now(UTC),
        )
        persisted = store.upsert_mail_reconciliation(reconciliation)
        if (
            persisted.state == "automatic_pending"
            and persisted.matched_application_id == matched_application_id
            and matched_application_id is not None
        ):
            automatic_matches.append((event, application, persisted))
        counts[state] += 1
    for event, application, reconciliation in sorted(
        automatic_matches,
        key=lambda item: (item[0].received_at, item[0].message_id),
        reverse=True,
    ):
        current = next(item for item in store.list_applications() if item.id == application.id)
        guard = _transition_guard(store, current, event, explicit_review=False)
        if guard != "transition_allowed":
            store.upsert_mail_reconciliation(
                replace(
                    reconciliation,
                    state="matched",
                    reason=guard,
                    updated_at=datetime.now(UTC),
                )
            )
            continue
        try:
            resolved = store.resolve_mail_reconciliation_match(
                reconciliation.id,
                application_id=application.id,
                target_status=_TARGET_STATUS[event.kind],
                automatic=True,
            )
        except ValueError:
            # An explicit review decision won the database transaction. It is authoritative and
            # the stale automatic candidate must not mutate another application.
            continue
        transitions += int(resolved.reason == "automatic_match_status_transitioned")
    return MailReconciliationSummary(
        matched=counts["matched"],
        review=counts["review"],
        unmatched=counts["unmatched"],
        ignored=counts["ignored"],
        transitions=transitions,
        application_fingerprint=fingerprint,
    )


def pending_mail_reviews(store: ErgaStore) -> list[MailReconciliation]:
    """Return only actionable ambiguity; unmatched events retry silently in the background."""
    return [item for item in store.list_mail_reconciliations() if item.state == "review"]


def resolve_mail_reconciliation(
    store: ErgaStore,
    review_id: str,
    *,
    action: str,
    application_id: str = "",
) -> MailReconciliation:
    """Apply one explicit local review choice; never sends or mutates remote mail."""
    reconciliation = store.get_mail_reconciliation(review_id)
    if reconciliation is None:
        raise ValueError("mail reconciliation review does not exist")
    normalized_action = action.strip().casefold()
    if normalized_action == "ignore":
        return store.resolve_mail_reconciliation_record(
            review_id,
            state="ignored",
            application_id=None,
            reason="explicitly_ignored",
        )
    if normalized_action != "match":
        raise ValueError("mail reconciliation action must be match or ignore")
    selected = application_id.strip()
    candidate_ids = {item.application_id for item in reconciliation.candidates}
    if selected not in candidate_ids:
        raise ValueError("application must be one of this review's candidates")
    if reconciliation.state in {"resolved", "ignored"}:
        if reconciliation.state == "resolved" and reconciliation.matched_application_id == selected:
            return reconciliation
        raise ValueError("mail reconciliation was already resolved differently")
    target = _TARGET_STATUS.get(reconciliation.event_kind)
    if target is None:
        raise ValueError("mail reconciliation event does not map to an application status")
    return store.resolve_mail_reconciliation_match(
        review_id,
        application_id=selected,
        target_status=target,
    )


def mail_reconciliation_card(reconciliation: MailReconciliation) -> CardView:
    """Render one client-neutral ambiguity review without private message content."""
    fields = tuple(
        CardField(
            f"{index}. {candidate.company} — {candidate.role}",
            f"{candidate.confidence.title()} confidence · {candidate.score:.0f} · "
            f"{', '.join(candidate.provenance)}",
        )
        for index, candidate in enumerate(reconciliation.candidates, start=1)
    ) or (CardField("Candidates", "No current application matches."),)
    actions = tuple(
        CardAction(
            f"mail.reconcile.match:{candidate.application_id}",
            f"Match {candidate.company}",
            "Confirm this local application and apply the guarded status transition.",
            "primary",
        )
        for candidate in reconciliation.candidates
    ) + (
        CardAction(
            "mail.reconcile.ignore",
            "Ignore",
            "Keep this message out of application tracking.",
            "secondary",
        ),
    )
    return CardView(
        title="Recruiting inbox review",
        summary=(
            f"{reconciliation.event_kind} · "
            f"{reconciliation.event_received_at.date().isoformat()} · "
            f"{reconciliation.reason.replace('_', ' ')}"
        ),
        fields=fields,
        actions=actions,
        footer="Private local review — no email is sent and no remote mailbox is changed",
    )
