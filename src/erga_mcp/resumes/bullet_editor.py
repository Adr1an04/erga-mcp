from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from erga_mcp.resumes.artifacts import latex_to_text, resume_item_texts

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_METRIC = re.compile(
    r"(?<![A-Za-z])(?:\$)?\d[\d,.]*(?:[kmb](?:/(?:wk|mo|year))?|"
    r"/(?:wk|mo|year)|\+|%|ms|hz|x|st|nd|rd|th)?",
    re.I,
)
_ACTION_WORDS = (
    "achieved",
    "architected",
    "automated",
    "built",
    "constructed",
    "contributed",
    "created",
    "delivered",
    "deployed",
    "designed",
    "developed",
    "directed",
    "drove",
    "engineered",
    "established",
    "fixed",
    "grew",
    "hardened",
    "implemented",
    "improved",
    "increased",
    "integrated",
    "launched",
    "led",
    "migrated",
    "optimized",
    "orchestrated",
    "placed",
    "prevented",
    "produced",
    "reduced",
    "refactored",
    "resolved",
    "scaled",
    "shipped",
    "standardized",
    "streamlined",
    "validated",
    "won",
)
_ACTION = re.compile(rf"^(?:{'|'.join(_ACTION_WORDS)})\b", re.I)
_CLAUSE_ACTION = re.compile(rf"^\s*(?:{'|'.join(_ACTION_WORDS)})\b", re.I)
_OUTCOME = re.compile(
    r"\b(?:achiev(?:ed|ing)|accelerat(?:ed|ing)|cut(?:ting)?|decreas(?:ed|ing)|"
    r"eliminat(?:ed|ing)|enabl(?:ed|ing)|improv(?:ed|ing)|increas(?:ed|ing)|"
    r"prevent(?:ed|ing)|reduc(?:ed|ing)|sav(?:ed|ing)|speed(?:ing)?|won|placed|"
    r"restor(?:ed|ing)|stabiliz(?:ed|ing))\b",
    re.I,
)
_SCOPE = re.compile(
    r"\b(?:applications?|attendees?|businesses?|customers?|developers?|devices?|"
    r"environments?|events?|members?|models?|organizations?|partners?|projects?|"
    r"recordings?|records?|requests?|routes?|services?|submissions?|teams?|users?|"
    r"workflows?)\b",
    re.I,
)
_TECHNICAL = re.compile(
    r"\b(?:ai|api|apis|aws|azure|c#|c\+\+|ci/cd|cloud|cnn|cuda|database|docker|"
    r"etl|fastapi|gitlab|graphql|grpc|java|javascript|kubernetes|lidar|linux|ml|"
    r"next\.js|postgres|prometheus|python|pytorch|react|redis|ros2|rust|slam|sql|"
    r"sqlite|schema|tensorflow|terraform|tests?|testing|trpc|typescript|uml|yaml)\b",
    re.I,
)
_FUNCTIONAL_PROOF = re.compile(
    r"\b(?:authenticated|authentication|cache|caching|edge cases?|failure handling|"
    r"health checks?|incident detection|regression|release cycles?|request failures?|"
    r"retries|retry handling|schema validation|test cases?|tests?|validation)\b",
    re.I,
)
_LOW_SIGNAL = re.compile(
    r"\b(?:assisted with|helped with|responsible for|worked on|various (?:features|tasks)|"
    r"multiple tasks|added code|made improvements)\b",
    re.I,
)
_ACTIVITY_ACCOUNTING = re.compile(
    r"\b(?:commits?|pull requests?|files?|lines (?:added|changed|deleted)|code churn)\b",
    re.I,
)
_VAGUE = re.compile(
    r"\b(?:higher-value work|"
    r"(?:improved|enhanced|better)\s+(?:[a-z-]+\s+){0,2}(?:performance|efficiency|reliability)|"
    r"streamlined operations|seamless(?:ly)?|robust|scalable|user-friendly|safely|"
    r"significantly|substantially|various|multiple)\b",
    re.I,
)
_GENERIC_ENDING = re.compile(
    r"\b(?:for (?:business|engineering|research|users?|teams?|operations)|"
    r"to improve (?:performance|efficiency|operations)|at scale)\.?$",
    re.I,
)
_FIRST_PERSON = re.compile(r"\b(?:I|me|my|mine|we|our|ours)\b", re.I)
_VERSION_CONTEXT = re.compile(
    r"(?:java|javascript|node(?:\.js)?|python|react|rust|typescript)\s*$", re.I
)
_DIRECTIONAL_ACTIONS = frozenset(
    {
        "achieved",
        "grew",
        "improved",
        "increased",
        "placed",
        "prevented",
        "reduced",
        "resolved",
        "won",
    }
)
_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "using",
        "via",
        "with",
    }
)


@dataclass(frozen=True)
class BulletIssue:
    code: str
    severity: str
    message: str
    repair: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BulletStructure:
    action: str | None
    clauses: tuple[str, ...]
    metrics: tuple[str, ...]
    technical_terms: tuple[str, ...]
    scope_terms: tuple[str, ...]
    outcome_terms: tuple[str, ...]
    functional_signals: tuple[str, ...]
    has_method: bool
    has_outcome: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BulletEditorialReport:
    text: str
    passed: bool
    score: int
    structure: BulletStructure
    strengths: tuple[str, ...]
    issues: tuple[BulletIssue, ...]
    supported_metrics_not_used: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def repair_brief(self) -> str:
        if self.passed and not self.issues:
            return "No editorial repair required."
        return "; ".join(f"{item.code}: {item.repair}" for item in self.issues)


@dataclass(frozen=True)
class ResumeEditorialReport:
    passed: bool
    average_score: int
    bullets: tuple[BulletEditorialReport, ...]
    duplicate_lead_verbs: tuple[str, ...]
    issue_counts: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _issue(code: str, severity: str, message: str, repair: str) -> BulletIssue:
    return BulletIssue(code=code, severity=severity, message=message, repair=repair)


def _unique_matches(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0).casefold() for match in pattern.finditer(text)))


def _meaningful_metrics(text: str) -> tuple[str, ...]:
    metrics: list[str] = []
    for match in _METRIC.finditer(text):
        token = match.group(0)
        numeric = token.removeprefix("$").replace(",", "").rstrip("+%x")
        numeric = re.sub(r"(?:st|nd|rd|th)$", "", numeric, flags=re.I)
        try:
            number = float(numeric.split("/", 1)[0].rstrip("kmb"))
        except ValueError:
            number = -1
        if token.isdigit() and 1900 <= number <= 2099:
            continue
        if _VERSION_CONTEXT.search(text[max(0, match.start() - 20) : match.start()]):
            continue
        metrics.append(token)
    return tuple(dict.fromkeys(metrics))


def analyze_bullet_editorially(
    text: str,
    *,
    supporting_text: str = "",
    minimum_characters: int = 0,
    maximum_characters: int = 0,
) -> BulletEditorialReport:
    """Parse and grade a résumé bullet without asking a model to judge its own prose."""
    normalized = " ".join(text.split()).strip(" •-")
    words = _WORD.findall(normalized)
    action_match = _ACTION.match(normalized)
    action = action_match.group(0) if action_match is not None else None
    clauses = tuple(part.strip() for part in normalized.split(";") if part.strip())
    metrics = _meaningful_metrics(normalized)
    technical_terms = _unique_matches(_TECHNICAL, normalized)
    scope_terms = _unique_matches(_SCOPE, normalized)
    outcome_source = normalized[action_match.end() :] if action_match is not None else normalized
    outcome_terms = _unique_matches(_OUTCOME, outcome_source)
    functional_signals = _unique_matches(_FUNCTIONAL_PROOF, normalized)
    vague_match = _VAGUE.search(normalized)
    directional_action = action is not None and action.casefold() in _DIRECTIONAL_ACTIONS
    has_outcome = bool(outcome_terms or (directional_action and vague_match is None))
    structure = BulletStructure(
        action=action,
        clauses=clauses,
        metrics=metrics,
        technical_terms=technical_terms,
        scope_terms=scope_terms,
        outcome_terms=outcome_terms,
        functional_signals=functional_signals,
        has_method=(
            bool(technical_terms)
            or bool(functional_signals)
            or re.search(r"\b(?:by|through|using|via)\b", normalized, re.I) is not None
        ),
        has_outcome=has_outcome,
    )
    issues: list[BulletIssue] = []
    if not normalized:
        issues.append(
            _issue("content.empty", "error", "The bullet is empty.", "Write one factual claim.")
        )
    if action is None:
        issues.append(
            _issue(
                "action.missing",
                "error",
                "The bullet does not open with a concrete action.",
                "Begin with a precise past-tense action supported by the cited evidence.",
            )
        )
    meaningful = [word for word in words[1:] if word.casefold() not in _STOP]
    if len(meaningful) < 4:
        issues.append(
            _issue(
                "specificity.thin",
                "error",
                "The bullet lacks a concrete object and differentiating detail.",
                "Name what changed plus the system, workflow, audience, or engineering layer.",
            )
        )
    if _LOW_SIGNAL.search(normalized):
        issues.append(
            _issue(
                "content.low_signal",
                "error",
                "The bullet describes participation rather than ownership.",
                "Replace participation language with the exact supported contribution.",
            )
        )
    if _ACTIVITY_ACCOUNTING.search(normalized):
        issues.append(
            _issue(
                "proof.activity_accounting",
                "error",
                "Repository activity is not user or engineering impact.",
                "Use the implemented behavior, verified scope, or approved outcome instead.",
            )
        )
    if len(clauses) > 1 and any(_CLAUSE_ACTION.match(clause) for clause in clauses[1:]):
        issues.append(
            _issue(
                "cohesion.tacked_claim",
                "error",
                "The bullet joins separate accomplishments with a semicolon.",
                "Keep one accomplishment and its result; move the other claim to its own bullet.",
            )
        )
    if len(metrics) >= 4 or (len(metrics) >= 3 and len(technical_terms) >= 3):
        issues.append(
            _issue(
                "readability.list_heavy",
                "warning",
                "The bullet stacks too many figures or implementation details.",
                "Keep the strongest proof point and only the details needed to explain it.",
            )
        )
    if not structure.has_method and not has_outcome and not functional_signals:
        issues.append(
            _issue(
                "implementation.missing",
                "warning",
                "The bullet does not explain how the result was produced.",
                "Add one supported method, technology, or engineering decision.",
            )
        )
    if not scope_terms and not metrics:
        issues.append(
            _issue(
                "scope.missing",
                "warning",
                "The size or boundary of the work is unclear.",
                "Add supported system, audience, workload, or organizational scope.",
            )
        )
    if not has_outcome and not metrics and not functional_signals:
        issues.append(
            _issue(
                "outcome.missing",
                "warning",
                "The bullet has no result or concrete proof point.",
                "Use a supported outcome; if none exists, emphasize precise functional scope.",
            )
        )
    if vague_match is not None:
        issues.append(
            _issue(
                "language.vague",
                "warning",
                f"The phrase {vague_match.group(0)!r} is not independently verifiable.",
                "Remove it or replace it with a supported audience, workflow, or outcome.",
            )
        )
    if _GENERIC_ENDING.search(normalized):
        issues.append(
            _issue(
                "ending.generic",
                "warning",
                "The ending does not add specific impact or context.",
                "End on the supported result, affected scope, or distinctive implementation.",
            )
        )
    if _FIRST_PERSON.search(normalized):
        issues.append(
            _issue(
                "voice.first_person",
                "error",
                "Résumé bullets should omit first-person pronouns.",
                "Remove the pronoun and keep the action-led construction.",
            )
        )
    if maximum_characters and len(normalized) > maximum_characters:
        issues.append(
            _issue(
                "length.maximum",
                "error",
                "The bullet exceeds the configured hard maximum.",
                f"Shorten it to at most {maximum_characters} characters without dropping proof.",
            )
        )
    elif minimum_characters and len(normalized) < minimum_characters:
        issues.append(
            _issue(
                "length.thin",
                "warning",
                "The bullet is shorter than the preferred detail range.",
                "Add supported method, scope, or outcome detail rather than filler.",
            )
        )
    elif len(normalized) > 200:
        issues.append(
            _issue(
                "length.dense",
                "warning",
                "The bullet is difficult to scan in one pass.",
                "Remove secondary clauses and keep one accomplishment story.",
            )
        )

    score = 0
    score += 15 if action is not None else 0
    score += 15 if len(meaningful) >= 4 else 0
    score += 15 if structure.has_method else 0
    score += 15 if scope_terms or metrics else 0
    score += 15 if metrics else 0
    score += 15 if has_outcome else 0
    score += 10 if functional_signals else 0
    score += 10 if 45 <= len(normalized) <= 190 else 5 if normalized else 0
    score -= 6 * sum(item.severity == "warning" for item in issues)
    score -= 25 * sum(item.severity == "error" for item in issues)
    score = max(0, min(100, score))
    strengths: list[str] = []
    if action is not None:
        strengths.append("action-led")
    if technical_terms:
        strengths.append("implementation-specific")
    if scope_terms:
        strengths.append("scope-aware")
    if metrics:
        strengths.append("quantified")
    if has_outcome:
        strengths.append("outcome-oriented")
    supporting_metrics = _meaningful_metrics(supporting_text)
    unused_supported_metrics = tuple(item for item in supporting_metrics if item not in metrics)
    passed = score >= 70 and not any(item.severity == "error" for item in issues)
    return BulletEditorialReport(
        text=normalized,
        passed=passed,
        score=score,
        structure=structure,
        strengths=tuple(strengths),
        issues=tuple(issues),
        supported_metrics_not_used=unused_supported_metrics,
    )


def analyze_resume_editorially(
    latex_content: str,
    *,
    minimum_characters: int = 0,
    maximum_characters: int = 0,
) -> ResumeEditorialReport:
    """Parse every résumé bullet and summarize deterministic editorial defects."""
    bullets = tuple(
        analyze_bullet_editorially(
            latex_to_text(item),
            minimum_characters=minimum_characters,
            maximum_characters=maximum_characters,
        )
        for item in resume_item_texts(latex_content)
    )
    lead_counts = Counter(
        report.structure.action.casefold()
        for report in bullets
        if report.structure.action is not None
    )
    issue_counts = Counter(issue.code for report in bullets for issue in report.issues)
    return ResumeEditorialReport(
        passed=bool(bullets) and all(report.passed for report in bullets),
        average_score=(
            round(sum(report.score for report in bullets) / len(bullets)) if bullets else 0
        ),
        bullets=bullets,
        duplicate_lead_verbs=tuple(
            sorted(lead for lead, count in lead_counts.items() if count > 1)
        ),
        issue_counts=tuple(sorted(issue_counts.items())),
    )
