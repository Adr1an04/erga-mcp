from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from erga_mcp.applications.role_profile import role_profile_from_text
from erga_mcp.models import Evidence
from erga_mcp.portfolio.inventory import (
    ProjectCandidate,
    ProjectSelection,
    project_quality_issues,
    select_project_rationales,
)
from erga_mcp.resumes.artifacts import ResumeProposal, latex_to_text, resolve_section_name
from erga_mcp.resumes.bullet_editor import analyze_resume_editorially
from erga_mcp.resumes.claims import (
    SupportedSkill,
    index_evidence_claims,
    supported_role_skills,
)
from erga_mcp.resumes.quality import (
    compare_resume_to_master,
    portfolio_quality_report,
    rank_project_candidates,
    select_quality_project_ids,
)

_TOKEN = re.compile(r"[a-z0-9+#.]+")
_PAGE_FILL_MARKER = "% ERGA-ADAPTIVE-PAGE-FILL"
_VISUAL_SPACING_MARKER = "% Erga visual spacing is template-controlled."
_PAGE_FILL_SETUP = r"""
% ERGA-ADAPTIVE-PAGE-FILL
\raggedbottom
"""
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "architected",
        "are",
        "as",
        "at",
        "authored",
        "be",
        "built",
        "by",
        "created",
        "designed",
        "engineered",
        "for",
        "from",
        "has",
        "have",
        "implemented",
        "improved",
        "in",
        "is",
        "it",
        "job",
        "learn",
        "most",
        "of",
        "off",
        "on",
        "open",
        "optimized",
        "or",
        "our",
        "out",
        "projects",
        "role",
        "shipped",
        "source",
        "that",
        "the",
        "their",
        "this",
        "time",
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
_RELEVANCE_CLUSTERS = (
    frozenset({"latency", "performance", "realtime", "real", "speed", "throughput"}),
    frozenset({"c++", "systems", "linux", "low-level"}),
    frozenset({"javascript", "typescript", "html", "css", "web", "website", "frontend"}),
    frozenset({"docker", "kubernetes", "containers", "deployment", "infrastructure"}),
    frozenset(
        {
            "arduino",
            "board",
            "dsp",
            "embedded",
            "emg",
            "hardware",
            "imu",
            "jetson",
            "lidar",
            "mcu",
            "myoware",
            "processor",
            "sensor",
            "silicon",
        }
    ),
    frozenset({"pytorch", "tensorflow", "machine", "ml", "model", "inference"}),
    frozenset({"test", "testing", "pytest", "quality", "reliability"}),
)
_LEAD_VERB_ALTERNATIVES = {
    "automated": (
        "Scripted",
        "Streamlined",
        "Orchestrated",
        "Systematized",
        "Programmed",
    ),
    "architected": (
        "Designed",
        "Engineered",
        "Structured",
        "Established",
        "Created",
        "Developed",
    ),
    "authored": ("Wrote", "Produced", "Created", "Documented", "Developed", "Delivered"),
    "built": (
        "Developed",
        "Engineered",
        "Constructed",
        "Created",
        "Delivered",
        "Produced",
        "Established",
        "Implemented",
        "Assembled",
        "Introduced",
    ),
    "created": (
        "Developed",
        "Built",
        "Produced",
        "Designed",
        "Established",
        "Engineered",
        "Delivered",
        "Introduced",
    ),
    "designed": (
        "Architected",
        "Developed",
        "Created",
        "Engineered",
        "Structured",
        "Established",
        "Produced",
    ),
    "developed": (
        "Built",
        "Engineered",
        "Created",
        "Constructed",
        "Delivered",
        "Produced",
        "Established",
        "Implemented",
        "Introduced",
    ),
    "earned": ("Won", "Secured", "Captured"),
    "engineered": (
        "Developed",
        "Built",
        "Designed",
        "Created",
        "Delivered",
        "Constructed",
        "Produced",
        "Established",
    ),
    "implemented": (
        "Integrated",
        "Delivered",
        "Deployed",
        "Developed",
        "Engineered",
        "Built",
        "Created",
        "Constructed",
        "Produced",
        "Executed",
        "Completed",
        "Advanced",
        "Expanded",
        "Extended",
        "Established",
        "Added",
        "Introduced",
    ),
    "improved": (
        "Enhanced",
        "Strengthened",
        "Advanced",
        "Optimized",
        "Refined",
        "Accelerated",
        "Streamlined",
    ),
    "optimized": (
        "Improved",
        "Accelerated",
        "Streamlined",
        "Tuned",
        "Refined",
        "Enhanced",
        "Strengthened",
        "Advanced",
    ),
    "shipped": ("Delivered", "Released", "Launched", "Published", "Deployed", "Produced"),
    "standardized": (
        "Unified",
        "Codified",
        "Harmonized",
        "Consolidated",
        "Normalized",
    ),
    "won": ("Earned", "Secured", "Captured"),
}
TAILORING_VERSION = 33
_GENERATED_TEMPLATE_MARKER = "% Generated by Erga from approved master resume SHA-256:"
_SEMANTIC_TEMPLATE_MARKER = "% Erga semantic resume template version:"


def _section_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


@dataclass(frozen=True)
class AutomaticResumeProposal:
    proposal: ResumeProposal
    meaningful_change: bool
    changed_sections: tuple[str, ...]
    constraint_violations: tuple[str, ...]
    project_selection: dict[str, object]
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ResumeProjectSelectionPlan:
    """The one project plan shared by Git research and final résumé generation."""

    selected: tuple[ProjectCandidate, ...]
    rationales: tuple[ProjectSelection, ...]
    quality_rejections: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class PdfPageFill:
    """Rendered text extent for one PDF page, expressed independently of pixels or font size."""

    page_height: float
    content_top: float
    content_bottom: float
    fill_ratio: float
    text_run_count: int


@dataclass(frozen=True)
class _CommandSpan:
    start: int
    end: int
    latex: str
    content: str


@dataclass(frozen=True)
class _ProjectHeadingInvocation:
    start: int
    end: int
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class _ProjectHeadingContract:
    mode: str
    argument_count: int
    inline_separator: str = " $|$ "


@dataclass(frozen=True)
class _RankedValue:
    group_index: int
    output_group_index: int
    original_index: int
    output_index: int
    latex: str
    text: str
    score: int
    matched_terms: tuple[str, ...]


def _normalized(value: str) -> str:
    return " ".join(_TOKEN.findall(latex_to_text(value).casefold()))


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _TOKEN.findall(latex_to_text(value).casefold())
        if len(token) > 1
        and token not in _STOP_WORDS
        and any(character.isalpha() or character in "+#." for character in token)
    )


def _relevance(value: str, job_description: str) -> tuple[int, tuple[str, ...]]:
    value_text = _normalized(value)
    job_text = _normalized(job_description)
    value_terms = _terms(value)
    original_job_terms = set(_terms(job_description))
    job_terms = set(original_job_terms)
    for cluster in _RELEVANCE_CLUSTERS:
        if cluster & original_job_terms:
            job_terms.update(cluster)
    matched = sorted(value_terms & job_terms)
    requirement_match = role_profile_from_text(job_description).match(latex_to_text(value))
    score = len(matched) * 10 + requirement_match.score
    if value_text and f" {value_text} " in f" {job_text} ":
        score += 100
    for term in matched:
        if any(marker in term for marker in ("+", "#", ".")) or any(
            character.isdigit() for character in term
        ):
            score += 5
    explanatory_matches = [
        *(
            f"semantic:{item.removeprefix('concept:')}"
            for item in requirement_match.matched_features
            if item.startswith("concept:")
        ),
        *matched,
    ]
    return score, tuple(dict.fromkeys(explanatory_matches))[:20]


def _balanced_argument_end(source: str, opening_brace: int, limit: int) -> int:
    depth = 0
    position = opening_brace
    while position < limit:
        character = source[position]
        escaped = position > 0 and source[position - 1] == "\\"
        if character == "{" and not escaped:
            depth += 1
        elif character == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    raise ValueError("unterminated LaTeX command argument")


def _command_spans(
    source: str, command: str, *, start: int = 0, end: int | None = None
) -> list[_CommandSpan]:
    limit = len(source) if end is None else end
    needle = f"\\{command}"
    spans: list[_CommandSpan] = []
    position = start
    while True:
        command_start = source.find(needle, position, limit)
        if command_start < 0:
            return spans
        argument_start = command_start + len(needle)
        while argument_start < limit and source[argument_start].isspace():
            argument_start += 1
        if argument_start >= limit or source[argument_start] != "{":
            position = command_start + len(needle)
            continue
        argument_end = _balanced_argument_end(source, argument_start, limit)
        line_start = source.rfind("\n", start, command_start) + 1
        span_start = line_start if not source[line_start:command_start].strip() else command_start
        span_end = argument_end
        while span_end < limit and source[span_end] in " \t":
            span_end += 1
        if span_end < limit and source[span_end] == "\n":
            span_end += 1
        spans.append(
            _CommandSpan(
                start=span_start,
                end=span_end,
                latex=source[span_start:span_end],
                content=source[argument_start + 1 : argument_end - 1],
            )
        )
        position = argument_end


def _separate_legacy_project_technology_stacks(source: str) -> str:
    """Upgrade legacy title-and-technology headings to the structural three-argument form."""
    command = r"\resumeProjectHeading"
    position = 0
    while True:
        command_start = source.find(command, position)
        if command_start < 0:
            return source
        cursor = command_start + len(command)
        arguments: list[str] = []
        arguments_end = cursor
        for _ in range(3):
            while cursor < len(source) and source[cursor].isspace():
                cursor += 1
            if cursor >= len(source) or source[cursor] != "{":
                break
            argument_end = _balanced_argument_end(source, cursor, len(source))
            arguments.append(source[cursor + 1 : argument_end - 1])
            cursor = argument_end
            arguments_end = argument_end
        if len(arguments) != 2:
            position = command_start + len(command)
            continue
        title, separator, technologies = arguments[0].partition("$|$")
        if not separator or not title.strip() or not technologies.strip():
            position = cursor
            continue
        replacement = (
            rf"\resumeProjectHeading{{{title.rstrip()}}}"
            rf"{{{technologies.strip()}}}"
            rf"{{{arguments[1]}}}"
        )
        source = source[:command_start] + replacement + source[arguments_end:]
        position = command_start + len(replacement)


_INLINE_PROJECT_SEPARATOR = re.compile(r"(\s*(?:\$\|\$|\\textbar(?:\{\})?|\s\|\s)\s*)")
_DATE_LIKE_PROJECT_METADATA = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"spring|summer|fall|winter|present)\b",
    re.IGNORECASE,
)


def _project_heading_invocations(source: str) -> tuple[_ProjectHeadingInvocation, ...]:
    """Parse project-heading calls and all consecutive balanced arguments."""
    command = r"\resumeProjectHeading"
    invocations: list[_ProjectHeadingInvocation] = []
    position = 0
    while True:
        start = source.find(command, position)
        if start < 0:
            break
        cursor = start + len(command)
        arguments: list[str] = []
        while len(arguments) < 3:
            while cursor < len(source) and source[cursor].isspace():
                cursor += 1
            if cursor >= len(source) or source[cursor] != "{":
                break
            argument_end = _balanced_argument_end(source, cursor, len(source))
            arguments.append(source[cursor + 1 : argument_end - 1])
            cursor = argument_end
        if len(arguments) >= 2:
            invocations.append(
                _ProjectHeadingInvocation(start=start, end=cursor, arguments=tuple(arguments))
            )
            position = cursor
        else:
            position = start + len(command)
    return tuple(invocations)


def _project_entry_headings(source: str) -> tuple[_ProjectHeadingInvocation, ...]:
    """Exclude category labels that are not followed by bullets before the next heading."""
    invocations = _project_heading_invocations(source)
    return tuple(
        invocation
        for index, invocation in enumerate(invocations)
        if r"\resumeItem"
        in source[
            invocation.end : (
                invocations[index + 1].start if index + 1 < len(invocations) else len(source)
            )
        ]
    )


def _split_inline_project_heading(value: str) -> tuple[str, str, str]:
    match = _INLINE_PROJECT_SEPARATOR.search(value)
    if match is None:
        return value.strip(), "", ""
    return value[: match.start()].rstrip(), value[match.end() :].lstrip(), match.group(1)


def _project_heading_mode(arguments: tuple[str, ...]) -> str:
    _, technologies, _ = _split_inline_project_heading(arguments[0])
    if technologies:
        return "inline"
    if len(arguments) >= 3:
        return "structured"
    secondary = arguments[1].strip() if len(arguments) >= 2 else ""
    if (
        secondary
        and r"\textit" in secondary
        and _DATE_LIKE_PROJECT_METADATA.search(latex_to_text(secondary)) is None
    ):
        return "right_technology"
    return "title_only"


def _infer_project_heading_contract(source: str) -> _ProjectHeadingContract:
    """Infer visible project-heading semantics from real entries in the supplied template."""
    headings = _project_entry_headings(source)
    if not headings:
        return _ProjectHeadingContract("preserve", 0)
    modes = [_project_heading_mode(heading.arguments) for heading in headings]
    counts = {mode: modes.count(mode) for mode in set(modes)}
    mode = max(counts, key=lambda item: (counts[item], -modes.index(item)))
    if counts[mode] * 2 <= len(modes):
        return _ProjectHeadingContract("preserve", 0)
    matching = [heading for heading in headings if _project_heading_mode(heading.arguments) == mode]
    argument_count = max(
        {len(heading.arguments) for heading in matching},
        key=lambda count: sum(len(heading.arguments) == count for heading in matching),
    )
    separator = " $|$ "
    if mode == "inline":
        separator = next(
            (
                found
                for heading in matching
                if (found := _split_inline_project_heading(heading.arguments[0])[2])
            ),
            separator,
        )
    return _ProjectHeadingContract(mode, argument_count, separator)


def _project_heading_parts(
    invocation: _ProjectHeadingInvocation,
) -> tuple[str, str, str]:
    title, technologies, _ = _split_inline_project_heading(invocation.arguments[0])
    mode = _project_heading_mode(invocation.arguments)
    if mode == "structured":
        technologies = invocation.arguments[1].strip()
        right = invocation.arguments[2].strip()
    elif mode == "right_technology":
        technologies = invocation.arguments[1].strip()
        right = ""
    else:
        right = invocation.arguments[1].strip()
    return title, technologies, right


def _adapt_project_heading_structure(
    source: str,
    contract: _ProjectHeadingContract,
    *,
    fallback_technologies: tuple[str, ...] = (),
) -> str:
    """Rewrite one inventory heading to the template's observed, deterministic shape."""
    if contract.mode == "preserve":
        return source
    invocations = _project_heading_invocations(source)
    if not invocations:
        raise ValueError("project inventory entry is missing a project heading")
    heading = invocations[0]
    title, technologies, right = _project_heading_parts(heading)
    if not technologies and fallback_technologies and contract.mode != "title_only":
        safe_tags = [
            re.sub(r"([%&#_$])", r"\\\1", value.strip())
            for value in fallback_technologies
            if value.strip()
        ]
        if safe_tags:
            technologies = rf"\textit{{{', '.join(safe_tags)}}}"
    if contract.mode == "inline":
        first = title + (contract.inline_separator + technologies if technologies else "")
        replacement = rf"\resumeProjectHeading{{{first}}}{{{right}}}"
    elif contract.mode == "structured":
        replacement = rf"\resumeProjectHeading{{{title}}}{{{technologies}}}{{{right}}}"
    elif contract.mode == "right_technology":
        replacement = rf"\resumeProjectHeading{{{title}}}{{{technologies}}}"
    else:
        replacement = rf"\resumeProjectHeading{{{title}}}{{{right}}}"
    return source[: heading.start] + replacement + source[heading.end :]


def _project_heading_contract_issues(
    template_section: str,
    proposed_section: str,
) -> tuple[str, ...]:
    """Reject project entries whose argument semantics drift from the template contract."""
    contract = _infer_project_heading_contract(template_section)
    if contract.mode == "preserve":
        return ()
    issues: list[str] = []
    for index, heading in enumerate(_project_entry_headings(proposed_section), start=1):
        mode = _project_heading_mode(heading.arguments)
        compatible_mode = mode == contract.mode
        if not compatible_mode or len(heading.arguments) != contract.argument_count:
            issues.append(
                f"project heading {index} uses {mode}/{len(heading.arguments)} arguments; "
                f"template requires {contract.mode}/{contract.argument_count}"
            )
    return tuple(issues)


def classify_wrapped_resume_items(
    source: str,
    candidates: tuple[ProjectCandidate, ...],
    wrapped_item_indices: tuple[int, ...],
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Map wrapped document bullets to inventory projects or immutable baseline sections."""
    document_start = source.find(r"\begin{document}")
    if document_start < 0:
        raise ValueError("resume proposal must contain \\begin{document}")
    document = source[document_start:]
    bullets = _command_spans(document, "resumeItem")
    project_start, project_end, _ = _section_body(document, "Projects")
    headings = _command_spans(
        document,
        "resumeProjectHeading",
        start=project_start,
        end=project_end,
    )
    project_ranges: list[tuple[int, int, str]] = []
    for index, heading in enumerate(headings):
        entry_end = headings[index + 1].start if index + 1 < len(headings) else project_end
        emphasized_titles = {
            _normalized(span.content) for span in _command_spans(heading.content, "textbf")
        }
        heading_text = _normalized(heading.content)
        matches = [
            candidate
            for candidate in candidates
            if (
                _normalized(candidate.title) in emphasized_titles
                or heading_text == _normalized(candidate.title)
                or heading_text.startswith(f"{_normalized(candidate.title)} ")
            )
        ]
        if len(matches) != 1:
            raise ValueError("rendered project heading does not match one inventory project")
        project_ranges.append((heading.start, entry_end, matches[0].id))

    project_ids: list[str] = []
    non_project_indices: list[int] = []
    for item_index in wrapped_item_indices:
        if item_index < 0 or item_index >= len(bullets):
            raise ValueError("resume layout validator returned an invalid bullet index")
        position = bullets[item_index].start
        project_id = next(
            (
                candidate_id
                for entry_start, entry_end, candidate_id in project_ranges
                if entry_start <= position < entry_end
            ),
            None,
        )
        if project_id is None:
            non_project_indices.append(item_index)
        elif project_id not in project_ids:
            project_ids.append(project_id)
    return tuple(project_ids), tuple(non_project_indices)


def _ranked_commands(
    source: str, command: str, job_description: str, *, group_index: int
) -> tuple[list[_RankedValue], list[_RankedValue]]:
    spans = _command_spans(source, command)
    original: list[_RankedValue] = []
    for index, span in enumerate(spans):
        score, matched_terms = _relevance(span.content, job_description)
        latex_end = spans[index + 1].start if index + 1 < len(spans) else span.end
        original.append(
            _RankedValue(
                group_index=group_index,
                output_group_index=group_index,
                original_index=index,
                output_index=index,
                latex=source[span.start : latex_end],
                text=latex_to_text(span.content),
                score=score,
                matched_terms=matched_terms,
            )
        )
    ranked = [
        replace(value, output_index=output_index)
        for output_index, value in enumerate(
            sorted(original, key=lambda value: (-value.score, value.original_index))
        )
    ]
    return original, ranked


def _replace_command_order(source: str, command: str, ranked: list[_RankedValue]) -> str:
    spans = _command_spans(source, command)
    if not spans:
        return source
    return (
        source[: spans[0].start]
        + "".join(value.latex for value in ranked)
        + source[spans[-1].end :]
    )


def _entry_ranges(section: str, heading_command: str) -> tuple[str, list[str], str]:
    headings = _command_spans(section, heading_command)
    if not headings:
        return section, [], ""
    prefix = section[: headings[0].start]
    closing = section.find("\\resumeSubHeadingListEnd", headings[-1].end)
    suffix_start = closing if closing >= 0 else len(section)
    entries = [
        section[
            heading.start : headings[index + 1].start if index + 1 < len(headings) else suffix_start
        ]
        for index, heading in enumerate(headings)
    ]
    return prefix, entries, section[suffix_start:]


def _prefer_master_project_blocks(
    section: str, candidates: tuple[ProjectCandidate, ...]
) -> tuple[ProjectCandidate, ...]:
    """Reuse exact master formatting when an inventory project carries the same claims."""
    _, entries, _ = _entry_ranges(section, "resumeProjectHeading")
    master_entries: dict[str, str] = {}
    for entry in entries:
        headings = _command_spans(entry, "resumeProjectHeading")
        if not headings:
            continue
        heading_text = _normalized(headings[0].content)
        emphasized_titles = {
            _normalized(span.content) for span in _command_spans(headings[0].content, "textbf")
        }
        for candidate in candidates:
            title = _normalized(candidate.title)
            if (
                title in emphasized_titles
                or heading_text == title
                or heading_text.startswith(f"{title} ")
            ):
                master_entries.setdefault(candidate.id, entry)

    preferred: list[ProjectCandidate] = []
    for candidate in candidates:
        master_entry = master_entries.get(candidate.id)
        if master_entry is None:
            preferred.append(candidate)
            continue
        inventory_bullets = [
            _normalized(span.content) for span in _command_spans(candidate.latex, "resumeItem")
        ]
        master_bullets = [
            _normalized(span.content) for span in _command_spans(master_entry, "resumeItem")
        ]
        preferred.append(
            replace(candidate, latex=master_entry)
            if inventory_bullets == master_bullets
            else candidate
        )
    return tuple(preferred)


def _projects_present_in_section(
    section: str, candidates: tuple[ProjectCandidate, ...]
) -> tuple[ProjectCandidate, ...]:
    """Return inventory projects already present in the source, preserving source order."""
    _, entries, _ = _entry_ranges(section, "resumeProjectHeading")
    by_id = {candidate.id: candidate for candidate in candidates}
    selected_ids: list[str] = []
    for entry in entries:
        headings = _command_spans(entry, "resumeProjectHeading")
        if not headings:
            continue
        heading_text = _normalized(headings[0].content)
        emphasized_titles = {
            _normalized(span.content) for span in _command_spans(headings[0].content, "textbf")
        }
        match = next(
            (
                candidate
                for candidate in candidates
                if (
                    _normalized(candidate.title) in emphasized_titles
                    or heading_text == _normalized(candidate.title)
                    or heading_text.startswith(f"{_normalized(candidate.title)} ")
                )
                and tuple(
                    _normalized(span.content)
                    for span in _command_spans(candidate.latex, "resumeItem")
                )
                == tuple(_normalized(span.content) for span in _command_spans(entry, "resumeItem"))
            ),
            None,
        )
        if match is not None and match.id not in selected_ids:
            selected_ids.append(match.id)
    return tuple(by_id[project_id] for project_id in selected_ids)


def _project_quality_rejections(
    candidates: tuple[ProjectCandidate, ...],
    original: str,
    *,
    maximum_characters: int,
    require_unique_lead_verbs: bool,
) -> list[dict[str, object]]:
    original_bullets = {
        _normalized(span.content) for span in _command_spans(original, "resumeItem")
    }
    projects_start, projects_end, _ = _section_body(original, "Projects")
    retained_original = original[:projects_start] + original[projects_end:]
    original_leads = {
        words[0].casefold()
        for span in _command_spans(retained_original, "resumeItem")
        if (words := re.findall(r"[A-Za-z]+", latex_to_text(span.content)))
    }
    rejections: list[dict[str, object]] = []
    for candidate in candidates:
        issues = list(project_quality_issues(candidate))
        for span in _command_spans(candidate.latex, "resumeItem"):
            text = latex_to_text(span.content)
            if _normalized(text) in original_bullets or not maximum_characters:
                continue
            if len(text) > maximum_characters:
                reason = f"new bullet exceeds the {maximum_characters}-character layout maximum"
                if reason not in issues:
                    issues.append(reason)
                continue
            words = re.findall(r"[A-Za-z]+", text)
            if require_unique_lead_verbs and words and words[0].casefold() in original_leads:
                lead = words[0]
                alternatives = _LEAD_VERB_ALTERNATIVES.get(lead.casefold(), ())
                fits = any(
                    alternative.casefold() not in original_leads
                    and len(text) - len(lead) + len(alternative) <= maximum_characters
                    for alternative in alternatives
                )
                if not fits:
                    reason = "new bullet has no layout-safe lead-verb alternative"
                    if reason not in issues:
                        issues.append(reason)
        if issues:
            rejections.append({"id": candidate.id, "title": candidate.title, "reasons": issues})
    return rejections


def _resume_project_selection_plan(
    original: str,
    candidates: tuple[ProjectCandidate, ...],
    job_description: str,
    *,
    project_count: int,
    maximum_characters: int,
    require_unique_lead_verbs: bool,
    additional_quality_rejections: tuple[dict[str, object], ...] = (),
) -> ResumeProjectSelectionPlan:
    if not candidates:
        return ResumeProjectSelectionPlan((), (), ())
    start, end, _ = _section_body(original, "Projects")
    effective_candidates = _prefer_master_project_blocks(original[start:end], candidates)
    quality_rejections = (
        *_project_quality_rejections(
            effective_candidates,
            original,
            maximum_characters=maximum_characters,
            require_unique_lead_verbs=require_unique_lead_verbs,
        ),
        *additional_quality_rejections,
    )
    rejected_ids = {
        str(item["id"]) for item in quality_rejections if isinstance(item.get("id"), str)
    }
    eligible = tuple(
        candidate for candidate in effective_candidates if candidate.id not in rejected_ids
    )
    selected_ids = select_quality_project_ids(
        eligible,
        job_description,
        project_count=project_count,
    )
    selected_by_id = {candidate.id: candidate for candidate in eligible}
    selected = tuple(selected_by_id[project_id] for project_id in selected_ids)
    rationale_by_id = {
        rationale.id: rationale
        for rationale in select_project_rationales(
            selected,
            job_description,
            max_projects=project_count,
        )
    }
    return ResumeProjectSelectionPlan(
        selected=selected,
        rationales=tuple(
            rationale_by_id[project_id]
            for project_id in selected_ids
            if project_id in rationale_by_id
        ),
        quality_rejections=quality_rejections,
    )


def plan_resume_project_selection(
    *,
    resume_path: Path,
    candidates: tuple[ProjectCandidate, ...],
    job_description: str,
    project_count: int,
    maximum_characters: int,
    require_unique_lead_verbs: bool,
) -> ResumeProjectSelectionPlan:
    """Plan the exact projects before Git inspection and reuse that plan for output."""
    if resume_path.suffix.casefold() != ".tex" or not resume_path.is_file():
        raise ValueError("resume_path must point to an existing .tex file")
    return _resume_project_selection_plan(
        resume_path.read_text(encoding="utf-8"),
        candidates,
        job_description,
        project_count=project_count,
        maximum_characters=maximum_characters,
        require_unique_lead_verbs=require_unique_lead_verbs,
    )


def _reorder_bullets(
    entry: str, job_description: str, *, group_index: int
) -> tuple[str, list[_RankedValue], bool]:
    original, ranked = _ranked_commands(
        entry, "resumeItem", job_description, group_index=group_index
    )
    changed = [item.original_index for item in ranked] != list(range(len(original)))
    return _replace_command_order(entry, "resumeItem", ranked), ranked, changed


def _tailor_experience(section: str, job_description: str) -> tuple[str, list[_RankedValue], bool]:
    prefix, entries, suffix = _entry_ranges(section, "resumeSubheading")
    if not entries:
        tailored, ranked, changed = _reorder_bullets(section, job_description, group_index=0)
        return tailored, ranked, changed
    claims: list[_RankedValue] = []
    changed = False
    tailored_entries: list[str] = []
    for group_index, entry in enumerate(entries):
        tailored, ranked, entry_changed = _reorder_bullets(
            entry, job_description, group_index=group_index
        )
        tailored_entries.append(tailored)
        claims.extend(ranked)
        changed = changed or entry_changed
    return prefix + "".join(tailored_entries) + suffix, claims, changed


def _tailor_projects(section: str, job_description: str) -> tuple[str, list[_RankedValue], bool]:
    prefix, entries, suffix = _entry_ranges(section, "resumeProjectHeading")
    if not entries:
        tailored, ranked, changed = _reorder_bullets(section, job_description, group_index=0)
        return tailored, ranked, changed
    tailored_entries: list[tuple[int, int | None, str, list[_RankedValue], int]] = []
    pending_categories: list[tuple[int, str]] = []
    content_indices: list[int] = []
    changed = False
    for index, entry in enumerate(entries):
        headings = _command_spans(entry, "resumeProjectHeading")
        is_category = (
            len(headings) == 1
            and not _command_spans(entry, "resumeItem")
            and r"\textit{" in headings[0].content
            and r"\textbf{" not in headings[0].content
        )
        if is_category:
            pending_categories.append((index, entry))
            continue
        tailored, ranked_bullets, bullets_changed = _reorder_bullets(
            entry, job_description, group_index=index
        )
        score, _ = _relevance(tailored, job_description)
        content_indices.append(index)
        category_prefix = "".join(category for _, category in pending_categories)
        entry_index = pending_categories[0][0] if pending_categories else index
        tailored_entries.append(
            (entry_index, index, category_prefix + tailored, ranked_bullets, score)
        )
        pending_categories = []
        changed = changed or bullets_changed
    if pending_categories:
        # Retain an incomplete source category without letting it detach from a completed project.
        first_index = pending_categories[0][0]
        tailored_entries.append(
            (first_index, None, "".join(category for _, category in pending_categories), [], -1)
        )
    ranked_entries = sorted(tailored_entries, key=lambda item: (-item[4], item[0]))
    ranked_content_indices = [item[1] for item in ranked_entries if item[1] is not None]
    changed = changed or ranked_content_indices != content_indices
    claims = [
        replace(claim, output_group_index=output_group_index)
        for output_group_index, (_, _, _, entry_claims, _) in enumerate(ranked_entries)
        for claim in entry_claims
    ]
    return prefix + "".join(item[2] for item in ranked_entries) + suffix, claims, changed


_SKILL_LINE = re.compile(
    r"^(?P<prefix>[ \t]*\\textbf\{(?P<category>[^{}]+):\}[ \t]*)"
    r"(?P<values>.*?)(?P<suffix>[ \t]*(?:\\\\)?[ \t]*)$",
    re.MULTILINE,
)
_SKILL_ROW = re.compile(
    r"\\resumeSkillRow\{(?P<category>[^{}]+)\}\{(?P<values>[^{}]*)\}",
    re.MULTILINE,
)


def _tailor_skills(
    section: str,
    job_description: str,
    *,
    maximum_values_per_category: int = 0,
    supported_skills: tuple[SupportedSkill, ...] = (),
) -> tuple[str, list[dict[str, object]], bool]:
    records: list[dict[str, object]] = []
    changed = False
    existing_section_text = latex_to_text(section).casefold()
    added_supported_skills: set[str] = set()

    def category_accepts(category: str, skill: SupportedSkill) -> bool:
        normalized = re.sub(r"[^a-z]+", " ", category.casefold())
        if any(marker in normalized for marker in ("technologies", "technical", "skills")):
            return True
        accepted: dict[str, tuple[str, ...]] = {
            "language": ("language",),
            "framework": ("framework", "library", "libraries"),
            "database": ("database", "data", "platform", "system", "tool"),
            "platform": ("cloud", "infrastructure", "platform", "system", "tool"),
            "tool": ("developer tool", "platform", "system", "tool"),
        }
        return any(marker in normalized for marker in accepted.get(skill.category, ()))

    def latex_skill(value: str) -> str:
        return value.replace("#", r"\#").replace("%", r"\%").replace("&", r"\&")

    def tailored_values(category: str, values: str) -> str:
        nonlocal changed
        items = [value.strip() for value in values.split(",") if value.strip()]
        ranked: list[tuple[int | None, str, int, tuple[str, ...], SupportedSkill | None]] = []
        existing = {latex_to_text(value).casefold() for value in items}
        for index, value in enumerate(items):
            score, matched = _relevance(value, job_description)
            ranked.append((index, value, score, matched, None))
        for skill in supported_skills:
            normalized_skill = skill.name.casefold()
            if (
                normalized_skill in existing
                or normalized_skill in existing_section_text
                or normalized_skill in added_supported_skills
                or not category_accepts(category, skill)
            ):
                continue
            score, matched = _relevance(skill.name, job_description)
            ranked.append((None, latex_skill(skill.name), score, matched, skill))
            existing.add(skill.name.casefold())
            added_supported_skills.add(normalized_skill)
        ranked.sort(
            key=lambda item: (
                -item[2],
                item[0] is None,
                item[0] if item[0] is not None else len(items),
                item[1],
            )
        )
        selected = ranked[:maximum_values_per_category] if maximum_values_per_category else ranked
        changed = changed or [item[0] for item in selected] != list(range(len(items)))
        for ranked_index, (original_index, value, score, matched, supported) in enumerate(ranked):
            included = ranked_index < len(selected)
            output_index = ranked_index if included else None
            source_ref = (
                f"approved-evidence#skill/{supported.name}"
                if supported is not None
                else f"source/resume.tex#Technical Skills/{category}/{original_index + 1}"
                if original_index is not None
                else "source/resume.tex#Technical Skills"
            )
            records.append(
                {
                    "action": (
                        "omitted_for_page_target"
                        if not included
                        else "added_from_approved_evidence"
                        if supported is not None
                        else "reordered"
                        if original_index != output_index
                        else "retained"
                    ),
                    "category": category,
                    "matched_terms": list(matched),
                    "original_index": original_index,
                    "output_index": output_index,
                    "relevance_score": score,
                    "evidence_ids": list(supported.evidence_ids) if supported is not None else [],
                    "source_kind": (
                        "approved_evidence" if supported is not None else "user_provided_template"
                    ),
                    "source_ref": source_ref,
                    "value": supported.name if supported is not None else latex_to_text(value),
                }
            )
        return ", ".join(item[1] for item in selected)

    def row_replacement(match: re.Match[str]) -> str:
        category = match.group("category")
        values = tailored_values(category, match.group("values"))
        return rf"\resumeSkillRow{{{category}}}{{{values}}}"

    def line_replacement(match: re.Match[str]) -> str:
        category = match.group("category")
        values = tailored_values(category, match.group("values"))
        return match.group("prefix") + values + match.group("suffix")

    tailored = _SKILL_ROW.sub(row_replacement, section)
    return _SKILL_LINE.sub(line_replacement, tailored), records, changed


def _section_body(source: str, section_name: str) -> tuple[int, int, str]:
    canonical = resolve_section_name(source, section_name)
    heading = next(
        match
        for match in re.finditer(r"^\\section\{(?P<name>[^}]+)\}\s*$", source, re.MULTILINE)
        if match.group("name") == canonical
    )
    following = re.search(r"^\\section\{[^}]+\}\s*$", source[heading.end() :], re.MULTILINE)
    end = heading.end() + following.start() if following is not None else len(source)
    return heading.end(), end, canonical


def _replace_section_body(source: str, section_name: str, body: str) -> str:
    start, end, _ = _section_body(source, section_name)
    return source[:start] + body + source[end:]


def _evidence_ids_for_claim(text: str, evidence: list[Evidence]) -> list[str]:
    normalized_claim = _normalized(text)
    return [item.id for item in evidence if _normalized(item.text) == normalized_claim]


def _project_claim_records(
    projects: tuple[ProjectCandidate, ...],
) -> list[dict[str, object]]:
    """Record explicit approved-evidence provenance for every selected inventory bullet."""
    records: list[dict[str, object]] = []
    for project_index, project in enumerate(projects):
        bullets = _command_spans(project.latex, "resumeItem")
        if len(bullets) != len(project.bullet_evidence_ids) or any(
            not evidence_ids for evidence_ids in project.bullet_evidence_ids
        ):
            raise ValueError("project inventory bullet_evidence_ids must map every bullet")
        for index, (span, evidence_ids) in enumerate(
            zip(bullets, project.bullet_evidence_ids, strict=True), start=1
        ):
            records.append(
                {
                    "evidence_ids": list(evidence_ids),
                    "output_group_index": project_index,
                    "output_index": index - 1,
                    "project_id": project.id,
                    "project_title": project.title,
                    "section": "Projects",
                    "source_kind": "project_inventory",
                    "source_ref": f"project_inventory/{project.id}/{index}",
                    "text": latex_to_text(span.content),
                    "text_changed": False,
                }
            )
    return records


def _claim_records(
    *, section: str, claims: list[_RankedValue], evidence: list[Evidence]
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for claim in claims:
        evidence_ids = _evidence_ids_for_claim(claim.text, evidence)
        records.append(
            {
                "action": (
                    "reordered"
                    if (
                        claim.group_index != claim.output_group_index
                        or claim.original_index != claim.output_index
                    )
                    else "retained"
                ),
                "evidence_ids": evidence_ids,
                "matched_terms": list(claim.matched_terms),
                "original_index": claim.original_index,
                "original_group_index": claim.group_index,
                "output_index": claim.output_index,
                "output_group_index": claim.output_group_index,
                "relevance_score": claim.score,
                "section": section,
                "source_kind": "approved_evidence" if evidence_ids else "user_provided_template",
                "source_ref": (
                    f"source/resume.tex#{section}/{claim.group_index + 1}/"
                    f"{claim.original_index + 1}"
                ),
                "text": claim.text,
                "text_changed": False,
            }
        )
    return records


def _compact_generated_section(
    section: str,
    *,
    maximum_items: int,
    maximum_characters: int,
) -> tuple[str, list[str]]:
    original, _ = _ranked_commands(section, "resumeItem", "", group_index=0)
    if len(original) <= maximum_items and (
        not maximum_characters or sum(len(item.text) for item in original) <= maximum_characters
    ):
        return section, []
    selected: list[_RankedValue] = []
    characters = 0
    for item in original:
        if len(selected) >= maximum_items:
            break
        item_characters = len(item.text)
        if maximum_characters and selected and characters + item_characters > maximum_characters:
            continue
        selected.append(item)
        characters += item_characters
    omitted = [item.text for item in original if item not in selected]
    return _replace_command_order(section, "resumeItem", selected), omitted


def _compact_generated_entry_section(
    section: str,
    *,
    heading_command: str,
    maximum_items: int,
    maximum_items_per_entry: tuple[int, ...] | None = None,
    minimum_items_per_entry: tuple[int, ...] | None = None,
    job_description: str = "",
    optimize_across_entries: bool = False,
) -> tuple[str, list[str]]:
    """Keep complete semantic entries while applying a rendered bullet budget."""
    prefix, entries, suffix = _entry_ranges(section, heading_command)
    if not entries:
        return _compact_generated_section(
            section,
            maximum_items=maximum_items,
            maximum_characters=0,
        )
    if optimize_across_entries:
        ranked_entries: list[tuple[int, str, list[_RankedValue], int, int]] = []
        content_index = 0
        for entry_index, entry in enumerate(entries):
            bullets, ranked = _ranked_commands(
                entry,
                "resumeItem",
                job_description,
                group_index=entry_index,
            )
            if not bullets:
                continue
            cap = (
                maximum_items_per_entry[content_index]
                if maximum_items_per_entry is not None
                and content_index < len(maximum_items_per_entry)
                else len(ranked)
            )
            minimum = (
                minimum_items_per_entry[content_index]
                if minimum_items_per_entry is not None
                and content_index < len(minimum_items_per_entry)
                else 0
            )
            content_index += 1
            cap = max(0, cap)
            if minimum > cap or minimum > len(ranked):
                raise ValueError(
                    "approved resume content cannot satisfy the configured per-entry bullet "
                    f"minimum for entry {content_index}"
                )
            ranked_entries.append((entry_index, entry, ranked, cap, minimum))
        required = sum(minimum for _, _, _, _, minimum in ranked_entries)
        if required > maximum_items:
            raise ValueError(
                "rendered resume budget cannot satisfy the configured per-entry bullet minimums"
            )
        counts = {entry_index: minimum for entry_index, _, _, _, minimum in ranked_entries}
        remaining = maximum_items - required
        # Every additional line competes on marginal role relevance across all retained roles.
        while remaining > 0:
            choices = [
                (ranked[counts[entry_index]].score, -entry_index, entry_index)
                for entry_index, _, ranked, cap, _ in ranked_entries
                if counts[entry_index] < min(cap, len(ranked))
            ]
            if not choices:
                break
            _, _, selected_entry = max(choices)
            counts[selected_entry] += 1
            remaining -= 1
        optimized_entries: list[str] = []
        optimized_omissions: list[str] = []
        by_index = {item[0]: item for item in ranked_entries}
        for entry_index, entry in enumerate(entries):
            ranked_entry = by_index.get(entry_index)
            if ranked_entry is None:
                continue
            _, _, ranked, _, _ = ranked_entry
            keep = counts[entry_index]
            if keep:
                optimized_entries.append(_replace_command_order(entry, "resumeItem", ranked[:keep]))
            optimized_omissions.extend(item.text for item in ranked[keep:])
        return prefix + "".join(optimized_entries) + suffix, optimized_omissions
    selected_entries: list[str] = []
    omitted: list[str] = []
    remaining = maximum_items
    content_entry_index = 0
    minimums = minimum_items_per_entry or ()
    substantive_entries = [
        entry for entry in entries if _ranked_commands(entry, "resumeItem", "", group_index=0)[0]
    ]
    required = sum(
        minimums[index] if index < len(minimums) else 0 for index in range(len(substantive_entries))
    )
    if required > maximum_items:
        raise ValueError(
            "rendered resume budget cannot satisfy the configured per-entry bullet minimums"
        )
    for entry in entries:
        bullets, _ = _ranked_commands(entry, "resumeItem", "", group_index=0)
        if not bullets:
            # Category labels and other approved structural entries are retained only while the
            # content budget still has room for a following substantive entry.
            if remaining:
                selected_entries.append(entry)
            continue
        if maximum_items_per_entry is None:
            entry_cap = remaining
        elif content_entry_index < len(maximum_items_per_entry):
            entry_cap = maximum_items_per_entry[content_entry_index]
        else:
            entry_cap = 0
        entry_minimum = (
            minimum_items_per_entry[content_entry_index]
            if minimum_items_per_entry is not None
            and content_entry_index < len(minimum_items_per_entry)
            else 0
        )
        content_entry_index += 1
        if entry_minimum > entry_cap or entry_minimum > len(bullets):
            raise ValueError(
                "approved resume content cannot satisfy the configured per-entry bullet "
                f"minimum for entry {content_entry_index}"
            )
        future_minimum = sum(
            minimums[index] if index < len(minimums) else 0
            for index in range(content_entry_index, len(substantive_entries))
        )
        keep = min(max(0, remaining - future_minimum), entry_cap, len(bullets))
        if keep < entry_minimum:
            raise ValueError(
                "rendered resume budget cannot satisfy the configured per-entry bullet minimums"
            )
        if keep:
            selected_entries.append(_replace_command_order(entry, "resumeItem", bullets[:keep]))
            remaining -= keep
        omitted.extend(item.text for item in bullets[keep:])
    return prefix + "".join(selected_entries) + suffix, omitted


def _compact_generated_resume(
    source: str,
    *,
    max_pages: int,
    section_item_limits: Mapping[str, int] | None = None,
    section_entry_item_limits: Mapping[str, tuple[int, ...]] | None = None,
    section_entry_item_minimums: Mapping[str, tuple[int, ...]] | None = None,
    job_description: str = "",
) -> tuple[str, tuple[str, ...], list[dict[str, str]]]:
    """Select bounded relevant items from a generated factual master for the page target."""
    if max_pages < 1 or _GENERATED_TEMPLATE_MARKER not in source:
        return source, (), []
    limits = {
        "Education": (3, 700),
        "Experience": (6, 1_900),
        "Projects": (5, 1_900),
    }
    names = [
        match.group("name")
        for match in re.finditer(r"^\\section\{(?P<name>[^}]+)\}\s*$", source, re.MULTILINE)
    ]
    compacted = source
    changed: list[str] = []
    omissions: list[dict[str, str]] = []
    for name in names:
        if _section_key(name) == _section_key("Technical Skills"):
            continue
        start, end, canonical = _section_body(compacted, name)
        current_section = compacted[start:end]
        maximum_items, maximum_characters = limits.get(canonical, (3, 900))
        if section_item_limits is not None:
            maximum_items = max(0, section_item_limits.get(canonical, maximum_items))
            maximum_characters = 0
        if r"\resumeProjectHeading" in current_section:
            section, omitted = _compact_generated_entry_section(
                current_section,
                heading_command="resumeProjectHeading",
                maximum_items=maximum_items * max_pages,
                maximum_items_per_entry=(
                    section_entry_item_limits.get(canonical)
                    if section_entry_item_limits is not None
                    else None
                ),
                minimum_items_per_entry=(
                    section_entry_item_minimums.get(canonical)
                    if section_entry_item_minimums is not None
                    else None
                ),
                job_description=job_description,
                optimize_across_entries=_section_key(canonical) == _section_key("Experience"),
            )
        elif r"\resumeSubheading" in current_section:
            section, omitted = _compact_generated_entry_section(
                current_section,
                heading_command="resumeSubheading",
                maximum_items=maximum_items * max_pages,
                maximum_items_per_entry=(
                    section_entry_item_limits.get(canonical)
                    if section_entry_item_limits is not None
                    else None
                ),
                minimum_items_per_entry=(
                    section_entry_item_minimums.get(canonical)
                    if section_entry_item_minimums is not None
                    else None
                ),
            )
        else:
            section, omitted = _compact_generated_section(
                current_section,
                maximum_items=maximum_items * max_pages,
                maximum_characters=(maximum_characters * max_pages if maximum_characters else 0),
            )
        if omitted:
            compacted = _replace_section_body(compacted, canonical, section)
            changed.append(canonical)
            omissions.extend(
                {
                    "action": "omitted_for_page_target",
                    "section": canonical,
                    "source_kind": "approved_master_template",
                    "text": text,
                }
                for text in omitted
            )
    return compacted, tuple(changed), omissions


def _configured_generated_entry_budgets(
    source: str,
    *,
    experience_minimum: int,
    experience_maximum: int,
    project_minimum: int,
    project_maximum: int,
) -> tuple[dict[str, tuple[int, ...]], dict[str, tuple[int, ...]]]:
    """Expand simple user settings into semantic per-entry budgets for generated templates."""
    limits: dict[str, tuple[int, ...]] = {}
    minimums: dict[str, tuple[int, ...]] = {}
    for section_name, heading_command, minimum, maximum in (
        (
            "Experience",
            "resumeSubheading",
            experience_minimum,
            experience_maximum,
        ),
        (
            "Projects",
            "resumeProjectHeading",
            project_minimum,
            project_maximum,
        ),
    ):
        if not minimum and not maximum:
            continue
        try:
            start, end, canonical = _section_body(source, section_name)
        except ValueError:
            continue
        _, entries, _ = _entry_ranges(source[start:end], heading_command)
        bullet_counts = tuple(
            len(bullets) for entry in entries if (bullets := _command_spans(entry, "resumeItem"))
        )
        if not bullet_counts:
            continue
        limits[canonical] = tuple(maximum or count for count in bullet_counts)
        minimums[canonical] = tuple(minimum for _ in bullet_counts)
    return limits, minimums


def _omit_generated_layout_bullets(
    source: str, rejected_texts: tuple[str, ...]
) -> tuple[str, list[dict[str, str]]]:
    """Remove visually poor rendered candidates before page-budget selection can backfill."""
    rejected = set(rejected_texts)
    if not rejected or _GENERATED_TEMPLATE_MARKER not in source:
        return source, []
    removals: list[tuple[int, int]] = []
    omissions: list[dict[str, str]] = []
    for match in re.finditer(r"^\\section\{(?P<name>[^}]+)\}\s*$", source, re.MULTILINE):
        name = match.group("name")
        start, end, canonical = _section_body(source, name)
        for span in _command_spans(source[start:end], "resumeItem"):
            text = latex_to_text(span.content)
            if text not in rejected:
                continue
            removals.append((start + span.start, start + span.end))
            omissions.append(
                {
                    "action": "omitted_for_layout_balance",
                    "section": canonical,
                    "source_kind": "approved_master_template",
                    "text": text,
                }
            )
    for start, end in sorted(removals, reverse=True):
        source = source[:start] + source[end:]
    return source, omissions


def generated_resume_item_counts(source: str) -> dict[str, int]:
    """Return selectable item counts for an Erga-generated factual template."""
    if _GENERATED_TEMPLATE_MARKER not in source:
        return {}
    counts: dict[str, int] = {}
    names = [
        match.group("name")
        for match in re.finditer(r"^\\section\{(?P<name>[^}]+)\}\s*$", source, re.MULTILINE)
    ]
    for name in names:
        start, end, canonical = _section_body(source, name)
        count = len(_command_spans(source[start:end], "resumeItem"))
        if count:
            counts[canonical] = count
    return counts


def semantic_resume_structure_issues(source: str) -> tuple[str, ...]:
    """Reject flattened semantic templates before a visually broken PDF can be published."""
    if _SEMANTIC_TEMPLATE_MARKER not in source:
        return ()
    issues: list[str] = []
    explicit_font_sizes = [
        float(value) for value in re.findall(r"\\fontsize\{([0-9]+(?:\.[0-9]+)?)pt\}", source)
    ]
    if r"\LARGE" not in source and not any(size >= 16 for size in explicit_font_sizes):
        issues.append("resume header hierarchy is missing")
    if r"\fontsize{8pt}" in source:
        issues.append("resume body typography is below the semantic template minimum")
    try:
        education_start, education_end, _ = _section_body(source, "Education")
        education = source[education_start:education_end]
        if r"\resumeEducationHeading" not in education:
            issues.append("education heading hierarchy is missing")
    except ValueError:
        pass
    try:
        skills_start, skills_end, _ = _section_body(source, "Technical Skills")
        skills = source[skills_start:skills_end]
        if not (
            re.search(r"\\textbf\{[^{}]+:\}\s*[^%\\\n]", skills) or r"\resumeSkillRow{" in skills
        ):
            issues.append("technical skills rows are missing")
    except ValueError:
        pass
    for section_name, heading_command in (
        ("Experience", "resumeSubheading"),
        ("Projects", "resumeProjectHeading"),
    ):
        try:
            start, end, _ = _section_body(source, section_name)
        except ValueError:
            continue
        section = source[start:end]
        bullet_count = len(_command_spans(section, "resumeItem"))
        heading_count = len(_command_spans(section, heading_command))
        if bullet_count and not heading_count:
            issues.append(f"{section_name.casefold()} subheadings are missing")
        if section_name == "Projects" and bullet_count >= 4 and heading_count < 2:
            issues.append("projects were flattened into too few semantic groups")
        if bullet_count and r"\resumeItemListStart" not in section:
            issues.append(f"{section_name.casefold()} bullet structure is missing")
    return tuple(issues)


def _mark_omitted_claims(claims: list[dict[str, object]], proposed: str) -> list[dict[str, object]]:
    remaining: dict[str, dict[str, int]] = {}
    for section in {str(claim["section"]) for claim in claims}:
        start, end, _ = _section_body(proposed, section)
        counts: dict[str, int] = {}
        for span in _command_spans(proposed[start:end], "resumeItem"):
            text = latex_to_text(span.content)
            counts[text] = counts.get(text, 0) + 1
        remaining[section] = counts
    for claim in claims:
        section = str(claim["section"])
        text = str(claim["text"])
        available = remaining[section].get(text, 0)
        if available:
            remaining[section][text] = available - 1
        else:
            claim["action"] = "omitted_for_page_target"
    return claims


def _bullet_constraint_report(
    original: str,
    proposed: str,
    *,
    minimum: int,
    target: int,
    maximum: int,
    equivalent_originals: dict[str, str] | None = None,
) -> tuple[dict[str, object], tuple[str, ...]]:
    configured = bool(minimum or target or maximum)
    original_document = original[original.find("\\begin{document}") :]
    proposed_document = proposed[proposed.find("\\begin{document}") :]
    original_text = [
        latex_to_text(span.content) for span in _command_spans(original_document, "resumeItem")
    ]
    proposed_text = [
        latex_to_text(span.content) for span in _command_spans(proposed_document, "resumeItem")
    ]
    original_counts: dict[str, int] = {}
    for text in original_text:
        original_counts[text] = original_counts.get(text, 0) + 1
    legacy: list[dict[str, object]] = []
    introduced: list[dict[str, object]] = []
    soft_deviations: list[dict[str, object]] = []
    seen: dict[str, int] = {}
    equivalents = equivalent_originals or {}
    if configured:
        for text in proposed_text:
            length = len(text)
            if minimum <= length <= maximum:
                continue
            if length < minimum:
                soft_deviations.append({"length": length, "text": text})
                continue
            source_text = equivalents.get(text, text)
            occurrence = seen.get(source_text, 0)
            seen[source_text] = occurrence + 1
            item = {"length": length, "text": text}
            if occurrence < original_counts.get(source_text, 0):
                legacy.append(item)
            else:
                introduced.append(item)
    violations = tuple(
        f"new bullet length {item['length']} is outside {minimum}-{maximum} characters"
        for item in introduced
    )
    return (
        {
            "configured": configured,
            "legacy_violations": legacy,
            "maximum": maximum,
            "minimum": minimum,
            "new_violations": introduced,
            "passed": not introduced,
            "soft_deviations": soft_deviations,
            "target": target,
        },
        violations,
    )


def _lead_verb_report(source: str, *, required: bool) -> tuple[dict[str, object], tuple[str, ...]]:
    """Check lead-verb uniqueness mechanically across every résumé bullet."""
    verbs: dict[str, list[str]] = {}
    document = source[source.find("\\begin{document}") :]
    for span in _command_spans(document, "resumeItem"):
        words = re.findall(r"[A-Za-z]+", latex_to_text(span.content))
        if words:
            verbs.setdefault(words[0].casefold(), []).append(latex_to_text(span.content))
    duplicates = {verb: bullets for verb, bullets in verbs.items() if len(bullets) > 1}
    violations = (
        tuple(f"duplicate lead verb '{verb}'" for verb in sorted(duplicates)) if required else ()
    )
    return (
        {
            "configured": required,
            "duplicates": duplicates,
            "passed": not required or not duplicates,
        },
        violations,
    )


def _resolve_duplicate_lead_verbs(
    source: str,
    *,
    required: bool,
    editable_sections: tuple[str, ...],
    maximum_characters: int = 0,
) -> tuple[str, list[dict[str, object]]]:
    """Rewrite repeated bullet openers only when a semantics-preserving alternative exists."""
    if not required:
        return source, []

    spans = _command_spans(source[source.find("\\begin{document}") :], "resumeItem")
    document_start = source.find("\\begin{document}")
    used = {
        words[0].casefold()
        for span in spans
        if (words := re.findall(r"[A-Za-z]+", latex_to_text(span.content)))
    }
    requested = {re.sub(r"[^a-z0-9]+", "", item.casefold()) for item in editable_sections}
    section_ranges: list[tuple[int, int, str]] = []
    for name in ("Experience", "Projects"):
        key = re.sub(r"[^a-z0-9]+", "", name.casefold())
        if key not in requested:
            continue
        start, end, canonical = _section_body(source, name)
        section_ranges.append((start, end, canonical))
    section_counts: dict[str, int] = {}
    seen: set[str] = set()
    rewrites: list[dict[str, object]] = []
    chunks: list[str] = []
    cursor = 0
    for relative_span in spans:
        span = replace(
            relative_span,
            start=relative_span.start + document_start,
            end=relative_span.end + document_start,
        )
        words = re.findall(r"[A-Za-z]+", latex_to_text(span.content))
        if not words:
            continue
        lead = words[0]
        normalized = lead.casefold()
        section = next(
            (name for start, end, name in section_ranges if start <= span.start < end), None
        )
        section_index = section_counts.get(section, 0) if section is not None else -1
        if section is not None:
            section_counts[section] = section_index + 1
        if normalized not in seen:
            seen.add(normalized)
            continue
        if section is None:
            continue
        candidates = _LEAD_VERB_ALTERNATIVES.get(normalized, ())
        original_text = latex_to_text(span.content)
        replacement = next(
            (
                candidate
                for candidate in candidates
                if candidate.casefold() not in used
                and (
                    not maximum_characters
                    or len(original_text) - len(lead) + len(candidate) <= maximum_characters
                )
            ),
            None,
        )
        if replacement is None:
            continue
        rewritten_content = re.sub(
            rf"(?<![A-Za-z]){re.escape(lead)}(?![A-Za-z])",
            replacement,
            span.content,
            count=1,
            flags=re.IGNORECASE,
        )
        rewritten_latex = span.latex.replace(span.content, rewritten_content, 1)
        chunks.extend((source[cursor : span.start], rewritten_latex))
        cursor = span.end
        rewritten_text = latex_to_text(rewritten_content)
        rewrites.append(
            {
                "from": lead,
                "original_text": latex_to_text(span.content),
                "rewritten_text": rewritten_text,
                "section": section,
                "section_bullet_index": section_index,
                "to": replacement,
            }
        )
        used.add(replacement.casefold())
    if not rewrites:
        return source, []
    chunks.append(source[cursor:])
    return "".join(chunks), rewrites


def _record_lead_verb_rewrites(
    records: list[dict[str, object]], rewrites: list[dict[str, object]]
) -> None:
    """Bind each rewrite to the exact output-position provenance record."""
    for rewrite in rewrites:
        section = rewrite["section"]
        if not isinstance(section, str):
            continue

        def position(item: dict[str, object], key: str) -> int:
            value = item.get(key)
            return value if isinstance(value, int) else 0

        ordered = sorted(
            (item for item in records if item.get("section") == section),
            key=lambda item: (
                position(item, "output_group_index"),
                position(item, "output_index"),
            ),
        )
        index_value = rewrite["section_bullet_index"]
        if not isinstance(index_value, int):
            continue
        index = index_value
        if index >= len(ordered):
            continue
        record = ordered[index]
        record["original_text"] = rewrite["original_text"]
        record["text"] = rewrite["rewritten_text"]
        record["text_changed"] = True


def apply_adaptive_single_page_fill(source: str) -> str:
    """Prevent TeX from stretching sparse resume content into artificial vertical gaps."""
    if _PAGE_FILL_MARKER in source or _VISUAL_SPACING_MARKER in source:
        return source
    if source.count(r"\resumeItem{") < 6:
        return source
    document_marker = r"\begin{document}"
    document_start = source.find(document_marker)
    if document_start < 0:
        raise ValueError("resume proposal must contain \\begin{document}")

    return source.replace(document_marker, document_marker + _PAGE_FILL_SETUP, 1)


def pdf_page_fill(pdf_path: Path) -> PdfPageFill:
    """Measure rendered text coverage on a one-page PDF using page-relative coordinates."""
    if pdf_path.suffix.casefold() != ".pdf" or not pdf_path.is_file():
        raise ValueError("pdf_path must point to an existing PDF")
    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) != 1:
            raise ValueError("page-fill validation requires exactly one PDF page")
        page = reader.pages[0]
        page_height = float(page.mediabox.height)
        text_runs: list[tuple[float, float]] = []

        def observe_text(
            text: str,
            current_matrix: list[float],
            text_matrix: list[float],
            _font: dict[str, object] | None,
            font_size: float,
        ) -> None:
            if not text.strip():
                return
            x = float(text_matrix[4])
            y = float(text_matrix[5])
            transformed_y = (
                x * float(current_matrix[1])
                + y * float(current_matrix[3])
                + float(current_matrix[5])
            )
            scaled_size = abs(float(font_size) * float(current_matrix[3])) or abs(float(font_size))
            text_runs.append((transformed_y - scaled_size * 0.2, transformed_y + scaled_size))

        page.extract_text(visitor_text=observe_text)
    except (OSError, PdfReadError) as error:
        raise ValueError(f"PDF content fill could not be measured: {error}") from error
    if page_height <= 0 or not text_runs:
        raise ValueError("PDF contains no measurable rendered text")
    content_bottom = max(0.0, min(bottom for bottom, _ in text_runs))
    content_top = min(page_height, max(top for _, top in text_runs))
    return PdfPageFill(
        page_height=round(page_height, 3),
        content_top=round(content_top, 3),
        content_bottom=round(content_bottom, 3),
        fill_ratio=round((content_top - content_bottom) / page_height, 4),
        text_run_count=len(text_runs),
    )


def create_automatic_resume_proposal(
    *,
    resume_path: Path,
    output_dir: Path,
    job_description: str,
    evidence: list[Evidence],
    editable_sections: tuple[str, ...],
    bullet_min_chars: int = 0,
    bullet_target_chars: int = 0,
    bullet_max_chars: int = 0,
    project_candidates: tuple[ProjectCandidate, ...] = (),
    project_count: int = 4,
    experience_min_bullets: int = 0,
    experience_max_bullets: int = 0,
    project_min_bullets: int = 0,
    project_max_bullets: int = 0,
    require_unique_lead_verbs: bool = True,
    minimum_page_fill_ratio: float = 0,
    max_pages: int = 0,
    generated_section_item_limits: Mapping[str, int] | None = None,
    generated_section_entry_item_limits: Mapping[str, tuple[int, ...]] | None = None,
    generated_section_entry_item_minimums: Mapping[str, tuple[int, ...]] | None = None,
    layout_rejected_bullet_texts: tuple[str, ...] = (),
    additional_project_quality_rejections: tuple[dict[str, object], ...] = (),
) -> AutomaticResumeProposal:
    """Tailor a résumé using only user-provided claims and approved project blocks."""
    if resume_path.suffix.casefold() != ".tex" or not resume_path.is_file():
        raise ValueError("resume_path must point to an existing .tex file")
    if any(not item.approved for item in evidence):
        raise ValueError("automatic proposal evidence must be approved")
    if not job_description.strip():
        raise ValueError("job_description cannot be empty")

    original = resume_path.read_text(encoding="utf-8")
    role_profile = role_profile_from_text(job_description)
    evidence_claims = index_evidence_claims(
        evidence,
        job_description,
        role_profile=role_profile,
    )
    generated_copy_only = _GENERATED_TEMPLATE_MARKER in original and not project_candidates
    enforce_unique_lead_verbs = require_unique_lead_verbs and not generated_copy_only
    proposed = original
    requested = {re.sub(r"[^a-z0-9]+", "", item.casefold()) for item in editable_sections}
    changed_sections: list[str] = []
    claims: list[dict[str, object]] = []
    project_claims: list[dict[str, object]] = []
    skill_records: list[dict[str, object]] = []
    projects_requested = "projects" in requested
    selection_plan = (
        _resume_project_selection_plan(
            original,
            project_candidates,
            job_description,
            project_count=project_count,
            maximum_characters=bullet_max_chars,
            require_unique_lead_verbs=require_unique_lead_verbs,
            additional_quality_rejections=additional_project_quality_rejections,
        )
        if projects_requested
        else ResumeProjectSelectionPlan((), (), ())
    )
    quality_rejections = list(selection_plan.quality_rejections)
    project_selection: dict[str, object] = {
        "candidate_count": len(project_candidates),
        "mode": "reorder_only",
        "quality_rejections": quality_rejections,
        "strategy": "contrastive_project_identity_v1",
        "selected_ids": [],
    }
    fallback_reason: str | None = None

    for requested_name, tailorer in (
        ("Experience", _tailor_experience),
        ("Projects", _tailor_projects),
    ):
        key = re.sub(r"[^a-z0-9]+", "", requested_name.casefold())
        if key not in requested:
            continue
        start, end, canonical = _section_body(proposed, requested_name)
        if requested_name == "Projects" and project_candidates:
            selected_projects = selection_plan.selected
            selected_rationales = selection_plan.rationales
            if not selected_projects:
                project_selection = {
                    "candidate_count": len(project_candidates),
                    "mode": "inventory_no_match",
                    "quality_rejections": quality_rejections,
                    "strategy": "contrastive_project_identity_v1",
                    "selected_ids": [],
                    "selected_titles": [],
                }
                # An enabled arsenal owns Projects selection; do not reshuffle stale entries.
                continue
            template_project_section = proposed[start:end]
            prefix, _, suffix = _entry_ranges(template_project_section, "resumeProjectHeading")
            heading_contract = _infer_project_heading_contract(template_project_section)
            inventory_section = (
                prefix
                + "".join(
                    _adapt_project_heading_structure(
                        item.latex,
                        heading_contract,
                        fallback_technologies=item.tags,
                    )
                    for item in selected_projects
                )
                + suffix
            )
            heading_issues = _project_heading_contract_issues(
                template_project_section,
                inventory_section,
            )
            if heading_issues:
                raise ValueError(
                    "project heading structure deviates from template: " + "; ".join(heading_issues)
                )
            proposed = _replace_section_body(proposed, canonical, inventory_section)
            start, end, canonical = _section_body(proposed, requested_name)
            project_selection = {
                "candidate_count": len(project_candidates),
                "mode": "inventory",
                "quality_rejections": quality_rejections,
                "strategy": "contrastive_project_identity_v1",
                "selected_ids": [item.id for item in selected_projects],
                "selected_titles": [item.title for item in selected_projects],
                "heading_contract": {
                    "argument_count": heading_contract.argument_count,
                    "mode": heading_contract.mode,
                    "validated": not heading_issues,
                },
                "portfolio_quality": portfolio_quality_report(selected_projects).as_dict(),
                "selected": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "matched_terms": list(item.matched_terms),
                        "matched_signals": list(item.matched_signals),
                        "narrative_signals": list(item.narrative_signals),
                        "metric_categories": list(item.metric_categories),
                        "differentiators": list(item.differentiators),
                        "evidence_tier": item.evidence_tier,
                        "quality_score": item.quality_score,
                        "differentiation_score": item.differentiation_score,
                        "selection_score": item.selection_score,
                        "applied_to_resume": True,
                    }
                    for item in selected_rationales
                ],
            }
            project_claims = _project_claim_records(selected_projects)
        tailored, section_claims, changed = tailorer(proposed[start:end], job_description)
        proposed = _replace_section_body(proposed, canonical, tailored)
        claims.extend(_claim_records(section=canonical, claims=section_claims, evidence=evidence))
        if changed or project_selection["mode"] == "inventory":
            changed_sections.append(canonical)

    skills_key = re.sub(r"[^a-z0-9]+", "", "Technical Skills".casefold())
    if skills_key in requested:
        start, end, canonical = _section_body(proposed, "Technical Skills")
        generated_template = _GENERATED_TEMPLATE_MARKER in original
        tailored, skill_records, changed = _tailor_skills(
            proposed[start:end],
            job_description,
            maximum_values_per_category=(6 * max_pages if generated_template and max_pages else 0),
            supported_skills=supported_role_skills(evidence, job_description),
        )
        proposed = _replace_section_body(proposed, canonical, tailored)
        if changed:
            changed_sections.append(canonical)

    # Generated factual templates contain a superset of approved content. Remove only candidates
    # proven to create a one/two-word rendered tail, then let normal relevance/page selection
    # backfill from the remaining approved claims.
    proposed, layout_omissions = _omit_generated_layout_bullets(
        proposed, layout_rejected_bullet_texts
    )

    # Select the requested
    # render budget before enforcing whole-document constraints so omitted candidates cannot cause
    # duplicate-lead or length failures in a proposal where they will not appear.
    configured_entry_limits, configured_entry_minimums = _configured_generated_entry_budgets(
        proposed,
        experience_minimum=experience_min_bullets,
        experience_maximum=experience_max_bullets,
        project_minimum=project_min_bullets,
        project_maximum=project_max_bullets,
    )
    resolved_entry_limits = (
        generated_section_entry_item_limits
        if generated_section_entry_item_limits is not None
        else configured_entry_limits or None
    )
    resolved_entry_minimums = (
        generated_section_entry_item_minimums
        if generated_section_entry_item_minimums is not None
        else configured_entry_minimums or None
    )
    proposed, compacted_sections, page_target_omissions = _compact_generated_resume(
        proposed,
        max_pages=max_pages,
        section_item_limits=generated_section_item_limits,
        section_entry_item_limits=resolved_entry_limits,
        section_entry_item_minimums=resolved_entry_minimums,
        job_description=job_description,
    )
    page_target_omissions.extend(layout_omissions)
    for compacted_section in compacted_sections:
        if compacted_section not in changed_sections:
            changed_sections.append(compacted_section)

    rewrite_sections = editable_sections
    if project_selection["mode"] == "inventory_no_match":
        rewrite_sections = tuple(
            section
            for section in editable_sections
            if re.sub(r"[^a-z0-9]+", "", section.casefold()) != "projects"
        )
    proposed, lead_verb_rewrites = _resolve_duplicate_lead_verbs(
        proposed,
        required=enforce_unique_lead_verbs,
        editable_sections=rewrite_sections,
        maximum_characters=bullet_max_chars,
    )
    for rewrite in lead_verb_rewrites:
        rewrite_section = rewrite.get("section")
        if isinstance(rewrite_section, str) and rewrite_section not in changed_sections:
            changed_sections.append(rewrite_section)
    _record_lead_verb_rewrites(claims, lead_verb_rewrites)
    _record_lead_verb_rewrites(project_claims, lead_verb_rewrites)
    equivalent_originals: dict[str, str] = {}
    for rewrite in lead_verb_rewrites:
        rewritten_text = rewrite.get("rewritten_text")
        original_text = rewrite.get("original_text")
        if isinstance(rewritten_text, str) and isinstance(original_text, str):
            equivalent_originals[rewritten_text] = original_text

    length_report, violations = _bullet_constraint_report(
        original,
        proposed,
        minimum=bullet_min_chars,
        target=bullet_target_chars,
        maximum=bullet_max_chars,
        equivalent_originals=equivalent_originals,
    )
    lead_verb_report, lead_verb_violations = _lead_verb_report(
        proposed, required=enforce_unique_lead_verbs
    )
    lead_verb_report["rewrites"] = lead_verb_rewrites
    violations = (*violations, *lead_verb_violations)
    if violations:
        fallback_reason = (
            "The tailored draft violated hard bullet or lead-verb constraints, so Erga retained "
            "the validated master content."
        )
        proposed = original
        changed_sections = []
        claims = []
        project_claims = []
        skill_records = []
        lead_verb_rewrites = []
        proposed, layout_omissions = _omit_generated_layout_bullets(
            proposed, layout_rejected_bullet_texts
        )
        proposed, compacted_sections, page_target_omissions = _compact_generated_resume(
            proposed,
            max_pages=max_pages,
            section_item_limits=generated_section_item_limits,
            section_entry_item_limits=resolved_entry_limits,
            section_entry_item_minimums=resolved_entry_minimums,
            job_description=job_description,
        )
        page_target_omissions.extend(layout_omissions)
        changed_sections.extend(compacted_sections)
        lead_verb_report, _ = _lead_verb_report(proposed, required=enforce_unique_lead_verbs)
        lead_verb_report["rewrites"] = []
        if project_candidates:
            project_start, project_end, _ = _section_body(original, "Projects")
            retained_projects = _projects_present_in_section(
                original[project_start:project_end], project_candidates
            )
            project_selection = {
                "candidate_count": len(project_candidates),
                "mode": "inventory_constraint_fallback",
                "quality_rejections": quality_rejections,
                "strategy": "master_project_fallback",
                "selected_ids": [item.id for item in retained_projects],
                "selected_titles": [item.title for item in retained_projects],
                "selected": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "matched_terms": [],
                        "matched_signals": [],
                        "applied_to_resume": True,
                    }
                    for item in retained_projects
                ],
            }

    claims = _mark_omitted_claims(claims, proposed)

    if minimum_page_fill_ratio:
        proposed = apply_adaptive_single_page_fill(proposed)

    quality_baseline, _ = _omit_generated_layout_bullets(original, layout_rejected_bullet_texts)
    quality_baseline, _, _ = _compact_generated_resume(
        quality_baseline,
        max_pages=max_pages,
        section_item_limits=generated_section_item_limits,
        section_entry_item_limits=resolved_entry_limits,
        section_entry_item_minimums=resolved_entry_minimums,
        job_description=job_description,
    )
    if minimum_page_fill_ratio:
        quality_baseline = apply_adaptive_single_page_fill(quality_baseline)

    meaningful_change = proposed != original
    rejected_master_comparison: dict[str, object] | None = None
    master_comparison = compare_resume_to_master(
        quality_baseline,
        proposed,
        job_description=job_description,
    )
    if not master_comparison.passed:
        # A safe master is always preferable to publishing weaker generated copy. Keep the
        # rejected comparison in the decision artifact, but validate and publish the unchanged
        # master instead of turning a quality miss into a failed intake.
        rejected_master_comparison = master_comparison.as_dict()
        fallback_reason = (
            "The tailored draft fell below the configured master-quality floor, so Erga retained "
            "the validated master content."
        )
        proposed = quality_baseline
        meaningful_change = proposed != original
        changed_sections = []
        for section_name in ("Experience", "Projects", "Technical Skills"):
            try:
                original_start, original_end, canonical = _section_body(original, section_name)
                baseline_start, baseline_end, _ = _section_body(proposed, section_name)
            except ValueError:
                continue
            if original[original_start:original_end] != proposed[baseline_start:baseline_end]:
                changed_sections.append(canonical)
        claims = _mark_omitted_claims(claims, proposed)
        master_comparison = compare_resume_to_master(
            quality_baseline,
            proposed,
            job_description=job_description,
        )
        master_retained_projects: tuple[ProjectCandidate, ...] = ()
        if project_candidates:
            project_start, project_end, _ = _section_body(original, "Projects")
            master_retained_projects = _projects_present_in_section(
                original[project_start:project_end], project_candidates
            )
        project_selection = {
            "candidate_count": len(project_candidates),
            "mode": "master_quality_fallback",
            "quality_rejections": quality_rejections,
            "strategy": "master_quality_floor_v1",
            "selected_ids": [item.id for item in master_retained_projects],
            "selected_titles": [item.title for item in master_retained_projects],
            "selected": [
                {
                    "id": item.id,
                    "title": item.title,
                    "matched_terms": [],
                    "matched_signals": [],
                    "applied_to_resume": True,
                }
                for item in master_retained_projects
            ],
        }
    output_dir.mkdir(parents=True, exist_ok=True)
    if not meaningful_change and fallback_reason is None:
        fallback_reason = (
            "No meaningful evidence-supported, constraint-valid tailoring change was available."
        )
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"
    decision_report_path = output_dir / "resume-decision.json"
    proposed_tex_path.write_text(proposed, encoding="utf-8")
    editorial_validation = analyze_resume_editorially(
        proposed,
        maximum_characters=bullet_max_chars,
    )
    diff_path.write_text(
        "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=str(resume_path),
                tofile=str(proposed_tex_path),
            )
        ),
        encoding="utf-8",
    )
    claim_report_path.write_text(
        json.dumps(
            {
                "approved_evidence": [
                    {"id": item.id, "source_ref": item.source_ref, "text": item.text}
                    for item in evidence
                ],
                "evidence_claims": [item.as_dict() for item in evidence_claims],
                "role_profile": role_profile.as_dict(),
                "claims": claims,
                "constraints": {
                    "bullet_characters": length_report,
                    "editorial_validation": editorial_validation.as_dict(),
                    "lead_verbs": lead_verb_report,
                },
                "external_sync": "not performed",
                "page_target_omissions": page_target_omissions,
                "project_claims": project_claims,
                "project_selection": project_selection,
                "skills": skill_records,
                "source_modified": False,
                "tailoring": {
                    "baseline_fallback": not meaningful_change,
                    "changed_sections": changed_sections,
                    "meaningful_change": meaningful_change,
                    "method": (
                        "deterministic relevance ordering and page-target selection; "
                        "no claim text changed"
                        if compacted_sections
                        else "deterministic relevance ordering with semantics-preserving "
                        "lead-verb rewrites"
                        if lead_verb_rewrites
                        else "deterministic relevance ordering; no claim text changed"
                    ),
                    "reason": (fallback_reason if not meaningful_change else None),
                    "version": TAILORING_VERSION,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    raw_selected_ids = project_selection.get("selected_ids")
    selected_ids = (
        tuple(item for item in raw_selected_ids if isinstance(item, str))
        if isinstance(raw_selected_ids, list)
        else ()
    )
    eligible_decision_candidates = tuple(
        candidate
        for candidate in project_candidates
        if not any(
            item.get("id") == candidate.id for item in quality_rejections if isinstance(item, dict)
        )
    )
    catalogue_decision = rank_project_candidates(
        eligible_decision_candidates,
        job_description,
        selected_ids=tuple(
            item for item in selected_ids if item in {c.id for c in eligible_decision_candidates}
        ),
    )
    decision_report_path.write_text(
        json.dumps(
            {
                "version": 1,
                "master_parity": master_comparison.as_dict(),
                "rejected_proposal_master_parity": rejected_master_comparison,
                "catalogue": catalogue_decision.as_dict(),
                "project_quality_rejections": quality_rejections,
                "preference_scope": "current_generation_only",
                "selected_bullet_evidence_ids": [
                    list(ids)
                    for candidate in eligible_decision_candidates
                    if candidate.id in selected_ids
                    for ids in candidate.bullet_evidence_ids
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return AutomaticResumeProposal(
        proposal=ResumeProposal(
            proposed_tex_path, diff_path, claim_report_path, decision_report_path
        ),
        meaningful_change=meaningful_change,
        changed_sections=tuple(changed_sections),
        constraint_violations=violations,
        project_selection=project_selection,
        fallback_reason=fallback_reason,
    )


def pdf_page_count(pdf_path: Path) -> int:
    """Count page objects in a PDF without relying on an OS-specific executable."""
    if pdf_path.suffix.casefold() != ".pdf" or not pdf_path.is_file():
        raise ValueError("pdf_path must point to an existing PDF")
    try:
        count = len(PdfReader(pdf_path).pages)
    except (OSError, PdfReadError):
        # Keep small structural fixtures readable while production PDFs use the full parser.
        count = len(re.findall(rb"/Type\s*/Page\b", pdf_path.read_bytes()))
    if count < 1:
        raise ValueError("PDF contains no readable page objects")
    return count
