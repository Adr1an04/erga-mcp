from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Protocol

from .resume import latex_to_text, resume_item_texts

_NUMBER = re.compile(r"(?<![A-Za-z])(?:\$)?\d[\d,.]*(?:\+|%|[kmb]|ms|hz|x|/year)?", re.I)
_WORD = re.compile(r"[a-z][a-z0-9+#.\-]*", re.I)
_ACTION_VERB = re.compile(
    r"^(?:architected|automated|built|constructed|created|delivered|deployed|designed|"
    r"developed|engineered|established|implemented|integrated|launched|optimized|"
    r"orchestrated|produced|refactored|scaled|shipped|streamlined|validated)\b",
    re.I,
)
_ACTIVITY_ACCOUNTING = re.compile(
    r"\b(?:commits?|pull requests?|files?|lines?|languages?)\b|\bcode churn\b",
    re.I,
)
_LOW_SIGNAL = re.compile(
    r"\b(?:added code|code added|helped with|responsible for|worked on|"
    r"various (?:features|tasks)|multiple tasks)\b",
    re.I,
)
_OUTCOME = re.compile(
    r"\b(?:accelerat(?:ed|ing)|achiev(?:ed|ing)|cut|decreas(?:ed|ing)|eliminat(?:ed|ing)|"
    r"enabl(?:ed|ing)|improv(?:ed|ing)|increas(?:ed|ing)|placed|prevent(?:ed|ing)|"
    r"reduc(?:ed|ing)|sav(?:ed|ing)|scaled|speedup|support(?:ed|ing)|won)\b",
    re.I,
)
_TECHNICAL = re.compile(
    r"\b(?:api|apis|async|authentication|autograd|aws|axum|c\+\+|cache|ci|cli|cloud|"
    r"compiler|cuda|database|docker|fastapi|gpu|graphql|grpc|inference|java|javascript|"
    r"kubernetes|latency|lidar|linux|llm|ml|model|models|next\.js|pipeline|postgres|"
    r"python|pytorch|react|redis|robotics|ros2|rust|sensor|sensors|slam|sql|streaming|"
    r"system|systems|tauri|tensorflow|testing|typescript|websocket|workflow)\b",
    re.I,
)
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "built",
        "by",
        "created",
        "developed",
        "engineered",
        "for",
        "from",
        "implemented",
        "in",
        "into",
        "of",
        "on",
        "optimized",
        "project",
        "projects",
        "service",
        "services",
        "supporting",
        "the",
        "through",
        "to",
        "using",
        "with",
    }
)

_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "adoption",
        re.compile(r"\b(?:customers?|daily active|downloads?|retention|users?)\b", re.I),
    ),
    (
        "performance",
        re.compile(
            r"\b(?:faster|fps|gflops?|latency|memory|ms|speedup|throughput|x speed|hz)\b",
            re.I,
        ),
    ),
    (
        "organizational scope",
        re.compile(
            r"\b(?:chapters?|collaborators?|contributors?|members?|organizations?|partners?|"
            r"teams?)\b",
            re.I,
        ),
    ),
    (
        "reliability",
        re.compile(
            r"\b(?:accuracy|coverage|errors?|failures?|reliability|uptime|validation)\b",
            re.I,
        ),
    ),
    (
        "delivery",
        re.compile(
            r"\b(?:deployments?|hours?|minutes?|releases?|saved|weeks?|workflows?)\b",
            re.I,
        ),
    ),
    (
        "competition",
        re.compile(r"\b(?:award|finalist|hackathon|placed|ranked|won)\b", re.I),
    ),
    (
        "functional scope",
        re.compile(
            r"\b(?:commands?|endpoints?|health checks?|integrations?|pipelines?|routes?|"
            r"suites?|tests?|workflows?)\b",
            re.I,
        ),
    ),
    (
        "scale",
        re.compile(
            r"\b(?:apis?|devices?|events?|gpus?|jobs?|models?|records?|requests?|sensors?|"
            r"submissions?|tokens?|transactions?)\b",
            re.I,
        ),
    ),
)

_NARRATIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "real-time / robotics",
        re.compile(
            r"\b(?:autonomous|lidar|navigation|real[ -]?time|robotics?|ros2|sensor|slam)\b",
            re.I,
        ),
    ),
    (
        "systems / infrastructure",
        re.compile(
            r"(?:\bc\+\+(?!\w)|\b(?:cloud|compiler|cuda|distributed|gpu|infrastructure|linux|"
            r"memory|network|rust|systems?)\b)",
            re.I,
        ),
    ),
    (
        "machine learning / AI",
        re.compile(
            r"\b(?:agentic|ai|autograd|inference|learning|llm|ml|models?|pytorch|tensorflow)\b",
            re.I,
        ),
    ),
    (
        "developer tooling",
        re.compile(
            r"\b(?:cli|compiler|developer tools?|open[ -]?source|sdk|testing|tooling)\b",
            re.I,
        ),
    ),
    (
        "product / adoption",
        re.compile(r"\b(?:customers?|members?|product|retention|users?)\b", re.I),
    ),
    (
        "web / platform",
        re.compile(
            r"\b(?:api|fastapi|full[ -]?stack|next\.js|platform|react|service|typescript|web)\b",
            re.I,
        ),
    ),
    (
        "scale / data",
        re.compile(
            r"\b(?:data|distributed|events?|processing|records?|requests?|scale|transactions?)\b",
            re.I,
        ),
    ),
    (
        "reliability / quality",
        re.compile(
            r"\b(?:accuracy|coverage|failure|reliability|testing|tests?|uptime|validation)\b",
            re.I,
        ),
    ),
    (
        "delivery / automation",
        re.compile(r"\b(?:automation|ci|deploy|deployment|release|shipped|workflow)\b", re.I),
    ),
    (
        "leadership / collaboration",
        re.compile(
            r"\b(?:chapters?|collaborated|contributors?|led|members?|mentored|organizations?|"
            r"partners?|teams?)\b",
            re.I,
        ),
    ),
    (
        "security",
        re.compile(r"\b(?:authentication|authorization|oauth|privacy|security|threat)\b", re.I),
    ),
)


class ProjectLike(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def latex(self) -> str: ...

    @property
    def evidence_ids(self) -> tuple[str, ...]: ...

    @property
    def bullet_evidence_ids(self) -> tuple[tuple[str, ...], ...]: ...

    @property
    def tags(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class BulletQuality:
    text: str
    score: int
    evidence_tier: str
    metric_categories: tuple[str, ...]
    technical_terms: tuple[str, ...]
    narrative_signals: tuple[str, ...]
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectIdentityProfile:
    project_id: str
    title: str
    technologies: tuple[str, ...]
    narrative_signals: tuple[str, ...]
    metric_categories: tuple[str, ...]
    identity_terms: tuple[str, ...]
    quality_score: int
    evidence_tier: str
    bullet_scores: tuple[BulletQuality, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectProfileComparison:
    left_project_id: str
    right_project_id: str
    overlap_score: int
    differentiation_score: int
    shared_narrative_signals: tuple[str, ...]
    shared_metric_categories: tuple[str, ...]
    left_only_metric_categories: tuple[str, ...]
    right_only_metric_categories: tuple[str, ...]
    differentiating_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioQualityReport:
    project_profiles: tuple[ProjectIdentityProfile, ...]
    pairwise_comparisons: tuple[ProjectProfileComparison, ...]
    average_quality_score: int
    differentiation_score: int
    distinct_narrative_count: int
    repeated_metric_categories: tuple[str, ...]
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _metric_categories(text: str) -> tuple[str, ...]:
    if _NUMBER.search(text) is None:
        return ()
    return tuple(label for label, pattern in _METRIC_PATTERNS if pattern.search(text))


def _narrative_signals(text: str) -> tuple[str, ...]:
    return tuple(label for label, pattern in _NARRATIVE_PATTERNS if pattern.search(text))


def _identity_terms(text: str) -> frozenset[str]:
    return frozenset(
        token.casefold().strip(".-")
        for token in _WORD.findall(text)
        if len(token.strip(".-")) > 1
        and token.casefold().strip(".-") not in _STOP_WORDS
        and not token.isdigit()
    )


def analyze_bullet_quality(text: str, *, evidence_count: int) -> BulletQuality:
    normalized = " ".join(text.split())
    metric_categories = _metric_categories(normalized)
    technical_terms = tuple(
        dict.fromkeys(match.group(0).casefold() for match in _TECHNICAL.finditer(normalized))
    )
    narrative_signals = _narrative_signals(normalized)
    issues: list[str] = []
    activity_accounting = _ACTIVITY_ACCOUNTING.search(normalized) is not None
    if activity_accounting:
        issues.append("activity accounting")
    if _LOW_SIGNAL.search(normalized):
        issues.append("generic low-signal wording")
    if not technical_terms and _OUTCOME.search(normalized) is None:
        issues.append("name-swap risk: no distinctive technical or outcome detail")

    if activity_accounting:
        evidence_tier = "C"
    elif any(category != "functional scope" for category in metric_categories):
        evidence_tier = "A"
    elif metric_categories or evidence_count:
        evidence_tier = "B"
    else:
        evidence_tier = "C"

    score = 0
    score += 15 if _ACTION_VERB.search(normalized) else 0
    score += 10 if evidence_count else 0
    score += 25 if metric_categories else 0
    score += min(20, 7 * len(technical_terms))
    score += 15 if _OUTCOME.search(normalized) else 0
    score += 10 if 70 <= len(normalized) <= 190 else 5 if 45 <= len(normalized) <= 220 else 0
    score += 5 if len(_identity_terms(normalized)) >= 5 else 0
    if activity_accounting:
        score -= 35
    if _LOW_SIGNAL.search(normalized):
        score -= 30
    if "name-swap risk: no distinctive technical or outcome detail" in issues:
        score -= 15
    return BulletQuality(
        text=normalized,
        score=max(0, min(100, score)),
        evidence_tier=evidence_tier,
        metric_categories=metric_categories,
        technical_terms=technical_terms,
        narrative_signals=narrative_signals,
        issues=tuple(issues),
    )


def build_project_identity_profile(candidate: ProjectLike) -> ProjectIdentityProfile:
    bullets = resume_item_texts(candidate.latex)
    bullet_scores = tuple(
        analyze_bullet_quality(
            latex_to_text(bullet),
            evidence_count=len(evidence_ids),
        )
        for bullet, evidence_ids in zip(bullets, candidate.bullet_evidence_ids, strict=False)
    )
    combined = " ".join(
        (
            candidate.title,
            " ".join(candidate.tags),
            *(item.text for item in bullet_scores),
        )
    )
    metric_categories = tuple(
        dict.fromkeys(category for item in bullet_scores for category in item.metric_categories)
    )
    narrative_signals = _narrative_signals(combined)
    evidence_tier = (
        "A"
        if any(item.evidence_tier == "A" for item in bullet_scores)
        else "B"
        if any(item.evidence_tier == "B" for item in bullet_scores)
        else "C"
    )
    quality_score = (
        round(sum(item.score for item in bullet_scores) / len(bullet_scores))
        if bullet_scores
        else 0
    )
    technologies = tuple(dict.fromkeys(tag.casefold() for tag in candidate.tags))
    return ProjectIdentityProfile(
        project_id=candidate.id,
        title=candidate.title,
        technologies=technologies,
        narrative_signals=narrative_signals,
        metric_categories=metric_categories,
        identity_terms=tuple(sorted(_identity_terms(combined))),
        quality_score=quality_score,
        evidence_tier=evidence_tier,
        bullet_scores=bullet_scores,
    )


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def bullet_semantic_overlap(left: str, right: str) -> int:
    """Measure whether two bullets communicate an interchangeable accomplishment."""
    left_terms = set(_identity_terms(left))
    right_terms = set(_identity_terms(right))
    left_metrics = set(_metric_categories(left))
    right_metrics = set(_metric_categories(right))
    return round(
        100
        * (0.8 * _jaccard(left_terms, right_terms) + 0.2 * _jaccard(left_metrics, right_metrics))
    )


def compare_project_profiles(
    left: ProjectIdentityProfile,
    right: ProjectIdentityProfile,
) -> ProjectProfileComparison:
    left_narratives = set(left.narrative_signals)
    right_narratives = set(right.narrative_signals)
    left_metrics = set(left.metric_categories)
    right_metrics = set(right.metric_categories)
    left_technologies = set(left.technologies)
    right_technologies = set(right.technologies)
    left_terms = set(left.identity_terms)
    right_terms = set(right.identity_terms)
    overlap = round(
        100
        * (
            0.35 * _jaccard(left_narratives, right_narratives)
            + 0.25 * _jaccard(left_metrics, right_metrics)
            + 0.25 * _jaccard(left_technologies, right_technologies)
            + 0.15 * _jaccard(left_terms, right_terms)
        )
    )
    differentiating_terms = tuple(
        sorted((left_terms ^ right_terms) | (left_technologies ^ right_technologies))[:12]
    )
    return ProjectProfileComparison(
        left_project_id=left.project_id,
        right_project_id=right.project_id,
        overlap_score=overlap,
        differentiation_score=100 - overlap,
        shared_narrative_signals=tuple(sorted(left_narratives & right_narratives)),
        shared_metric_categories=tuple(sorted(left_metrics & right_metrics)),
        left_only_metric_categories=tuple(sorted(left_metrics - right_metrics)),
        right_only_metric_categories=tuple(sorted(right_metrics - left_metrics)),
        differentiating_terms=differentiating_terms,
    )


def portfolio_quality_report(candidates: tuple[ProjectLike, ...]) -> PortfolioQualityReport:
    profiles = tuple(build_project_identity_profile(candidate) for candidate in candidates)
    comparisons = tuple(
        compare_project_profiles(left, right)
        for left_index, left in enumerate(profiles)
        for right in profiles[left_index + 1 :]
    )
    repeated_metrics = tuple(
        sorted(
            category
            for category, count in Counter(
                category for profile in profiles for category in set(profile.metric_categories)
            ).items()
            if count > 1
        )
    )
    issues: list[str] = []
    for comparison in comparisons:
        if comparison.overlap_score >= 60:
            issues.append(
                f"{comparison.left_project_id} and {comparison.right_project_id} have "
                f"{comparison.overlap_score}% narrative overlap"
            )
    for profile in profiles:
        if profile.quality_score < 60:
            issues.append(
                f"{profile.project_id} averages {profile.quality_score}/100 bullet quality"
            )
        if any(item.issues for item in profile.bullet_scores):
            issues.append(f"{profile.project_id} contains low-distinction bullet language")
    average_quality = (
        round(sum(profile.quality_score for profile in profiles) / len(profiles)) if profiles else 0
    )
    differentiation = (
        round(
            sum(comparison.differentiation_score for comparison in comparisons) / len(comparisons)
        )
        if comparisons
        else 100
    )
    return PortfolioQualityReport(
        project_profiles=profiles,
        pairwise_comparisons=comparisons,
        average_quality_score=average_quality,
        differentiation_score=differentiation,
        distinct_narrative_count=len(
            {signal for profile in profiles for signal in profile.narrative_signals}
        ),
        repeated_metric_categories=repeated_metrics,
        issues=tuple(dict.fromkeys(issues)),
    )
