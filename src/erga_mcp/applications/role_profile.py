from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

RequirementKind = Literal[
    "responsibility",
    "skill",
    "experience",
    "education",
    "leadership",
    "domain",
    "general",
]
RequirementPriority = Literal["required", "preferred", "context"]

_TOKEN = re.compile(r"[a-z0-9+#.]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n+")
_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "candidate",
        "company",
        "for",
        "from",
        "have",
        "in",
        "is",
        "it",
        "job",
        "minimum",
        "of",
        "on",
        "or",
        "our",
        "qualification",
        "qualifications",
        "required",
        "role",
        "that",
        "the",
        "their",
        "this",
        "to",
        "using",
        "we",
        "will",
        "with",
        "work",
        "you",
        "your",
    }
)

# Canonical concepts are deliberately compact and auditable. Matching a concept improves ranking;
# it never establishes that a candidate possesses it and never authorizes a generated claim.
_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "accessibility": ("a11y", "accessible design", "wcag"),
    "api": ("apis", "application programming interface", "rest", "restful"),
    "artificial intelligence": ("ai", "generative ai", "llm", "large language model"),
    "backend": ("back end", "back-end", "server side", "server-side"),
    "cloud": ("aws", "azure", "gcp", "google cloud", "cloud infrastructure"),
    "collaboration": ("cross functional", "cross-functional", "stakeholder management"),
    "containers": ("containerization", "docker", "kubernetes", "k8s"),
    "continuous delivery": ("ci/cd", "cicd", "continuous deployment", "release automation"),
    "customer": ("client", "end user", "end-user", "user facing", "user-facing"),
    "data engineering": ("data pipeline", "data pipelines", "etl", "elt"),
    "databases": ("database", "postgres", "postgresql", "mysql", "sql", "nosql"),
    "distributed systems": ("distributed system", "microservices", "service oriented"),
    "frontend": ("front end", "front-end", "client side", "client-side", "web ui"),
    "go": ("golang", "go programming", "go language"),
    "incident response": ("incident management", "on call", "on-call", "production support"),
    "leadership": ("led", "leading", "mentored", "mentoring", "technical direction"),
    "machine learning": ("ml", "deep learning", "model training", "model inference"),
    "observability": ("monitoring", "telemetry", "tracing", "metrics and logging"),
    "performance": ("latency", "throughput", "optimization", "optimisation", "realtime"),
    "product": ("product development", "product engineering", "product strategy"),
    "project management": ("program management", "delivery management", "roadmap"),
    "quality": ("qa", "quality assurance", "test automation", "testing", "verification"),
    "reliability": ("resilience", "fault tolerance", "high availability", "sre"),
    "security": ("application security", "cybersecurity", "infosec", "threat modeling"),
    "service ownership": ("own services", "production ownership", "operational ownership"),
    "software development": ("software engineering", "application development"),
    "typescript": ("ts", "type script"),
    "user experience": ("ux", "user research", "usability"),
}

_ALIAS_TO_CONCEPT = {
    alias: concept for concept, aliases in _CONCEPT_ALIASES.items() for alias in (concept, *aliases)
}
_REQUIRED = re.compile(
    r"\b(?:must|required|minimum|need to|needs to|you have|you bring|qualifications?)\b",
    re.I,
)
_PREFERRED = re.compile(
    r"\b(?:preferred|ideally|nice to have|nice-to-have|bonus|plus|desirable)\b",
    re.I,
)
_LEADERSHIP = re.compile(
    r"\b(?:lead|leader|leadership|mentor|manage|manager|strategy|strategic|stakeholder)\w*\b",
    re.I,
)
_EDUCATION = re.compile(r"\b(?:degree|bachelor|master|phd|doctorate|university|college)\b", re.I)
_EXPERIENCE = re.compile(r"\b(?:\d+\+?\s+years?|experience|background|track record)\b", re.I)
_RESPONSIBILITY = re.compile(
    r"\b(?:build|create|deliver|design|develop|drive|implement|improve|lead|maintain|own|partner|"
    r"support|work with)\b",
    re.I,
)


def _normalize(value: str) -> str:
    return " ".join(_TOKEN.findall(value.casefold().replace("–", "-").replace("—", "-")))


def _stem(token: str) -> str:
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    for suffix in ("ments", "ment", "ations", "ation", "ing", "ers", "er", "ed", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def concepts_and_terms(value: str) -> frozenset[str]:
    """Return transparent lexical features plus canonical synonym concepts."""
    normalized = _normalize(value)
    tokens = {
        _stem(token)
        for token in _TOKEN.findall(normalized)
        if len(token) > 1 and token not in _STOP_WORDS and not token.isdigit()
    }
    concepts = {
        f"concept:{concept}"
        for alias, concept in _ALIAS_TO_CONCEPT.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized)
    }
    return frozenset((*tokens, *concepts))


@dataclass(frozen=True)
class JobRequirement:
    id: str
    text: str
    kind: RequirementKind
    priority: RequirementPriority
    weight: int
    features: tuple[str, ...]
    source_excerpt: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "kind": self.kind,
            "priority": self.priority,
            "weight": self.weight,
            "features": list(self.features),
            "source_excerpt": self.source_excerpt,
        }


@dataclass(frozen=True)
class RequirementMatch:
    score: int
    coverage_percent: int
    matched_requirement_ids: tuple[str, ...]
    missing_required_ids: tuple[str, ...]
    matched_features: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "coverage_percent": self.coverage_percent,
            "matched_requirement_ids": list(self.matched_requirement_ids),
            "missing_required_ids": list(self.missing_required_ids),
            "matched_features": list(self.matched_features),
        }


@dataclass(frozen=True)
class RoleProfile:
    role: str
    requirements: tuple[JobRequirement, ...]

    @property
    def required(self) -> tuple[JobRequirement, ...]:
        return tuple(item for item in self.requirements if item.priority == "required")

    def match(self, value: str) -> RequirementMatch:
        candidate_features = concepts_and_terms(value)
        matched_ids: list[str] = []
        missing_required: list[str] = []
        matched_features: set[str] = set()
        earned = 0.0
        available = 0
        for requirement in self.requirements:
            requirement_features = set(requirement.features)
            if not requirement_features:
                continue
            available += requirement.weight
            overlap = requirement_features & candidate_features
            concept_overlap = {item for item in overlap if item.startswith("concept:")}
            # One canonical concept is meaningful. Plain words require either two signals or a
            # substantial fraction of a short requirement to avoid matching boilerplate.
            threshold = 1 if concept_overlap or len(requirement_features) == 1 else 2
            if len(overlap) >= threshold:
                fraction = min(1.0, len(overlap) / max(1, min(4, len(requirement_features))))
                earned += requirement.weight * max(0.55, fraction)
                matched_ids.append(requirement.id)
                matched_features.update(overlap)
            elif requirement.priority == "required":
                missing_required.append(requirement.id)
        coverage = round(100 * earned / available) if available else 0
        # The absolute score preserves useful separation when profiles contain different numbers
        # of requirements; the normalized coverage is used for user-facing explanations.
        score = round(earned * 10 + len(matched_features))
        return RequirementMatch(
            score=score,
            coverage_percent=min(100, coverage),
            matched_requirement_ids=tuple(matched_ids),
            missing_required_ids=tuple(missing_required),
            matched_features=tuple(sorted(matched_features)),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "requirements": [item.as_dict() for item in self.requirements],
        }


def _kind(value: str, *, default: RequirementKind) -> RequirementKind:
    if _EDUCATION.search(value):
        return "education"
    if _LEADERSHIP.search(value):
        return "leadership"
    if _EXPERIENCE.search(value):
        return "experience"
    return default


def _requirement(
    text: str,
    *,
    kind: RequirementKind,
    priority: RequirementPriority,
) -> JobRequirement | None:
    cleaned = " ".join(text.strip().lstrip("-•* ").split())
    features = tuple(sorted(concepts_and_terms(cleaned)))
    if not cleaned or not features:
        return None
    weight = {"required": 5, "preferred": 3, "context": 1}[priority]
    if kind in {"responsibility", "leadership"}:
        weight += 1
    digest = hashlib.sha256(f"{kind}\0{priority}\0{_normalize(cleaned)}".encode()).hexdigest()[:12]
    return JobRequirement(
        id=f"req_{digest}",
        text=cleaned,
        kind=kind,
        priority=priority,
        weight=weight,
        features=features,
        source_excerpt=cleaned[:500],
    )


def build_role_profile(
    *,
    role: str,
    responsibilities: tuple[str, ...] = (),
    qualifications: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    highlights: tuple[str, ...] = (),
) -> RoleProfile:
    pending: list[JobRequirement] = []
    inputs: tuple[tuple[str, RequirementKind, RequirementPriority], ...] = (
        *((item, "responsibility", "context") for item in responsibilities),
        *(
            (item, "general", "preferred" if _PREFERRED.search(item) else "required")
            for item in qualifications
        ),
        *((item, "skill", "context") for item in skills),
        *((item, "general", "context") for item in highlights),
    )
    for text, default_kind, priority in inputs:
        item = _requirement(
            text,
            kind=_kind(text, default=default_kind),
            priority=priority,
        )
        if item is not None:
            pending.append(item)
    if role.strip():
        role_item = _requirement(role, kind="domain", priority="context")
        if role_item is not None:
            pending.append(role_item)
    unique: dict[str, JobRequirement] = {}
    for item in pending:
        key = _normalize(item.text)
        existing = unique.get(key)
        if existing is None or item.weight > existing.weight:
            unique[key] = item
    return RoleProfile(role=role.strip(), requirements=tuple(unique.values()))


@lru_cache(maxsize=128)
def role_profile_from_text(job_description: str) -> RoleProfile:
    """Build a bounded profile for call sites that only have posting text."""
    chunks = [
        " ".join(item.strip().lstrip("-•* ").split()) for item in _SENTENCE.split(job_description)
    ]
    chunks = [item for item in chunks if 8 <= len(item) <= 600]
    responsibilities: list[str] = []
    qualifications: list[str] = []
    highlights: list[str] = []
    role = chunks[0][:160] if chunks else ""
    for item in chunks[:120]:
        if (
            _PREFERRED.search(item)
            or _REQUIRED.search(item)
            or _EDUCATION.search(item)
            or _EXPERIENCE.search(item)
        ):
            qualifications.append(item)
        elif _RESPONSIBILITY.search(item):
            responsibilities.append(item)
        elif len(highlights) < 12:
            highlights.append(item)
    if not responsibilities and not qualifications:
        responsibilities = chunks[:30]
    return build_role_profile(
        role=role,
        responsibilities=tuple(responsibilities[:50]),
        qualifications=tuple(qualifications[:50]),
        highlights=tuple(highlights),
    )
