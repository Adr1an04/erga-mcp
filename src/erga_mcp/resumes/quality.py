from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Protocol

from erga_mcp.applications.role_profile import role_profile_from_text
from erga_mcp.resumes.artifacts import latex_to_text, resume_item_texts

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
_GENERIC_ROLE_TERMS = frozenset(
    {"developer", "development", "engineer", "engineering", "intern", "internship", "software"}
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


@dataclass(frozen=True)
class ResumeMasterComparison:
    """A template-agnostic quality floor derived only from the current master resume."""

    passed: bool
    template_contract: str
    master_quality_score: int
    proposal_quality_score: int
    master_quantified_coverage: int
    proposal_quantified_coverage: int
    content_retention_percent: int
    master_duplicate_lead_verbs: tuple[str, ...]
    proposal_duplicate_lead_verbs: tuple[str, ...]
    semantic_redundancy_pairs: tuple[tuple[int, int, int], ...]
    master_role_fit_score: int
    proposal_role_fit_score: int
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectDecisionEntry:
    project_id: str
    title: str
    selected: bool
    role_relevance_score: int
    quality_score: int
    differentiation_score: int
    total_score: int
    matched_role_terms: tuple[str, ...]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ProjectDecisionReport:
    strategy: str
    selected: tuple[ProjectDecisionEntry, ...]
    alternatives: tuple[ProjectDecisionEntry, ...]
    evaluated_candidate_count: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _balanced_command_arities(source: str, command: str) -> tuple[int, ...]:
    """Return required-argument counts without assuming a particular resume template."""
    return tuple(len(arguments) for arguments in _balanced_command_arguments(source, command))


def _balanced_command_arguments(source: str, command: str) -> tuple[tuple[str, ...], ...]:
    """Return every balanced required-argument group for a LaTeX command."""
    needle = f"\\{command}"
    position = 0
    invocations: list[tuple[str, ...]] = []
    while (start := source.find(needle, position)) >= 0:
        command_end = start + len(needle)
        if command_end < len(source) and source[command_end].isalpha():
            position = command_end
            continue
        cursor = start + len(needle)
        arguments: list[str] = []
        while True:
            while cursor < len(source) and source[cursor].isspace():
                cursor += 1
            if cursor >= len(source) or source[cursor] != "{":
                break
            argument_start = cursor
            depth = 0
            while cursor < len(source):
                character = source[cursor]
                escaped = cursor > 0 and source[cursor - 1] == "\\"
                if character == "{" and not escaped:
                    depth += 1
                elif character == "}" and not escaped:
                    depth -= 1
                    if depth == 0:
                        arguments.append(source[argument_start + 1 : cursor])
                        cursor += 1
                        break
                cursor += 1
            if depth:
                break
        invocations.append(tuple(arguments))
        position = max(cursor, start + len(needle))
    return tuple(invocations)


def _argument_format_skeleton(value: str) -> str:
    """Preserve LaTeX roles and separators while masking user-editable heading text."""
    tokens: list[str] = []
    position = 0
    while position < len(value):
        character = value[position]
        if character == "\\":
            command = re.match(r"\\(?:[A-Za-z@]+\*?|.)", value[position:])
            if command is not None:
                tokens.append(command.group(0))
                position += len(command.group(0))
                continue
        if character in "{}$|&":
            tokens.append(character)
            position += 1
            continue
        if character.isspace():
            tokens.append(" ")
            position += 1
            continue
        while (
            position < len(value)
            and value[position] not in "\\{}$|&"
            and not value[position].isspace()
        ):
            position += 1
        tokens.append("#")
    return re.sub(r"(?:#\s*)+", "#", "".join(tokens)).strip()


_BODY_LAYOUT_COMMAND = re.compile(
    r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge|"
    r"raggedright|raggedleft|centering|sloppy|fussy|flushbottom|raggedbottom)\b|"
    r"\\(?:begin|end)\{(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|"
    r"LARGE|huge|Huge)\}|"
    r"\\fontfamily\s*\{[^{}]*\}\s*\\selectfont|"
    r"\\(?:vspace|hspace)\*?\s*\{[^{}]*\}|"
    r"\\(?:setlength|addtolength|fontsize)\s*\{[^{}]*\}\s*\{[^{}]*\}|"
    r"\\linespread\s*\{[^{}]*\}|\\\\(?:\s*\[[^]]*\])?"
)


def _body_layout_contract(document_body: str) -> tuple[tuple[str, str], ...]:
    """Bind body font/spacing primitives to the section where the master uses them."""
    document_body = re.sub(
        r"% ERGA-ADAPTIVE-PAGE-FILL\s*\n\s*\\(?:flushbottom|raggedbottom)\s*",
        "",
        document_body,
    )
    headings = tuple(re.finditer(r"(?m)^\\section\{([^}]+)\}", document_body))
    contract: list[tuple[str, str]] = []
    for match in _BODY_LAYOUT_COMMAND.finditer(document_body):
        line_end = document_body.find("\n", match.start())
        line = document_body[match.start() : line_end if line_end >= 0 else len(document_body)]
        if "ERGA-ADAPTIVE-PAGE-FILL" in line:
            continue
        section = next(
            (heading.group(1) for heading in reversed(headings) if heading.start() < match.start()),
            "__header__",
        )
        contract.append(
            (
                re.sub(r"[^a-z0-9]+", "", section.casefold()),
                re.sub(r"\s+", "", match.group(0)),
            )
        )
    return tuple(contract)


def _template_contract(source: str) -> tuple[object, ...]:
    sections = tuple(
        re.sub(r"[^a-z0-9]+", "", name.casefold())
        for name in re.findall(r"(?m)^\\section\{([^}]+)\}", source)
    )
    document_marker = r"\begin{document}"
    has_document = document_marker in source
    document_prefix = source.split(document_marker, 1)[0] if has_document else ""
    document_body = source.split(document_marker, 1)[1] if has_document else source
    first_section = re.search(r"(?m)^\\section\{", document_body)
    header = document_body[: first_section.start()] if first_section is not None else document_body
    header = (
        header.replace("% ERGA-ADAPTIVE-PAGE-FILL", "")
        .replace(r"\flushbottom", "")
        .replace(r"\raggedbottom", "")
    )
    header = "\n".join(line.rstrip() for line in header.splitlines() if line.strip())
    macro_names = tuple(
        re.findall(r"\\(?:newcommand|renewcommand)\*?\{?\\([A-Za-z@]+)", document_prefix)
    )
    heading_arities = _balanced_command_arities(source, "resumeProjectHeading")
    project_heading_formats = frozenset(
        tuple(_argument_format_skeleton(argument) for argument in arguments)
        for arguments in _balanced_command_arguments(document_body, "resumeProjectHeading")
    )
    heading_commands = tuple(
        sorted(
            (command, tuple(sorted(set(_balanced_command_arities(source, command)))))
            for command in set(re.findall(r"\\(resume[A-Za-z]*Heading)\b", source))
        )
    )
    list_starts = source.count(r"\resumeItemListStart")
    list_ends = source.count(r"\resumeItemListEnd")
    return (
        sections,
        document_prefix,
        header,
        macro_names,
        heading_commands,
        tuple(sorted(set(heading_arities))),
        project_heading_formats,
        _body_layout_contract(document_body),
        list_starts == list_ends,
        (list_starts == len(heading_arities)) if heading_arities else True,
    )


def _project_heading_format_contract_preserved(master: str, proposal: str) -> bool:
    master_body = master.split(r"\begin{document}", 1)[-1]
    proposal_body = proposal.split(r"\begin{document}", 1)[-1]
    master_formats = tuple(
        tuple(_argument_format_skeleton(argument) for argument in arguments)
        for arguments in _balanced_command_arguments(master_body, "resumeProjectHeading")
    )
    proposal_formats = tuple(
        tuple(_argument_format_skeleton(argument) for argument in arguments)
        for arguments in _balanced_command_arguments(proposal_body, "resumeProjectHeading")
    )
    if len(master_formats) == len(proposal_formats):
        return Counter(master_formats) == Counter(proposal_formats)
    return len(set(master_formats)) <= 1 and set(proposal_formats) <= set(master_formats)


def _plain_project_heading_formats(source: str) -> tuple[str, ...]:
    """Capture non-macro Project heading roles for simple LaTeX templates."""
    section = re.search(
        r"(?ms)^\\section\{Projects\}\s*(?P<body>.*?)(?=^\\section\{|\\end\{document\}|\Z)",
        source,
        flags=re.I,
    )
    if section is None:
        return ()
    return tuple(
        _argument_format_skeleton(line.strip())
        for line in section.group("body").splitlines()
        if r"\textbf" in line
        and r"\resumeProjectHeading" not in line
        and (
            "$|$" in line
            or r"\textbar" in line
            or r"\hfill" in line
            or r"\emph" in line
            or r"\textit" in line
        )
    )


def _plain_project_heading_contract_preserved(master: str, proposal: str) -> bool:
    master_formats = _plain_project_heading_formats(master)
    proposal_formats = _plain_project_heading_formats(proposal)
    if len(master_formats) == len(proposal_formats):
        return Counter(master_formats) == Counter(proposal_formats)
    return len(set(master_formats)) <= 1 and set(proposal_formats) <= set(master_formats)


def _lead_verb_counts(bullets: tuple[str, ...]) -> Counter[str]:
    return Counter(
        words[0].casefold() for bullet in bullets if (words := _WORD.findall(latex_to_text(bullet)))
    )


def _resume_quality_summary(source: str) -> tuple[int, int, tuple[str, ...]]:
    bullets = resume_item_texts(source)
    analyzed = tuple(analyze_bullet_quality(text, evidence_count=1) for text in bullets)
    average = round(sum(item.score for item in analyzed) / len(analyzed)) if analyzed else 0
    quantified = (
        round(100 * sum(bool(item.metric_categories) for item in analyzed) / len(analyzed))
        if analyzed
        else 0
    )
    duplicate_leads = tuple(
        sorted(lead for lead, count in _lead_verb_counts(bullets).items() if count > 1)
    )
    return average, quantified, duplicate_leads


def compare_resume_to_master(
    master_latex: str,
    proposal_latex: str,
    *,
    job_description: str = "",
) -> ResumeMasterComparison:
    """Require generated copy and structure to remain on par with its own master.

    The comparison never imports a global person's facts or a fixed template. Its baseline is the
    source used for this generation, so fonts, macros, heading arity, and density can vary by user.
    """
    master_score, master_coverage, master_duplicates = _resume_quality_summary(master_latex)
    proposal_score, proposal_coverage, proposal_duplicates = _resume_quality_summary(proposal_latex)
    contract_preserved = (
        _template_contract(master_latex) == _template_contract(proposal_latex)
        and _project_heading_format_contract_preserved(master_latex, proposal_latex)
        and _plain_project_heading_contract_preserved(master_latex, proposal_latex)
    )
    proposal_bullets = resume_item_texts(proposal_latex)
    master_bullets = resume_item_texts(master_latex)
    profile = role_profile_from_text(job_description) if job_description.strip() else None
    master_role_fit = (
        sum(profile.match(latex_to_text(item)).score for item in master_bullets)
        if profile is not None
        else 0
    )
    proposal_role_fit = (
        sum(profile.match(latex_to_text(item)).score for item in proposal_bullets)
        if profile is not None
        else 0
    )
    role_fit_improved = proposal_role_fit > master_role_fit
    inherited_redundancy = {
        tuple(
            sorted(
                (
                    " ".join(_WORD.findall(left.casefold())),
                    " ".join(_WORD.findall(right.casefold())),
                )
            )
        )
        for left_index, left in enumerate(master_bullets)
        for right in master_bullets[left_index + 1 :]
        if bullet_semantic_overlap(left, right) >= 70
    }
    redundancy = tuple(
        (left_index, right_index, overlap)
        for left_index, left in enumerate(proposal_bullets)
        for right_index, right in enumerate(proposal_bullets[left_index + 1 :], left_index + 1)
        if (overlap := bullet_semantic_overlap(left, right)) >= 70
        and tuple(
            sorted(
                (
                    " ".join(_WORD.findall(left.casefold())),
                    " ".join(_WORD.findall(right.casefold())),
                )
            )
        )
        not in inherited_redundancy
    )
    issues: list[str] = []
    if not contract_preserved:
        issues.append("proposal changes the master template contract")
    master_issue_count = sum(
        bool(item.issues)
        for item in (analyze_bullet_quality(text, evidence_count=1) for text in master_bullets)
    )
    proposal_issue_count = sum(
        bool(item.issues)
        for item in (analyze_bullet_quality(text, evidence_count=1) for text in proposal_bullets)
    )
    # A score is a coarse heuristic: small shifts from project choice or a precise action verb
    # are parity. Reject material score collapses and any lower score caused by newly introduced
    # activity-accounting or generic wording.
    if proposal_score < master_score - 15 or (
        proposal_score < master_score and proposal_issue_count > master_issue_count
    ):
        issues.append("proposal falls below master bullet quality")
    if proposal_coverage < master_coverage and not (
        role_fit_improved and proposal_score >= master_score - 5
    ):
        issues.append("proposal falls below master supported quantitative coverage")
    master_lead_counts = _lead_verb_counts(master_bullets)
    proposal_lead_counts = _lead_verb_counts(proposal_bullets)
    worsened_duplicate_leads = tuple(
        sorted(
            lead
            for lead, count in proposal_lead_counts.items()
            if count > 1 and count > master_lead_counts.get(lead, 0)
        )
    )
    if worsened_duplicate_leads:
        issues.append("proposal introduces duplicate lead verbs")
    content_retention = (
        round(100 * len(proposal_bullets) / len(master_bullets)) if master_bullets else 100
    )
    if content_retention < 70 and not (
        role_fit_improved and content_retention >= 50 and len(proposal_bullets) >= 4
    ):
        issues.append("proposal removes too much of the master resume's validated content")
    if redundancy:
        issues.append("proposal contains semantically redundant bullets")
    return ResumeMasterComparison(
        passed=not issues,
        template_contract="preserved" if contract_preserved else "changed",
        master_quality_score=master_score,
        proposal_quality_score=proposal_score,
        master_quantified_coverage=master_coverage,
        proposal_quantified_coverage=proposal_coverage,
        content_retention_percent=content_retention,
        master_duplicate_lead_verbs=master_duplicates,
        proposal_duplicate_lead_verbs=proposal_duplicates,
        semantic_redundancy_pairs=redundancy,
        master_role_fit_score=master_role_fit,
        proposal_role_fit_score=proposal_role_fit,
        issues=tuple(issues),
    )


def select_quality_project_ids(
    candidates: tuple[ProjectLike, ...],
    job_description: str,
    *,
    project_count: int,
    locked_ids: tuple[str, ...] = (),
    preferred_narratives: tuple[str, ...] = (),
    preferred_metric_categories: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Choose an evidence-eligible set by relevance, master-like quality, and contrast."""
    if project_count < 1:
        raise ValueError("project_count must be positive")
    by_id = {candidate.id: candidate for candidate in candidates}
    if locked_ids:
        if len(locked_ids) != project_count or any(item not in by_id for item in locked_ids):
            raise ValueError("locked_ids must be the exact eligible project selection")
        return locked_ids
    job_terms = _role_terms(job_description)
    role_profile = role_profile_from_text(job_description)
    sparse_generic_role = bool(job_terms) and job_terms <= _GENERIC_ROLE_TERMS
    profiles = {candidate.id: build_project_identity_profile(candidate) for candidate in candidates}
    remaining = dict(by_id)
    selected: list[str] = []
    while remaining and len(selected) < project_count:

        def score(candidate: ProjectLike) -> tuple[int, int, str]:
            profile = profiles[candidate.id]
            matched = job_terms & set(profile.identity_terms)
            requirement_match = role_profile.match(
                latex_to_text(candidate.latex) + " " + " ".join(candidate.tags)
            )
            relevance = min(100, len(matched) * 12 + requirement_match.score)
            differentiation = (
                min(
                    compare_project_profiles(profiles[item], profile).differentiation_score
                    for item in selected
                )
                if selected
                else 100
            )
            preference_bonus = min(
                10,
                3 * len(set(preferred_narratives) & set(profile.narrative_signals))
                + 2 * len(set(preferred_metric_categories) & set(profile.metric_categories)),
            )
            issue_penalty = 20 * sum(bool(item.issues) for item in profile.bullet_scores)
            total = round(
                0.5 * relevance
                + 0.3 * profile.quality_score
                + 0.2 * differentiation
                + preference_bonus
                - issue_penalty
            )
            return total, relevance, candidate.id

        choice = max(remaining.values(), key=lambda item: (score(item)[0], score(item)[1]))
        total, relevance, _ = score(choice)
        if relevance <= 0 and not sparse_generic_role:
            break
        selected.append(choice.id)
        del remaining[choice.id]
    return tuple(selected)


def _role_terms(value: str) -> frozenset[str]:
    return _identity_terms(value)


def rank_project_candidates(
    candidates: tuple[ProjectLike, ...],
    job_description: str,
    *,
    selected_ids: tuple[str, ...],
    preferred_narratives: tuple[str, ...] = (),
    preferred_metric_categories: tuple[str, ...] = (),
) -> ProjectDecisionReport:
    """Explain the complete eligible catalogue after hard evidence/layout gates have passed."""
    selected_set = frozenset(selected_ids)
    if len(selected_set) != len(selected_ids):
        raise ValueError("selected_ids must be distinct")
    candidate_ids = {candidate.id for candidate in candidates}
    if not selected_set <= candidate_ids:
        raise ValueError("selected_ids must belong to the evaluated catalogue")
    job_terms = _role_terms(job_description)
    profiles = {candidate.id: build_project_identity_profile(candidate) for candidate in candidates}
    selected_profiles = [profiles[project_id] for project_id in selected_ids]
    entries: list[ProjectDecisionEntry] = []
    for candidate in candidates:
        profile = profiles[candidate.id]
        matched = tuple(sorted(job_terms & set(profile.identity_terms)))
        relevance = min(100, len(matched) * 12)
        differentiation = (
            min(
                compare_project_profiles(profile, selected).differentiation_score
                for selected in selected_profiles
                if selected.project_id != candidate.id
            )
            if any(selected.project_id != candidate.id for selected in selected_profiles)
            else 100
        )
        preference_bonus = min(
            10,
            3 * len(set(preferred_narratives) & set(profile.narrative_signals))
            + 2 * len(set(preferred_metric_categories) & set(profile.metric_categories)),
        )
        total = round(0.5 * relevance + 0.3 * profile.quality_score + 0.2 * differentiation)
        total = min(100, total + preference_bonus)
        bullet_issues = {issue for item in profile.bullet_scores for issue in item.issues}
        reasons: list[str] = []
        if "activity accounting" in bullet_issues:
            reasons.append("activity accounting")
        if "generic low-signal wording" in bullet_issues:
            reasons.append("generic low-signal wording")
        if not matched:
            reasons.append("insufficient role specificity; ranked by approved quality and contrast")
        if candidate.id not in selected_set:
            selected_relevance = [
                len(job_terms & set(profiles[item].identity_terms)) * 12 for item in selected_ids
            ]
            if selected_relevance and relevance < max(selected_relevance):
                reasons.append("lower role relevance")
            if profile.quality_score < max(
                (profiles[item].quality_score for item in selected_ids), default=0
            ):
                reasons.append("weaker approved bullet quality")
            if differentiation < 50:
                reasons.append("duplicates the selected portfolio story")
        if candidate.id in selected_set:
            reasons.append("selected after evidence and layout eligibility gates")
        entries.append(
            ProjectDecisionEntry(
                project_id=candidate.id,
                title=candidate.title,
                selected=candidate.id in selected_set,
                role_relevance_score=relevance,
                quality_score=profile.quality_score,
                differentiation_score=differentiation,
                total_score=total,
                matched_role_terms=matched,
                reasons=tuple(dict.fromkeys(reasons)),
            )
        )
    by_id = {entry.project_id: entry for entry in entries}
    selected = tuple(by_id[project_id] for project_id in selected_ids)
    alternatives = tuple(
        sorted(
            (entry for entry in entries if not entry.selected),
            key=lambda item: (-item.total_score, item.project_id),
        )
    )
    return ProjectDecisionReport(
        strategy="master_parity_contrastive_v1",
        selected=selected,
        alternatives=alternatives,
        evaluated_candidate_count=len(candidates),
    )


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
