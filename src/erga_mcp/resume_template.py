"""Generate a private, self-contained LaTeX template from an approved resume source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from .config import load_config
from .private_files import restrict_private_directory, restrict_private_file
from .resume_settings import update_settings
from .resume_sources import ResumeSource, load_resume_source
from .resume_tailoring import latex_to_text

TEMPLATE_GENERATION_VERSION = 11
_PAGE_MARKER = re.compile(r"^\[Page \d+\]$")
_BULLET_PREFIX = re.compile(r"^(?:[•●▪◦‣⁃*]|[-–—]\s)\s*")
_SPACE = re.compile(r"\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9])")
_SECTION_KEY = re.compile(r"[^a-z0-9]+")
_LAYOUT_INDENT_MARKER = "[[ERGA-LAYOUT-INDENT]]"
_LAYOUT_COLUMN_MARKER = "[[ERGA-LAYOUT-COLUMN]]"
_SEMANTIC_TEMPLATE_MARKER = "% Erga semantic resume template version: 11"
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
    section_item_counts: dict[str, int]
    project_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "editable_sections": list(self.editable_sections),
            "project_count": self.project_count,
            "repeatable_sections": list(self.repeatable_sections),
            "section_item_counts": self.section_item_counts,
            "section_order": list(self.section_order),
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
        folded: list[str] = []
        pending = ""
        pending_indent = -1
        for raw in raw_lines:
            if not raw.strip() or _PAGE_MARKER.fullmatch(raw.strip()):
                if pending:
                    folded.append(pending)
                    pending = ""
                    pending_indent = -1
                continue
            indent = len(raw) - len(raw.lstrip())
            columns = [
                _SPACE.sub(" ", part).strip()
                for part in re.split(r"\s{2,}", raw.strip())
                if part.strip()
            ]
            line = _LAYOUT_COLUMN_MARKER.join(columns)
            continuation = (
                bool(pending)
                and indent > 0
                and indent == pending_indent
                and not pending.endswith((".", "!", "?"))
            )
            if continuation:
                pending = f"{pending} {line}"
                continue
            if pending:
                folded.append(pending)
            pending = f"{_LAYOUT_INDENT_MARKER}{line}" if indent > 0 else line
            pending_indent = indent
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
    source_order = [name for name, _ in sections]
    preferred = _style_order(style)
    # A supplied style resume defines the desired section shape. Its wording remains excluded;
    # only matching factual sections from the master are eligible for the generated resume.
    order = preferred if preferred else [*source_order, *_DEFAULT_ORDER]
    unique_order = list(dict.fromkeys(name for name in order if name in content))
    return [(name, content[name]) for name in unique_order]


def infer_resume_layout_profile(source: str) -> ResumeLayoutProfile:
    """Infer section capabilities from a rendered/generated LaTeX template."""
    document_start = source.find(r"\begin{document}")
    document = source[document_start:] if document_start >= 0 else source
    matches = list(re.finditer(r"^\s*\\section\{(?P<name>[^}]+)\}\s*$", document, re.MULTILINE))
    section_order: list[str] = []
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
        section_order.append(name)
        section_item_counts[name] = item_count
        if item_count:
            repeatable_sections.append(name)
        normalized = _SECTION_KEY.sub("", name.casefold())
        if normalized in {"experience", "projects", "technicalskills"}:
            editable_sections.append(name)
        if normalized == "projects":
            project_count = len(re.findall(r"\\resumeProjectHeading\s*\{", body))
    return ResumeLayoutProfile(
        section_order=tuple(section_order),
        editable_sections=tuple(editable_sections),
        repeatable_sections=tuple(repeatable_sections),
        section_item_counts=section_item_counts,
        project_count=max(1, project_count),
    )


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
    groups = 0
    has_bullets = False
    for line in project_lines:
        if _is_layout_indented(line):
            has_bullets = True
        elif has_bullets:
            groups += 1
            has_bullets = False
    if has_bullets:
        groups += 1
    return groups


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


def _render_header(lines: list[str]) -> str:
    if not lines:
        return ""
    rendered = [
        r"\begin{center}",
        rf"{{\LARGE \textbf{{{_latex_escape(_flatten_columns(lines[0]))}}}}}\\[2pt]",
    ]
    if len(lines) > 1:
        rendered.append(
            r"\small "
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
    layout_source = style or master
    compact = (layout_source.page_count or 1) <= 1
    font_size = "10pt"
    margin = "0.50in" if compact else "0.60in"
    body = "\n\n".join(_render_section(name, lines) for name, lines in sections)
    rendered_header = _render_header(header)
    preamble = (
        f"% Generated by Erga from approved master resume SHA-256: {master.sha256}\n"
        "% Factual text below comes only from that approved master source.\n"
        f"{_SEMANTIC_TEMPLATE_MARKER}\n"
        rf"\documentclass[letterpaper,{font_size}]{{article}}"
        "\n"
        r"\usepackage[T1]{fontenc}"
        "\n"
        r"\usepackage[utf8]{inputenc}"
        "\n"
        rf"\usepackage[margin={margin}]{{geometry}}"
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
        r"\titleformat{\section}{\large\bfseries}{}{0em}{}[\titlerule]"
        "\n"
        r"\titlespacing*{\section}{0pt}{5pt}{2pt}"
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
        r"\newcommand{\resumeItemListStart}{\begin{itemize}[leftmargin=0.18in,label=\textbullet,"
        r"itemsep=1pt,topsep=1pt,parsep=0pt,partopsep=0pt]}"
        "\n"
        r"\newcommand{\resumeItemListEnd}{\end{itemize}}"
        "\n"
        r"\newcommand{\resumeSubheading}[4]{\item[] \textbf{#1}\hfill #2\\[-1pt]"
        r"\textit{#3}\hfill\textit{#4}}"
        "\n"
        r"\newcommand{\resumeProjectHeading}[2]{\item[] #1\hfill #2}"
        "\n"
        r"\begin{document}"
        "\n"
    )
    if compact:
        preamble += r"\fontsize{9pt}{10.4pt}\selectfont" "\n"
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
        "style_used_for_facts": False,
        "template_path": str(target),
        "layout_profile": profile.as_json(),
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
    """Clear custom style/template choices and regenerate the default from the master."""
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
