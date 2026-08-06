from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .models import Evidence
from .project_inventory import ProjectCandidate, select_projects
from .resume import ResumeProposal, latex_to_text, resolve_section_name

_TOKEN = re.compile(r"[a-z0-9+#.]+")
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
TAILORING_VERSION = 7


@dataclass(frozen=True)
class AutomaticResumeProposal:
    proposal: ResumeProposal
    meaningful_change: bool
    changed_sections: tuple[str, ...]
    constraint_violations: tuple[str, ...]


@dataclass(frozen=True)
class _CommandSpan:
    start: int
    end: int
    latex: str
    content: str


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
    score = len(matched) * 10
    if value_text and f" {value_text} " in f" {job_text} ":
        score += 100
    for term in matched:
        if any(marker in term for marker in ("+", "#", ".")) or any(
            character.isdigit() for character in term
        ):
            score += 5
    return score, tuple(matched[:20])


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
    tailored_entries: list[tuple[int, str, list[_RankedValue], int]] = []
    changed = False
    for index, entry in enumerate(entries):
        tailored, ranked_bullets, bullets_changed = _reorder_bullets(
            entry, job_description, group_index=index
        )
        score, _ = _relevance(tailored, job_description)
        tailored_entries.append((index, tailored, ranked_bullets, score))
        changed = changed or bullets_changed
    ranked_entries = sorted(tailored_entries, key=lambda item: (-item[3], item[0]))
    changed = changed or [item[0] for item in ranked_entries] != list(range(len(entries)))
    claims = [
        replace(claim, output_group_index=output_group_index)
        for output_group_index, (_, _, entry_claims, _) in enumerate(ranked_entries)
        for claim in entry_claims
    ]
    return prefix + "".join(item[1] for item in ranked_entries) + suffix, claims, changed


_SKILL_LINE = re.compile(
    r"^(?P<prefix>[ \t]*\\textbf\{(?P<category>[^{}]+):\}[ \t]*)"
    r"(?P<values>.*?)(?P<suffix>[ \t]*(?:\\\\)?[ \t]*)$",
    re.MULTILINE,
)


def _tailor_skills(section: str, job_description: str) -> tuple[str, list[dict[str, object]], bool]:
    records: list[dict[str, object]] = []
    changed = False

    def replacement(match: re.Match[str]) -> str:
        nonlocal changed
        values = [value.strip() for value in match.group("values").split(",") if value.strip()]
        ranked: list[tuple[int, str, int, tuple[str, ...]]] = []
        for index, value in enumerate(values):
            score, matched = _relevance(value, job_description)
            ranked.append((index, value, score, matched))
        ranked.sort(key=lambda item: (-item[2], item[0]))
        changed = changed or [item[0] for item in ranked] != list(range(len(values)))
        category = match.group("category")
        for output_index, (original_index, value, score, matched) in enumerate(ranked):
            records.append(
                {
                    "action": "reordered" if original_index != output_index else "retained",
                    "category": category,
                    "matched_terms": list(matched),
                    "original_index": original_index,
                    "output_index": output_index,
                    "relevance_score": score,
                    "source_kind": "user_provided_template",
                    "source_ref": (
                        f"source/resume.tex#Technical Skills/{category}/{original_index + 1}"
                    ),
                    "value": value,
                }
            )
        return match.group("prefix") + ", ".join(item[1] for item in ranked) + match.group("suffix")

    return _SKILL_LINE.sub(replacement, section), records, changed


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
    for project in projects:
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
                    "project_id": project.id,
                    "project_title": project.title,
                    "source_kind": "project_inventory",
                    "source_ref": f"project_inventory/{project.id}/{index}",
                    "text": latex_to_text(span.content),
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


def _bullet_constraint_report(
    original: str,
    proposed: str,
    *,
    minimum: int,
    target: int,
    maximum: int,
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
    seen: dict[str, int] = {}
    if configured:
        for text in proposed_text:
            length = len(text)
            if minimum <= length <= maximum:
                continue
            occurrence = seen.get(text, 0)
            seen[text] = occurrence + 1
            item = {"length": length, "text": text}
            if occurrence < original_counts.get(text, 0):
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
    require_unique_lead_verbs: bool = False,
) -> AutomaticResumeProposal:
    """Tailor a résumé using only user-provided claims and approved project blocks."""
    if resume_path.suffix.casefold() != ".tex" or not resume_path.is_file():
        raise ValueError("resume_path must point to an existing .tex file")
    if any(not item.approved for item in evidence):
        raise ValueError("automatic proposal evidence must be approved")
    if not job_description.strip():
        raise ValueError("job_description cannot be empty")

    original = resume_path.read_text(encoding="utf-8")
    proposed = original
    requested = {re.sub(r"[^a-z0-9]+", "", item.casefold()) for item in editable_sections}
    changed_sections: list[str] = []
    claims: list[dict[str, object]] = []
    project_claims: list[dict[str, object]] = []
    skill_records: list[dict[str, object]] = []
    project_selection: dict[str, object] = {
        "candidate_count": len(project_candidates),
        "mode": "reorder_only",
        "selected_ids": [],
    }

    for requested_name, tailorer in (
        ("Experience", _tailor_experience),
        ("Projects", _tailor_projects),
    ):
        key = re.sub(r"[^a-z0-9]+", "", requested_name.casefold())
        if key not in requested:
            continue
        start, end, canonical = _section_body(proposed, requested_name)
        if requested_name == "Projects" and project_candidates:
            selected_projects = select_projects(
                project_candidates, job_description, max_projects=project_count
            )
            if not selected_projects:
                project_selection = {
                    "candidate_count": len(project_candidates),
                    "mode": "inventory_no_match",
                    "selected_ids": [],
                    "selected_titles": [],
                }
                # An enabled arsenal owns Projects selection; do not reshuffle stale entries.
                continue
            prefix, _, suffix = _entry_ranges(proposed[start:end], "resumeProjectHeading")
            inventory_section = prefix + "".join(item.latex for item in selected_projects) + suffix
            proposed = _replace_section_body(proposed, canonical, inventory_section)
            start, end, canonical = _section_body(proposed, requested_name)
            project_selection = {
                "candidate_count": len(project_candidates),
                "mode": "inventory",
                "selected_ids": [item.id for item in selected_projects],
                "selected_titles": [item.title for item in selected_projects],
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
        tailored, skill_records, changed = _tailor_skills(proposed[start:end], job_description)
        proposed = _replace_section_body(proposed, canonical, tailored)
        if changed:
            changed_sections.append(canonical)

    length_report, violations = _bullet_constraint_report(
        original,
        proposed,
        minimum=bullet_min_chars,
        target=bullet_target_chars,
        maximum=bullet_max_chars,
    )
    lead_verb_report, lead_verb_violations = _lead_verb_report(
        proposed, required=require_unique_lead_verbs
    )
    violations = (*violations, *lead_verb_violations)
    if violations:
        proposed = original
        changed_sections = []
        claims = []
        project_claims = []
        skill_records = []
        if project_candidates:
            project_selection = {
                "candidate_count": len(project_candidates),
                "mode": "inventory_constraint_fallback",
                "selected_ids": [],
                "selected_titles": [],
            }

    meaningful_change = proposed != original
    output_dir.mkdir(parents=True, exist_ok=True)
    proposed_tex_path = output_dir / "proposal.tex"
    diff_path = output_dir / "proposal.diff"
    claim_report_path = output_dir / "claim-report.json"
    proposed_tex_path.write_text(proposed, encoding="utf-8")
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
                "claims": claims,
                "constraints": {
                    "bullet_characters": length_report,
                    "lead_verbs": lead_verb_report,
                },
                "external_sync": "not performed",
                "project_claims": project_claims,
                "project_selection": project_selection,
                "skills": skill_records,
                "source_modified": False,
                "tailoring": {
                    "baseline_fallback": not meaningful_change,
                    "changed_sections": changed_sections,
                    "meaningful_change": meaningful_change,
                    "method": "deterministic relevance ordering; no claim text changed",
                    "reason": (
                        "No meaningful, constraint-valid ordering change was available."
                        if not meaningful_change
                        else None
                    ),
                    "version": TAILORING_VERSION,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return AutomaticResumeProposal(
        proposal=ResumeProposal(proposed_tex_path, diff_path, claim_report_path),
        meaningful_change=meaningful_change,
        changed_sections=tuple(changed_sections),
        constraint_violations=violations,
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
