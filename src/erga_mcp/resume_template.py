"""Generate a private, self-contained LaTeX template from an approved resume source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .config import load_config
from .private_files import restrict_private_directory, restrict_private_file
from .resume_settings import update_settings
from .resume_sources import ResumeSource, load_resume_source
from .resume_tailoring import latex_to_text

TEMPLATE_GENERATION_VERSION = 15
_PAGE_MARKER = re.compile(r"^\[Page \d+\]$")
_BULLET_PREFIX = re.compile(r"^(?:[•●▪◦‣⁃*]|[-–—]\s)\s*")
_SPACE = re.compile(r"\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])")
_SECTION_KEY = re.compile(r"[^a-z0-9]+")
_LAYOUT_INDENT_MARKER = "[[ERGA-LAYOUT-INDENT]]"
_LAYOUT_COLUMN_MARKER = "[[ERGA-LAYOUT-COLUMN]]"
_SEMANTIC_TEMPLATE_MARKER = "% Erga semantic resume template version: 15"
_VISUAL_SPACING_MARKER = "% Erga visual spacing is template-controlled."
_SECTION_ALIASES = {
    "activities": "Activities",
    "awards": "Awards",
    "certifications": "Certifications",
    "coursework": "Coursework",
    "education": "Education",
    "experience": "Experience",
    "leadership": "Leadership",
    "opensource": "Open Source",
    "opensourcecontributions": "Open Source",
    "professionalexperience": "Experience",
    "projects": "Projects",
    "publications": "Publications",
    "research": "Research",
    "skills": "Technical Skills",
    "summary": "Summary",
    "technicalskills": "Technical Skills",
    "technicalskillsinterests": "Technical Skills",
    "volunteering": "Volunteering",
    "workexperience": "Experience",
}
_DEFAULT_ORDER = (
    "Education",
    "Experience",
    "Projects",
    "Open Source",
    "Technical Skills",
    "Research",
    "Leadership",
    "Activities",
    "Publications",
    "Certifications",
    "Awards",
    "Coursework",
    "Volunteering",
    "Summary",
)
_UNICODE_REPLACEMENTS = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "--",
        "—": "---",
        "…": "...",
        "•": "",
        "●": "",
        "▪": "",
        "◦": "",
    }
)


@dataclass(frozen=True)
class GeneratedResumeTemplate:
    path: Path
    metadata_path: Path
    master_sha256: str
    style_sha256: str | None
    profile: ResumeLayoutProfile


@dataclass(frozen=True)
class ResumeLayoutProfile:
    """The template-derived resume shape that constrains every later tailoring run."""

    section_order: tuple[str, ...]
    editable_sections: tuple[str, ...]
    repeatable_sections: tuple[str, ...]
    section_entry_item_counts: dict[str, tuple[int, ...]]
    section_item_counts: dict[str, int]
    project_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "editable_sections": list(self.editable_sections),
            "project_count": self.project_count,
            "repeatable_sections": list(self.repeatable_sections),
            "section_entry_item_counts": {
                section: list(counts) for section, counts in self.section_entry_item_counts.items()
            },
            "section_item_counts": self.section_item_counts,
            "section_order": list(self.section_order),
        }


@dataclass(frozen=True)
class ResumeVisualProfile:
    """Measured presentation characteristics from a user-supplied PDF reference."""

    body_font_size_pt: float
    body_leading_pt: float
    bullet_left_margin_in: float
    bottom_margin_in: float
    entry_spacing_pt: float
    header_font_size_pt: float
    item_spacing_pt: float
    left_margin_in: float
    margin_in: float
    right_margin_in: float
    section_after_spacing_pt: float
    section_bold: bool
    section_before_spacing_pt: float
    section_font_size_pt: float
    section_rule: bool
    section_rule_width_pt: float
    section_small_caps: bool
    top_margin_in: float

    def as_json(self) -> dict[str, object]:
        return {
            "body_font_size_pt": self.body_font_size_pt,
            "body_leading_pt": self.body_leading_pt,
            "bullet_left_margin_in": self.bullet_left_margin_in,
            "bottom_margin_in": self.bottom_margin_in,
            "entry_spacing_pt": self.entry_spacing_pt,
            "header_font_size_pt": self.header_font_size_pt,
            "item_spacing_pt": self.item_spacing_pt,
            "left_margin_in": self.left_margin_in,
            "margin_in": self.margin_in,
            "right_margin_in": self.right_margin_in,
            "section_after_spacing_pt": self.section_after_spacing_pt,
            "section_bold": self.section_bold,
            "section_before_spacing_pt": self.section_before_spacing_pt,
            "section_font_size_pt": self.section_font_size_pt,
            "section_rule": self.section_rule,
            "section_rule_width_pt": self.section_rule_width_pt,
            "section_small_caps": self.section_small_caps,
            "top_margin_in": self.top_margin_in,
        }


def _section_name(line: str) -> str | None:
    line = line.removeprefix(_LAYOUT_INDENT_MARKER).replace(_LAYOUT_COLUMN_MARKER, "")
    normalized = _SECTION_KEY.sub("", line.casefold().rstrip(":").strip())
    return _SECTION_ALIASES.get(normalized)


def _plain_line(line: str) -> str:
    return line.removeprefix(_LAYOUT_INDENT_MARKER)


def _flatten_columns(line: str) -> str:
    return _plain_line(line).replace(_LAYOUT_COLUMN_MARKER, " | ")


def _split_columns(line: str) -> tuple[str, str]:
    left, separator, right = _plain_line(line).partition(_LAYOUT_COLUMN_MARKER)
    return left.strip(), right.strip() if separator else ""


def _is_layout_indented(line: str) -> bool:
    return line.startswith(_LAYOUT_INDENT_MARKER)


def _source_lines(source: ResumeSource) -> list[str]:
    raw_lines = source.text.splitlines()
    if source.format == "pdf":
        explicit_bullets = any(_BULLET_PREFIX.match(raw.strip()) is not None for raw in raw_lines)
        folded: list[str] = []
        pending = ""
        pending_indent = -1
        pending_is_bullet = False
        for raw in raw_lines:
            if not raw.strip() or _PAGE_MARKER.fullmatch(raw.strip()):
                if pending:
                    folded.append(pending)
                    pending = ""
                    pending_indent = -1
                    pending_is_bullet = False
                continue
            indent = len(raw) - len(raw.lstrip())
            is_bullet = (
                _BULLET_PREFIX.match(raw.strip()) is not None if explicit_bullets else indent > 0
            )
            columns = [
                _SPACE.sub(" ", part).strip()
                for part in re.split(r"\s{2,}", raw.strip())
                if part.strip()
            ]
            line = _LAYOUT_COLUMN_MARKER.join(columns)
            continuation = (
                bool(pending)
                and pending_is_bullet
                and (
                    (explicit_bullets and not is_bullet and indent >= pending_indent)
                    or (not explicit_bullets and is_bullet and indent == pending_indent)
                )
                and not pending.endswith((".", "!", "?"))
            )
            if continuation:
                pending = f"{pending} {line}"
                continue
            if pending:
                folded.append(pending)
            pending = f"{_LAYOUT_INDENT_MARKER}{line}" if is_bullet else line
            pending_indent = indent
            pending_is_bullet = is_bullet
        if pending:
            folded.append(pending)
        raw_lines = folded

    lines: list[str] = []
    for raw in raw_lines:
        if source.format == "tex":
            raw = latex_to_text(raw)
        indented = _is_layout_indented(raw)
        line = _SPACE.sub(" ", _plain_line(raw)).strip()
        if not line or _PAGE_MARKER.fullmatch(line):
            continue
        line = _BULLET_PREFIX.sub("", line).strip()
        if line:
            lines.append(f"{_LAYOUT_INDENT_MARKER}{line}" if indented else line)
    return lines


def _partition_master(source: ResumeSource) -> tuple[list[str], list[tuple[str, list[str]]]]:
    header: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in _source_lines(source):
        section = _section_name(line)
        if section is not None:
            if current_name is not None:
                sections.append((current_name, current_lines))
            current_name = section
            current_lines = []
        elif current_name is None:
            header.append(line)
        else:
            if current_name in {"Experience", "Projects"} and len(line) > 260:
                fragments = [
                    part.strip() for part in _SENTENCE_BOUNDARY.split(line) if part.strip()
                ]
                current_lines.extend(fragments)
            else:
                current_lines.append(line)
    if current_name is not None:
        sections.append((current_name, current_lines))

    if not sections:
        # A text extractor may omit visual headings. Keep a small contact header and place every
        # remaining approved line in Experience rather than dropping or inventing content.
        header_count = min(3, max(1, len(header) // 5)) if header else 0
        body = header[header_count:]
        header = header[:header_count]
        sections = [("Experience", body)]

    merged: dict[str, list[str]] = {}
    source_order: list[str] = []
    for name, lines in sections:
        if name not in merged:
            merged[name] = []
            source_order.append(name)
        merged[name].extend(lines)
    skill_lines = merged.get("Technical Skills")
    if skill_lines:
        joined_skills: list[str] = []
        for line in skill_lines:
            plain = _plain_line(line)
            if ":" not in plain and joined_skills:
                separator = " " if joined_skills[-1].rstrip().endswith(",") else ", "
                joined_skills[-1] = f"{joined_skills[-1]}{separator}{plain}"
            else:
                joined_skills.append(plain)
        merged["Technical Skills"] = joined_skills
    return header, [(name, merged[name]) for name in source_order]


def _style_order(style: ResumeSource | None) -> list[str]:
    if style is None:
        return []
    observed: list[str] = []
    for line in _source_lines(style):
        section = _section_name(line)
        if section is not None and section not in observed:
            observed.append(section)
    return observed


def _ordered_sections(
    sections: list[tuple[str, list[str]]], style: ResumeSource | None
) -> list[tuple[str, list[str]]]:
    content = {name: lines for name, lines in sections}
    preferred = _style_order(style)
    # A supplied style resume defines the desired section shape. Its wording remains excluded;
    # only matching factual sections from the master are eligible for the generated resume. When
    # no style exists, the built-in Jake layout owns ordering; master layout never becomes style.
    order = preferred if preferred else list(_DEFAULT_ORDER)
    unique_order = list(dict.fromkeys(name for name in order if name in content))
    return [(name, content[name]) for name in unique_order]


def infer_resume_layout_profile(source: str) -> ResumeLayoutProfile:
    """Infer section capabilities from a rendered/generated LaTeX template."""
    document_start = source.find(r"\begin{document}")
    document = source[document_start:] if document_start >= 0 else source
    matches = list(re.finditer(r"^\s*\\section\{(?P<name>[^}]+)\}\s*$", document, re.MULTILINE))
    section_order: list[str] = []
    section_entry_item_counts: dict[str, tuple[int, ...]] = {}
    section_item_counts: dict[str, int] = {}
    editable_sections: list[str] = []
    repeatable_sections: list[str] = []
    project_count = 0
    for index, match in enumerate(matches):
        name = latex_to_text(match.group("name")).strip()
        if not name:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document)
        body = document[match.end() : end]
        item_count = len(re.findall(r"\\resumeItem\s*\{", body))
        normalized = _SECTION_KEY.sub("", name.casefold())
        heading_command = (
            "resumeProjectHeading"
            if normalized == "projects"
            else "resumeSubheading"
            if normalized == "experience"
            else ""
        )
        entry_counts: tuple[int, ...] = ()
        if heading_command:
            headings = list(re.finditer(rf"\\{heading_command}\s*\{{", body))
            entry_counts = tuple(
                len(
                    re.findall(
                        r"\\resumeItem\s*\{",
                        body[
                            heading.end() : (
                                headings[heading_index + 1].start()
                                if heading_index + 1 < len(headings)
                                else len(body)
                            )
                        ],
                    )
                )
                for heading_index, heading in enumerate(headings)
            )
        section_order.append(name)
        section_entry_item_counts[name] = entry_counts
        section_item_counts[name] = item_count
        if item_count:
            repeatable_sections.append(name)
        if normalized in {"experience", "projects", "technicalskills"}:
            editable_sections.append(name)
        if normalized == "projects":
            project_count = len(re.findall(r"\\resumeProjectHeading\s*\{", body))
    return ResumeLayoutProfile(
        section_order=tuple(section_order),
        editable_sections=tuple(editable_sections),
        repeatable_sections=tuple(repeatable_sections),
        section_entry_item_counts=section_entry_item_counts,
        section_item_counts=section_item_counts,
        project_count=max(1, project_count),
    )


def infer_source_layout_profile(source: ResumeSource) -> ResumeLayoutProfile:
    """Infer the visible content budget of a PDF, DOCX, or TeX style reference."""
    if source.format == "tex" and r"\section{" in source.text:
        return infer_resume_layout_profile(source.text)
    _, sections = _partition_master(source)
    section_order = tuple(name for name, _ in sections)
    item_counts = {
        name: sum(_is_layout_indented(line) for line in lines) for name, lines in sections
    }
    entry_item_counts = {name: _grouped_entry_item_counts(lines) for name, lines in sections}
    repeatable = tuple(name for name in section_order if item_counts[name])
    editable = tuple(
        name
        for name in section_order
        if _SECTION_KEY.sub("", name.casefold()) in {"experience", "projects", "technicalskills"}
    )
    project_lines = next((lines for name, lines in sections if name == "Projects"), [])
    return ResumeLayoutProfile(
        section_order=section_order,
        editable_sections=editable,
        repeatable_sections=repeatable,
        section_entry_item_counts=entry_item_counts,
        section_item_counts=item_counts,
        project_count=max(1, _grouped_entry_count(project_lines)),
    )


def _grouped_entry_count(lines: list[str]) -> int:
    return len(_grouped_entry_item_counts(lines))


def _grouped_entry_item_counts(lines: list[str]) -> tuple[int, ...]:
    counts: list[int] = []
    current = 0
    for line in lines:
        if _is_layout_indented(line):
            current += 1
        elif current:
            counts.append(current)
            current = 0
    if current:
        counts.append(current)
    return tuple(counts)


def _observed_project_count(source: ResumeSource) -> int:
    """Count project groups from a user's explicit layout source without using its claims."""
    if source.format == "tex":
        command_count = len(re.findall(r"\\resumeProjectHeading\s*\{", source.text))
        if command_count:
            return command_count
    _, sections = _partition_master(source)
    project_lines = next((lines for name, lines in sections if name == "Projects"), [])
    if not project_lines:
        return 0
    return _grouped_entry_count(project_lines)


def _pdf_visual_profile(source: ResumeSource) -> ResumeVisualProfile | None:
    """Measure typography and margins from the first page of a PDF style reference."""
    if source.format != "pdf" or not source.path.is_file():
        return None
    try:
        page = PdfReader(source.path).pages[0]
    except (OSError, PdfReadError, IndexError):
        return None

    fragments: list[tuple[str, float, float, float, str]] = []
    horizontal_rules: list[tuple[float, float, float, float]] = []
    current_line_width = 0.4
    path_start: tuple[float, float] | None = None
    path_end: tuple[float, float] | None = None

    def transformed_point(x: float, y: float, matrix: Any) -> tuple[float, float]:
        return (
            x * float(matrix[0]) + y * float(matrix[2]) + float(matrix[4]),
            x * float(matrix[1]) + y * float(matrix[3]) + float(matrix[5]),
        )

    def visitor(
        text: str,
        current_transform: Any,
        text_matrix: Any,
        font: Any,
        font_size: float,
    ) -> None:
        rendered = _SPACE.sub(" ", text).strip()
        if not rendered:
            return
        try:
            text_x = float(text_matrix[4])
            text_y = float(text_matrix[5])
            x, y = transformed_point(text_x, text_y, current_transform)
            size = float(font_size)
        except (IndexError, TypeError, ValueError):
            try:
                x = float(text_matrix[4])
                y = float(text_matrix[5])
                size = float(font_size)
            except (IndexError, TypeError, ValueError):
                return
        font_name = str(font.get("/BaseFont", "")) if isinstance(font, dict) else ""
        fragments.append((rendered, x, y, size, font_name))

    def drawing_visitor(operator: bytes, operands: Any, matrix: Any, _text_matrix: Any) -> None:
        nonlocal current_line_width, path_end, path_start
        try:
            if operator == b"w":
                current_line_width = float(operands[0])
            elif operator == b"m":
                path_start = transformed_point(float(operands[0]), float(operands[1]), matrix)
                path_end = path_start
            elif operator == b"l":
                path_end = transformed_point(float(operands[0]), float(operands[1]), matrix)
            elif operator in {b"S", b"s"} and path_start is not None and path_end is not None:
                if abs(path_start[1] - path_end[1]) <= 1:
                    horizontal_rules.append(
                        (path_start[0], path_end[0], path_start[1], current_line_width)
                    )
                path_start = None
                path_end = None
        except (IndexError, TypeError, ValueError):
            path_start = None
            path_end = None

    try:
        page.extract_text(visitor_text=visitor, visitor_operand_before=drawing_visitor)
    except (KeyError, TypeError, ValueError):
        return None
    candidates = [item for item in fragments if 8 <= item[3] <= 14]
    if not candidates:
        return None
    weighted_sizes = [item[3] for item in candidates for _ in range(max(1, min(len(item[0]), 80)))]
    body_size = float(statistics.median(weighted_sizes))
    section_fragments = [item for item in candidates if _section_name(item[0]) is not None]
    section_size = (
        float(statistics.median(item[3] for item in section_fragments))
        if section_fragments
        else min(14.0, body_size * 1.2)
    )
    section_fonts = [item[4].casefold() for item in section_fragments]
    margin_x = min(
        (item[1] for item in section_fragments), default=min(item[1] for item in candidates)
    )
    page_width = float(page.mediabox.width)
    broad_rules = [rule for rule in horizontal_rules if abs(rule[1] - rule[0]) >= page_width * 0.5]
    left_margin_pt = (
        float(statistics.median(min(rule[0], rule[1]) for rule in broad_rules))
        if broad_rules
        else margin_x
    )
    right_margin_pt = (
        page_width - float(statistics.median(max(rule[0], rule[1]) for rule in broad_rules))
        if broad_rules
        else margin_x
    )
    left_margin_in = max(0.3, min(1.25, left_margin_pt / 72.0))
    right_margin_in = max(0.3, min(1.25, right_margin_pt / 72.0))
    margin_in = float(statistics.median((left_margin_in, right_margin_in)))

    body_baselines = sorted(
        (item[2] for item in candidates if abs(item[3] - body_size) <= 0.35), reverse=True
    )
    clustered: list[float] = []
    for baseline in body_baselines:
        if clustered and abs(clustered[-1] - baseline) <= 2:
            clustered[-1] = (clustered[-1] + baseline) / 2
        else:
            clustered.append(baseline)
    gaps = [
        first - second
        for first, second in zip(clustered, clustered[1:], strict=False)
        if 8 <= first - second <= 18
    ]
    body_leading = max(body_size + 1, min(body_size + 3, body_size * 1.2))
    observed_gap = float(statistics.median(gaps)) if gaps else body_leading
    bullet_fragments = [
        item
        for item in fragments
        if item[3] < 8 and item[0].strip() in {"•", "●", "▪", "◦", "‣", "⁃"}
    ]
    bullet_baselines = sorted({round(item[2], 2) for item in bullet_fragments}, reverse=True)
    bullet_gaps = [
        first - second
        for first, second in zip(bullet_baselines, bullet_baselines[1:], strict=False)
        if body_leading <= first - second <= body_leading + 6
    ]
    observed_bullet_gap = float(statistics.median(bullet_gaps)) if bullet_gaps else observed_gap
    item_spacing = max(0.0, min(4.0, observed_bullet_gap - body_leading))

    line_baselines: list[float] = []
    for baseline in sorted((item[2] for item in candidates), reverse=True):
        if line_baselines and abs(line_baselines[-1] - baseline) <= 2:
            line_baselines[-1] = (line_baselines[-1] + baseline) / 2
        else:
            line_baselines.append(baseline)
    before_gaps: list[float] = []
    after_gaps: list[float] = []
    for section in section_fragments:
        closest = min(
            range(len(line_baselines)), key=lambda index: abs(line_baselines[index] - section[2])
        )
        if closest:
            before_gaps.append(line_baselines[closest - 1] - line_baselines[closest])
        if closest + 1 < len(line_baselines):
            after_gaps.append(line_baselines[closest] - line_baselines[closest + 1])
    section_before_spacing = max(
        0.0,
        min(
            12.0,
            (float(statistics.median(before_gaps)) if before_gaps else body_leading * 1.5)
            - body_leading * 2,
        ),
    )
    section_after_spacing = max(
        0.0,
        min(
            10.0,
            (float(statistics.median(after_gaps)) if after_gaps else body_leading) - body_leading,
        ),
    )

    heading_fragments = [
        item
        for item in candidates
        if "bold" in item[4].casefold()
        and margin_x + 3 <= item[1] <= margin_x + 20
        and _section_name(item[0]) is None
    ]
    entry_gaps = [
        bullet_y - heading[2]
        for heading in heading_fragments
        for bullet_y in bullet_baselines
        if body_leading < bullet_y - heading[2] <= body_leading + 15
    ]
    entry_spacing = max(
        0.0,
        min(
            8.0,
            (float(statistics.median(entry_gaps)) if entry_gaps else body_leading)
            - body_leading
            - item_spacing
            - 4,
        ),
    )
    entry_x = min((item[1] for item in heading_fragments), default=margin_x + 10.8)
    bullet_x = min((item[1] for item in bullet_fragments), default=entry_x + 13)
    bullet_left_margin_in = max(0.12, min(0.35, (bullet_x - entry_x) / 72.0))
    section_rule_matches = sum(
        any(0 <= section[2] - rule[2] <= 8 for rule in broad_rules) for section in section_fragments
    )
    section_rule = bool(section_fragments) and section_rule_matches >= max(
        1, len(section_fragments) // 2
    )
    section_rule_width = (
        float(statistics.median(rule[3] for rule in broad_rules)) if broad_rules else 0.4
    )
    header_size = max((item[3] for item in fragments), default=body_size * 2)
    return ResumeVisualProfile(
        body_font_size_pt=round(body_size, 2),
        body_leading_pt=round(body_leading, 2),
        bullet_left_margin_in=round(bullet_left_margin_in, 3),
        bottom_margin_in=round(margin_in, 2),
        entry_spacing_pt=round(entry_spacing, 2),
        header_font_size_pt=round(max(body_size * 1.6, min(28.0, header_size)), 2),
        item_spacing_pt=round(item_spacing, 2),
        left_margin_in=round(left_margin_in, 2),
        margin_in=round(margin_in, 2),
        right_margin_in=round(right_margin_in, 2),
        section_after_spacing_pt=round(section_after_spacing, 2),
        section_bold=any("bold" in name for name in section_fonts),
        section_before_spacing_pt=round(section_before_spacing, 2),
        section_font_size_pt=round(section_size, 2),
        section_rule=section_rule,
        section_rule_width_pt=round(section_rule_width, 2),
        section_small_caps=any("caps" in name for name in section_fonts),
        top_margin_in=round(margin_in, 2),
    )


def _latex_escape(value: str) -> str:
    value = value.replace(_LAYOUT_COLUMN_MARKER, " ")
    value = value.translate(_UNICODE_REPLACEMENTS)
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "_": r"\_",
        "%": r"\%",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in value)


def _measure(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _render_header(lines: list[str], visual: ResumeVisualProfile | None) -> str:
    if not lines:
        return ""
    if visual is None:
        name = rf"{{\LARGE \textbf{{{_latex_escape(_flatten_columns(lines[0]))}}}}}\\[2pt]"
        contact_prefix = r"\small "
    else:
        header_size = _measure(visual.header_font_size_pt)
        header_leading = _measure(visual.header_font_size_pt * 1.2)
        name = (
            rf"{{\fontsize{{{header_size}pt}}{{{header_leading}pt}}\selectfont\bfseries "
            rf"{_latex_escape(_flatten_columns(lines[0]))}}}\\[2pt]"
        )
        contact_prefix = (
            rf"\fontsize{{{_measure(visual.body_font_size_pt)}pt}}"
            rf"{{{_measure(visual.body_leading_pt)}pt}}\selectfont "
        )
    rendered = [
        r"\begin{center}",
        name,
    ]
    if len(lines) > 1:
        rendered.append(
            contact_prefix
            + r" \enspace\textbar\enspace ".join(
                _latex_escape(_flatten_columns(line)) for line in lines[1:]
            )
        )
    rendered.append(r"\end{center}")
    return "\n".join(rendered)


def _split_skill(line: str) -> tuple[str, str]:
    line = _flatten_columns(line)
    category, separator, values = line.partition(":")
    if separator and category.strip() and values.strip() and len(category.strip()) <= 40:
        return category.strip(), values.strip()
    return "Skills", line


def _render_grouped_section(name: str, lines: list[str]) -> list[str]:
    """Render layout-derived headings and their indented bullets as stable semantic groups."""
    heading_command = "resumeProjectHeading" if name == "Projects" else "resumeSubheading"
    rendered = [r"\resumeSubHeadingListStart"]
    pending_details: list[str] = []
    groups: list[tuple[list[str], list[str]]] = []
    current_details: list[str] = []
    current_bullets: list[str] = []
    for line in lines:
        if _is_layout_indented(line):
            current_bullets.append(_flatten_columns(line))
            continue
        if current_bullets:
            groups.append((current_details, current_bullets))
            current_details = []
            current_bullets = []
        current_details.append(_plain_line(line))
    if current_bullets:
        groups.append((current_details, current_bullets))
    else:
        pending_details = current_details

    for details, bullets in groups:
        # Category labels can precede a real project heading. Keep the closest line as the entry
        # title and render earlier labels as lightweight approved context.
        if heading_command == "resumeProjectHeading":
            for detail in details[:-1]:
                category, category_right = _split_columns(detail)
                rendered.append(
                    rf"\resumeProjectHeading{{\textit{{{_latex_escape(category)}}}}}"
                    rf"{{{_latex_escape(category_right)}}}"
                )
            title = details[-1] if details else name
            title_left, title_right = _split_columns(title)
            rendered.append(
                rf"\resumeProjectHeading{{\textbf{{{_latex_escape(title_left)}}}}}"
                rf"{{{_latex_escape(title_right)}}}"
            )
        else:
            title, title_right = _split_columns(details[0] if details else name)
            subtitle, subtitle_right = _split_columns(details[1]) if len(details) > 1 else ("", "")
            rendered.append(
                rf"\resumeSubheading{{{_latex_escape(title)}}}{{{_latex_escape(title_right)}}}"
                rf"{{{_latex_escape(subtitle)}}}{{{_latex_escape(subtitle_right)}}}"
            )
        rendered.append(r"\resumeItemListStart")
        rendered.extend(rf"\resumeItem{{{_latex_escape(bullet)}}}" for bullet in bullets)
        rendered.append(r"\resumeItemListEnd")
    if heading_command == "resumeProjectHeading":
        for line in pending_details:
            detail, detail_right = _split_columns(line)
            rendered.append(
                rf"\resumeProjectHeading{{\textit{{{_latex_escape(detail)}}}}}"
                rf"{{{_latex_escape(detail_right)}}}"
            )
    else:
        for line in pending_details:
            detail, detail_right = _split_columns(line)
            rendered.append(
                rf"\resumeSubheading{{{_latex_escape(detail)}}}"
                rf"{{{_latex_escape(detail_right)}}}{{}}{{}}"
            )
    rendered.append(r"\resumeSubHeadingListEnd")
    return rendered


def _render_section(name: str, lines: list[str]) -> str:
    rendered = [rf"\section{{{_latex_escape(name)}}}"]
    if name == "Technical Skills":
        if not lines:
            rendered.append("% No approved skills were extracted from the master resume.")
        else:
            for line in lines:
                category, values = _split_skill(line)
                rendered.append(
                    rf"\textbf{{{_latex_escape(category)}:}} {_latex_escape(values)} \\"
                )
        return "\n".join(rendered)

    if not lines:
        rendered.append(f"% No approved {name.casefold()} content was extracted.")
        return "\n".join(rendered)
    if name == "Education":
        institution, location = _split_columns(lines[0])
        rendered.append(
            rf"\resumeEducationHeading{{{_latex_escape(institution)}}}"
            rf"{{{_latex_escape(location)}}}"
        )
        if len(lines) > 1:
            degree, date = _split_columns(lines[1])
            rendered.append(
                rf"\resumeEducationDetail{{{_latex_escape(degree)}}}{{{_latex_escape(date)}}}"
            )
        rendered.extend(
            rf"\resumeDetail{{{_latex_escape(_flatten_columns(line))}}}" for line in lines[2:]
        )
        return "\n".join(rendered)
    if name in {"Experience", "Projects"} and any(_is_layout_indented(line) for line in lines):
        rendered.extend(_render_grouped_section(name, lines))
        return "\n".join(rendered)
    rendered.extend(
        [
            r"\begin{itemize}[leftmargin=0.18in,label=\textbullet]",
            *[rf"  \resumeItem{{{_latex_escape(_flatten_columns(line))}}}" for line in lines],
            r"\end{itemize}",
        ]
    )
    return "\n".join(rendered)


def _render_template(master: ResumeSource, style: ResumeSource | None) -> str:
    header, parsed_sections = _partition_master(master)
    sections = _ordered_sections(parsed_sections, style)
    visual = _pdf_visual_profile(style) if style is not None else None
    font_size = "10pt"
    geometry = (
        "left="
        f"{_measure(visual.left_margin_in)}in,right={_measure(visual.right_margin_in)}in,"
        f"top={_measure(visual.top_margin_in)}in,bottom={_measure(visual.bottom_margin_in)}in"
        if visual is not None
        else "margin=0.50in"
    )
    item_spacing = _measure(visual.item_spacing_pt) if visual is not None else "1"
    # enumitem positions the bullet glyph slightly inside its configured left margin. Compensate
    # for that label box so the rendered glyph offset matches the measured reference PDF.
    bullet_left_margin = (
        _measure(visual.bullet_left_margin_in + 0.024) if visual is not None else "0.18"
    )
    entry_spacing = _measure(visual.entry_spacing_pt) if visual is not None else "0"
    section_before_spacing = (
        _measure(visual.section_before_spacing_pt) if visual is not None else "5"
    )
    section_after_spacing = _measure(visual.section_after_spacing_pt) if visual is not None else "2"
    if visual is None:
        section_style = r"\large\bfseries"
    else:
        section_style = (
            rf"\fontsize{{{_measure(visual.section_font_size_pt)}pt}}"
            rf"{{{_measure(visual.section_font_size_pt * 1.2)}pt}}\selectfont"
        )
        if visual.section_small_caps:
            section_style += r"\scshape"
        if visual.section_bold:
            section_style += r"\bfseries"
    body = "\n\n".join(_render_section(name, lines) for name, lines in sections)
    rendered_header = _render_header(header, visual)
    section_rule = (
        r"[\ergasectionrule]"
        if visual is not None and visual.section_rule
        else (r"[\titlerule]" if visual is None else "")
    )
    section_rule_definition = (
        rf"\newcommand{{\ergasectionrule}}{{\titlerule[{_measure(visual.section_rule_width_pt)}pt]}}"
        "\n"
        if visual is not None and visual.section_rule
        else ""
    )
    preamble = (
        f"% Generated by Erga from approved master resume SHA-256: {master.sha256}\n"
        "% Factual text below comes only from that approved master source.\n"
        f"{_SEMANTIC_TEMPLATE_MARKER}\n"
        + (f"{_VISUAL_SPACING_MARKER}\n" if style is not None else "")
        + rf"\documentclass[letterpaper,{font_size}]{{article}}"
        "\n"
        r"\usepackage[T1]{fontenc}"
        "\n"
        r"\usepackage[utf8]{inputenc}"
        "\n"
        rf"\usepackage[{geometry}]{{geometry}}"
        "\n"
        r"\usepackage[hidelinks]{hyperref}"
        "\n"
        r"\usepackage{enumitem}"
        "\n"
        r"\usepackage{titlesec}"
        "\n"
        r"\pagestyle{empty}"
        "\n"
        r"\raggedbottom"
        "\n"
        r"\sloppy"
        "\n"
        r"\setlength{\parindent}{0pt}"
        "\n"
        r"\setlength{\parskip}{0pt}"
        "\n"
        r"\setlist[itemize]{nosep}"
        "\n"
        + section_rule_definition
        + rf"\titleformat{{\section}}{{{section_style}}}{{}}{{0em}}{{}}{section_rule}"
        "\n"
        rf"\titlespacing*{{\section}}{{0pt}}{{{section_before_spacing}pt}}"
        rf"{{{section_after_spacing}pt}}"
        "\n"
        r"\newcommand{\resumeItem}[1]{\item #1}"
        "\n"
        r"\newcommand{\resumeDetail}[1]{\noindent #1\\[-1pt]}"
        "\n"
        r"\newcommand{\resumeEducationHeading}[2]{\textbf{#1}\hfill #2\\[-1pt]}"
        "\n"
        r"\newcommand{\resumeEducationDetail}[2]{\textit{#1}\hfill\textit{#2}\\[-1pt]}"
        "\n"
        r"\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0in,label={},nosep]}"
        "\n"
        r"\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}"
        "\n"
        rf"\newcommand{{\resumeItemListStart}}{{\begin{{itemize}}[leftmargin={bullet_left_margin}in,"
        r"label=\textbullet,"
        rf"itemsep={item_spacing}pt,topsep={item_spacing}pt,parsep=0pt,partopsep=0pt]}}"
        "\n"
        rf"\newcommand{{\resumeItemListEnd}}{{\end{{itemize}}\vspace{{{entry_spacing}pt}}}}"
        "\n"
        r"\newcommand{\resumeSubheading}[4]{\item[] \textbf{#1}\hfill #2\\[-1pt]"
        r"\textit{#3}\hfill\textit{#4}}"
        "\n"
        r"\newcommand{\resumeProjectHeading}[2]{\item[] #1\hfill #2}"
        "\n"
        r"\begin{document}"
        "\n"
    )
    if visual is not None:
        preamble += (
            rf"\fontsize{{{_measure(visual.body_font_size_pt)}pt}}"
            rf"{{{_measure(visual.body_leading_pt)}pt}}\selectfont"
            "\n"
        )
    elif style is None:
        preamble += r"\fontsize{10pt}{12pt}\selectfont" "\n"
    if rendered_header:
        preamble += f"{rendered_header}\n\n"
    return preamble + body + "\n\n" + r"\end{document}" + "\n"


def generate_latex_template(
    master: ResumeSource,
    *,
    data_dir: Path,
    style: ResumeSource | None = None,
) -> GeneratedResumeTemplate:
    """Create or replace a content-addressed private template from approved local sources."""
    identity = hashlib.sha256(
        f"{TEMPLATE_GENERATION_VERSION}:{master.sha256}:{style.sha256 if style else ''}".encode()
    ).hexdigest()
    target_dir = data_dir.expanduser().absolute() / "resume-templates" / identity
    target_dir.mkdir(parents=True, exist_ok=True)
    restrict_private_directory(target_dir)
    target = target_dir / "resume.tex"
    metadata_path = target_dir / "template.json"
    rendered = _render_template(master, style)
    profile = infer_resume_layout_profile(rendered)
    style_profile = infer_source_layout_profile(style) if style is not None else None
    visual_profile = _pdf_visual_profile(style) if style is not None else None
    if "Projects" in profile.editable_sections:
        # An explicit style/template owns its observed number of project slots. Without one,
        # Erga's one-page default starts at four and lets rendered packing decide bullet density.
        explicit_style_count = _observed_project_count(style) if style is not None else 0
        project_count = explicit_style_count or min(profile.project_count, 4)
        profile = replace(profile, project_count=max(1, project_count))
    metadata = {
        "generation_version": TEMPLATE_GENERATION_VERSION,
        "master_sha256": master.sha256,
        "style_sha256": style.sha256 if style else None,
        "styling_source": "user-template" if style else "erga-default-jake",
        "style_used_for_facts": False,
        "template_path": str(target),
        "layout_profile": profile.as_json(),
        "style_layout_profile": style_profile.as_json() if style_profile else None,
        "visual_style_profile": visual_profile.as_json() if visual_profile else None,
    }

    for path, content in (
        (target, rendered),
        (metadata_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n"),
    ):
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target_dir, prefix=f".{path.name}-", delete=False
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            restrict_private_file(temporary_path)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
        restrict_private_file(path)
    return GeneratedResumeTemplate(
        target,
        metadata_path,
        master.sha256,
        style.sha256 if style else None,
        profile,
    )


def ensure_resume_template(config_path: Path) -> Path:
    """Return an existing template or generate one from the configured approved master."""
    config = load_config(config_path)
    existing = config.resume.template_path
    existing_is_valid = (
        existing is not None and existing.is_file() and existing.suffix.casefold() == ".tex"
    )
    metadata_path: Path | None = None
    if existing_is_valid:
        assert existing is not None
        metadata_path = existing.with_name("template.json")
    if existing_is_valid and metadata_path is not None and not metadata_path.is_file():
        assert existing is not None
        return existing
    if config.resume.master_path is None:
        if existing_is_valid:
            assert existing is not None
            return existing
        raise ValueError("a master resume PDF, DOCX, or LaTeX source must be configured")
    master = load_resume_source(config.resume.master_path)
    style = (
        load_resume_source(config.resume.reference_path)
        if config.resume.reference_path is not None
        else None
    )
    if existing_is_valid:
        assert existing is not None and metadata_path is not None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = None
        if (
            isinstance(metadata, dict)
            and metadata.get("generation_version") == TEMPLATE_GENERATION_VERSION
            and metadata.get("master_sha256") == master.sha256
            and metadata.get("style_sha256") == (style.sha256 if style else None)
        ):
            return existing
    generated = generate_latex_template(master, data_dir=config.data_dir, style=style)
    projects_enabled = any(
        _SECTION_KEY.sub("", section.casefold()) == "projects"
        for section in generated.profile.editable_sections
    )
    update_settings(
        config_path,
        {
            "template_path": str(generated.path),
            "editable_sections": list(generated.profile.editable_sections),
            "project_count": generated.profile.project_count,
            "project_selection_mode": (
                "inventory_required"
                if projects_enabled and config.resume.project_inventory_path is not None
                else "inventory_optional"
                if projects_enabled
                else "template_only"
            ),
        },
    )
    return generated.path


def reset_resume_template(config_path: Path) -> Path:
    """Clear custom style choices and apply the default Jake layout to approved master facts."""
    config = load_config(config_path)
    if config.resume.master_path is None or not config.resume.master_path.is_file():
        raise ValueError("import a master resume before resetting the template")
    update_settings(
        config_path,
        {
            "reference_path": "",
            "template_path": "",
        },
    )
    template_path = ensure_resume_template(config_path)
    return template_path
