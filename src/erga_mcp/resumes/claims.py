from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from erga_mcp.applications.role_profile import (
    RequirementMatch,
    RoleProfile,
    concepts_and_terms,
    role_profile_from_text,
)
from erga_mcp.models import Evidence

_CLAIM_BOUNDARY = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s+(?=[A-Z0-9])")
_METRIC = re.compile(
    r"(?<![A-Za-z])(?:\$)?\d[\d,.]*(?:\+|%|[kmbx]|\s?(?:ms|hz))?",
    re.I,
)
_EXPERIENCE = re.compile(
    r"\b(?:built|created|delivered|designed|developed|drove|implemented|improved|launched|"
    r"led|managed|optimized|owned|reduced|shipped|supported)\b",
    re.I,
)
_CLAIM_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "built",
        "created",
        "delivered",
        "designed",
        "developed",
        "for",
        "from",
        "implemented",
        "in",
        "led",
        "of",
        "on",
        "the",
        "to",
        "using",
        "with",
    }
)

# Exact, finite extraction is intentional: a skill is added to a résumé only when both the job and
# approved evidence contain one of these explicit aliases. The semantic matcher may rank broader
# concepts, but it cannot invent a skill label.
_SKILLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("C++", "language", ("c++", "cpp")),
    ("C#", "language", ("c#", "c sharp")),
    ("Go", "language", ("golang", "go programming", "go language")),
    ("Java", "language", ("java",)),
    ("JavaScript", "language", ("javascript",)),
    ("Python", "language", ("python",)),
    ("Ruby", "language", ("ruby",)),
    ("Rust", "language", ("rust",)),
    ("SQL", "language", ("sql",)),
    ("Swift", "language", ("swift",)),
    ("TypeScript", "language", ("typescript", "type script")),
    ("Axum", "framework", ("axum",)),
    ("Django", "framework", ("django",)),
    ("FastAPI", "framework", ("fastapi",)),
    ("Flask", "framework", ("flask",)),
    ("Next.js", "framework", ("next.js", "nextjs")),
    ("Node.js", "framework", ("node.js", "nodejs")),
    ("PyTorch", "framework", ("pytorch",)),
    ("React", "framework", ("react", "react.js", "reactjs")),
    ("TensorFlow", "framework", ("tensorflow",)),
    ("AWS", "platform", ("aws", "amazon web services")),
    ("Azure", "platform", ("azure",)),
    ("Docker", "platform", ("docker",)),
    ("GCP", "platform", ("gcp", "google cloud")),
    ("Git", "tool", ("git",)),
    ("GitHub Actions", "tool", ("github actions",)),
    ("Kubernetes", "platform", ("kubernetes", "k8s")),
    ("Linux", "platform", ("linux",)),
    ("PostgreSQL", "database", ("postgresql", "postgres")),
    ("Redis", "database", ("redis",)),
    ("Terraform", "platform", ("terraform",)),
)


def _contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized) for alias in aliases
    )


@dataclass(frozen=True)
class EvidenceClaim:
    id: str
    evidence_id: str
    source_ref: str
    text: str
    kind: str
    metrics: tuple[str, ...]
    skills: tuple[str, ...]
    role_match: RequirementMatch

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "evidence_id": self.evidence_id,
            "source_ref": self.source_ref,
            "text": self.text,
            "kind": self.kind,
            "metrics": list(self.metrics),
            "skills": list(self.skills),
            "role_match": self.role_match.as_dict(),
        }


@dataclass(frozen=True)
class SupportedSkill:
    name: str
    category: str
    evidence_ids: tuple[str, ...]


def _claim_kind(text: str) -> str:
    if _METRIC.search(text):
        return "achievement"
    if _EXPERIENCE.search(text):
        return "experience"
    if any(_contains_alias(text, aliases) for _, _, aliases in _SKILLS):
        return "skill"
    return "fact"


def index_evidence_claims(
    evidence: list[Evidence] | tuple[Evidence, ...],
    job_description: str,
    *,
    role_profile: RoleProfile | None = None,
) -> tuple[EvidenceClaim, ...]:
    """Split approved evidence into stable, explainable claim-sized records."""
    profile = role_profile or role_profile_from_text(job_description)
    claims: list[EvidenceClaim] = []
    for item in evidence:
        if not item.approved:
            continue
        pieces = [
            " ".join(value.strip().lstrip("-•* ").split())
            for value in _CLAIM_BOUNDARY.split(item.text)
        ]
        pieces = [value for value in pieces if value]
        if not pieces:
            pieces = [" ".join(item.text.split())]
        for index, text in enumerate(pieces[:200]):
            digest = hashlib.sha256(f"{item.id}\0{index}\0{text}".encode()).hexdigest()[:16]
            skills = tuple(name for name, _, aliases in _SKILLS if _contains_alias(text, aliases))
            claims.append(
                EvidenceClaim(
                    id=f"claim_{digest}",
                    evidence_id=item.id,
                    source_ref=item.source_ref,
                    text=text,
                    kind=_claim_kind(text),
                    metrics=tuple(_METRIC.findall(text)),
                    skills=skills,
                    role_match=profile.match(text),
                )
            )
    return tuple(
        sorted(
            claims,
            key=lambda item: (
                -item.role_match.score,
                item.evidence_id,
                item.id,
            ),
        )
    )


def supported_role_skills(
    evidence: list[Evidence] | tuple[Evidence, ...],
    job_description: str,
) -> tuple[SupportedSkill, ...]:
    """Return only skills explicitly present in both the posting and approved evidence."""
    approved = tuple(item for item in evidence if item.approved)
    supported: list[SupportedSkill] = []
    for name, category, aliases in _SKILLS:
        if not _contains_alias(job_description, aliases):
            continue
        evidence_ids = tuple(item.id for item in approved if _contains_alias(item.text, aliases))
        if evidence_ids:
            supported.append(
                SupportedSkill(
                    name=name,
                    category=category,
                    evidence_ids=evidence_ids,
                )
            )
    return tuple(supported)


def manual_claim_support_report(
    claims: tuple[str, ...],
    evidence: list[Evidence] | tuple[Evidence, ...],
) -> tuple[dict[str, object], ...]:
    """Map every manually authored bullet to one materially supporting evidence record."""
    approved = tuple(item for item in evidence if item.approved)
    reports: list[dict[str, object]] = []
    for claim in claims:
        claim_features = {
            item
            for item in concepts_and_terms(claim)
            if item.startswith("concept:") or item not in _CLAIM_STOP
        }
        claim_metrics = set(_METRIC.findall(claim))
        claim_skills = {name for name, _, aliases in _SKILLS if _contains_alias(claim, aliases)}
        best_coverage = 0.0
        best_evidence: Evidence | None = None
        best_overlap: tuple[str, ...] = ()
        for item in approved:
            evidence_features = concepts_and_terms(item.text)
            overlap = claim_features & evidence_features
            coverage = len(overlap) / max(1, len(claim_features))
            if any(value.startswith("concept:") for value in overlap):
                coverage = max(coverage, 0.5)
            evidence_metrics = set(_METRIC.findall(item.text))
            evidence_skills = {
                name for name, _, aliases in _SKILLS if _contains_alias(item.text, aliases)
            }
            if not claim_metrics <= evidence_metrics or not claim_skills <= evidence_skills:
                continue
            if coverage > best_coverage:
                best_coverage = coverage
                best_evidence = item
                best_overlap = tuple(sorted(overlap))
        passed = best_evidence is not None and (not claim_features or best_coverage >= 0.5)
        reports.append(
            {
                "claim": claim,
                "passed": passed,
                "evidence_id": best_evidence.id if best_evidence is not None else None,
                "coverage_percent": round(best_coverage * 100),
                "matched_features": list(best_overlap),
                "metrics": sorted(claim_metrics),
                "skills": sorted(claim_skills),
            }
        )
    return tuple(reports)
