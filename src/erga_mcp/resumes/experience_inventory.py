from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from erga_mcp.models import Evidence
from erga_mcp.resumes.artifacts import latex_to_text

_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
_NUMBER = re.compile(r"(?<!\w)\$?\d[\d,]*(?:\.\d+)?(?:%|x|\+)?(?!\w)", re.I)
_CONTROL_SEQUENCE = re.compile(r"\\([A-Za-z@]+|.)")
_ALLOWED_CONTROL_SEQUENCES = frozenset(
    {"textbf", "textit", "href", "%", "&", "#", "$", "_", ",", "-", "/", "{", "}"}
)
_DISALLOWED_LATEX = ("\\input", "\\include", "\\write18", "\\immediate\\write")
_GIT_METRIC_KINDS = frozenset(
    {"implementation_scope", "test_inventory", "repository_fact", "interface_count"}
)


def _balanced_end(source: str, opening: int) -> int:
    depth = 0
    for position in range(opening, len(source)):
        escaped = position > 0 and source[position - 1] == "\\"
        if source[position] == "{" and not escaped:
            depth += 1
        elif source[position] == "}" and not escaped:
            depth -= 1
            if depth == 0:
                return position + 1
    raise ValueError("unterminated LaTeX argument in experience inventory source")


def _command_arguments_at(source: str, start: int, command: str) -> tuple[tuple[str, ...], int]:
    cursor = start + len(f"\\{command}")
    arguments: list[str] = []
    while cursor < len(source):
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != "{":
            break
        end = _balanced_end(source, cursor)
        arguments.append(source[cursor + 1 : end - 1])
        cursor = end
    return tuple(arguments), cursor


def _resume_item_contents(source: str) -> tuple[str, ...]:
    contents: list[str] = []
    cursor = 0
    needle = r"\resumeItem"
    while (start := source.find(needle, cursor)) >= 0:
        arguments, end = _command_arguments_at(source, start, "resumeItem")
        if arguments:
            contents.append(arguments[0])
            cursor = end
        else:
            cursor = start + len(needle)
    return tuple(contents)


@dataclass(frozen=True)
class ExperienceMetric:
    value: str
    provenance: str
    kind: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExperienceBullet:
    latex: str
    evidence_ids: tuple[str, ...]
    tags: tuple[str, ...] = ()
    metrics: tuple[ExperienceMetric, ...] = ()

    @property
    def text(self) -> str:
        return latex_to_text(self.latex)


@dataclass(frozen=True)
class ExperienceCandidate:
    id: str
    title: str
    company: str
    match_terms: tuple[str, ...]
    bullets: tuple[ExperienceBullet, ...]


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"experience inventory {label} must be a non-empty string")
    return value.strip()


def _strings(value: object, label: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"experience inventory {label} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if required and not result:
        raise ValueError(f"experience inventory {label} cannot be empty")
    return result


def _metric(value: object) -> ExperienceMetric:
    if not isinstance(value, dict):
        raise ValueError("experience inventory metrics must be objects")
    return ExperienceMetric(
        value=_string(value.get("value"), "metric value"),
        provenance=_string(value.get("provenance"), "metric provenance"),
        kind=_string(value.get("kind"), "metric kind"),
        evidence_ids=_strings(value.get("evidence_ids"), "metric evidence_ids", required=True),
    )


def _bullet(value: object) -> ExperienceBullet:
    if not isinstance(value, dict):
        raise ValueError("experience inventory bullets must be objects")
    raw_metrics = value.get("metrics", [])
    if not isinstance(raw_metrics, list):
        raise ValueError("experience inventory bullet metrics must be a list")
    return ExperienceBullet(
        latex=_string(value.get("latex"), "bullet latex"),
        evidence_ids=_strings(value.get("evidence_ids"), "bullet evidence_ids", required=True),
        tags=_strings(value.get("tags", []), "bullet tags"),
        metrics=tuple(_metric(item) for item in raw_metrics),
    )


def _candidate(value: object) -> ExperienceCandidate:
    if not isinstance(value, dict):
        raise ValueError("experience inventory entries must be objects")
    raw_bullets = value.get("bullets")
    if not isinstance(raw_bullets, list) or not raw_bullets:
        raise ValueError("experience inventory entries require at least one bullet")
    title = _string(value.get("title"), "title")
    company = _string(value.get("company"), "company")
    return ExperienceCandidate(
        id=_string(value.get("id"), "id"),
        title=title,
        company=company,
        match_terms=_strings(
            value.get("match_terms", [title, company]), "match_terms", required=True
        ),
        bullets=tuple(_bullet(item) for item in raw_bullets),
    )


def _validate_candidate(candidate: ExperienceCandidate, approved: dict[str, Evidence]) -> None:
    if _SAFE_ID.fullmatch(candidate.id) is None:
        raise ValueError("experience inventory IDs must be lowercase safe identifiers")
    for bullet in candidate.bullets:
        if any(marker in bullet.latex for marker in _DISALLOWED_LATEX):
            raise ValueError("experience inventory bullet contains a disallowed LaTeX command")
        unknown = {
            match.group(1)
            for match in _CONTROL_SEQUENCE.finditer(bullet.latex)
            if match.group(1) not in _ALLOWED_CONTROL_SEQUENCES
        }
        if unknown:
            raise ValueError("experience inventory bullet contains a disallowed LaTeX command")
        if any(evidence_id not in approved for evidence_id in bullet.evidence_ids):
            raise ValueError("experience inventory bullets require approved evidence IDs")
        declared = {metric.value.casefold() for metric in bullet.metrics}
        observed = {match.group(0).casefold() for match in _NUMBER.finditer(bullet.text)}
        if observed - declared:
            missing = ", ".join(sorted(observed - declared))
            raise ValueError(f"experience inventory numeric claims require provenance: {missing}")
        for metric in bullet.metrics:
            if metric.provenance not in {"user_confirmed", "git_observed"}:
                raise ValueError(
                    "experience metric provenance must be user_confirmed or git_observed"
                )
            if any(evidence_id not in approved for evidence_id in metric.evidence_ids):
                raise ValueError("experience metrics require approved evidence IDs")
            if metric.provenance == "git_observed":
                if metric.kind not in _GIT_METRIC_KINDS:
                    raise ValueError(
                        "Git may support only implementation/test-scope metrics, not impact, "
                        "adoption, performance, revenue, or user counts"
                    )
                if not all(
                    approved[evidence_id].source_ref.startswith(("git:", "git-"))
                    for evidence_id in metric.evidence_ids
                ):
                    raise ValueError("git_observed metrics require approved Git evidence")


def load_experience_inventory(
    path: Path, evidence: Sequence[Evidence]
) -> tuple[ExperienceCandidate, ...]:
    """Load approved alternative bullets for existing experience entries."""
    if not path.is_file():
        raise FileNotFoundError(f"experience inventory does not exist: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"experience inventory is not valid JSON: {path}") from error
    if not isinstance(payload, list):
        raise ValueError("experience inventory must be a JSON array")
    approved = {item.id: item for item in evidence if item.approved}
    candidates = tuple(_candidate(item) for item in payload)
    seen: set[str] = set()
    for candidate in candidates:
        _validate_candidate(candidate, approved)
        if candidate.id in seen:
            raise ValueError(f"experience inventory contains duplicate ID: {candidate.id}")
        seen.add(candidate.id)
    return candidates


def experience_inventory_entries_from_master(
    master_latex: str, evidence_id: str
) -> list[dict[str, object]]:
    """Seed role pools from approved master bullets without copying template structure."""
    section = re.search(r"(?ms)^\\section\{Experience\}\s*$.*?(?=^\\section\{|\Z)", master_latex)
    if section is None:
        return []
    body = section.group(0)
    heading_command = next(
        (
            command
            for command in (
                "resumeSubheading",
                "resumeExperienceHeading",
                "resumeEntryHeading",
                "cventry",
            )
            if f"\\{command}" in body
        ),
        None,
    )
    if heading_command is None:
        return []
    headings = list(re.finditer(rf"\\{re.escape(heading_command)}\b", body))
    entries: list[dict[str, object]] = []
    used_ids: set[str] = set()
    for index, heading in enumerate(headings):
        next_heading = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        arguments, _ = _command_arguments_at(body, heading.start(), heading_command)
        if len(arguments) < 2:
            continue
        if heading_command == "cventry" and len(arguments) >= 3:
            title = latex_to_text(arguments[1])
            company = latex_to_text(arguments[2])
        else:
            title = latex_to_text(arguments[0])
            company = latex_to_text(arguments[2] if len(arguments) >= 3 else arguments[1])
        bullets = _resume_item_contents(body[heading.start() : next_heading])
        if not title or not company or not bullets:
            continue
        base = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".casefold()).strip("-")
        candidate_id = base or "experience"
        suffix = 2
        while candidate_id in used_ids:
            candidate_id = f"{base}-{suffix}"
            suffix += 1
        used_ids.add(candidate_id)
        bullet_rows: list[dict[str, object]] = []
        for latex in bullets:
            text = latex_to_text(latex)
            metrics = [
                {
                    "value": match.group(0),
                    "provenance": "user_confirmed",
                    "kind": "user_claim",
                    "evidence_ids": [evidence_id],
                }
                for match in _NUMBER.finditer(text)
            ]
            bullet_rows.append(
                {
                    "latex": latex,
                    "evidence_ids": [evidence_id],
                    "tags": sorted(
                        {
                            token
                            for token in re.findall(r"[a-z0-9+#.]+", text.casefold())
                            if len(token) > 2
                        }
                    )[:24],
                    "metrics": metrics,
                }
            )
        entries.append(
            {
                "id": candidate_id,
                "title": title,
                "company": company,
                "match_terms": [title, company],
                "bullets": bullet_rows,
            }
        )
    return entries


def sync_experience_inventory_from_master(
    path: Path, *, master_latex: str, evidence_id: str
) -> tuple[bool, int, int]:
    """Add missing approved roles/bullets while preserving user-authored alternatives."""
    if path.is_symlink():
        raise ValueError("experience inventory must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    if path.exists():
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"experience inventory is not valid JSON: {path}") from error
        if not isinstance(payload, list):
            raise ValueError(f"experience inventory must be a JSON array: {path}")
    else:
        payload = []
    additions = 0
    by_identity = {
        (
            re.sub(r"[^a-z0-9]+", "", str(item.get("title", "")).casefold()),
            re.sub(r"[^a-z0-9]+", "", str(item.get("company", "")).casefold()),
        ): item
        for item in payload
        if isinstance(item, dict)
    }
    for master_entry in experience_inventory_entries_from_master(master_latex, evidence_id):
        identity = (
            re.sub(r"[^a-z0-9]+", "", str(master_entry["title"]).casefold()),
            re.sub(r"[^a-z0-9]+", "", str(master_entry["company"]).casefold()),
        )
        existing = by_identity.get(identity)
        if existing is None:
            payload.append(master_entry)
            by_identity[identity] = master_entry
            additions += 1
            continue
        raw_existing_bullets = existing.get("bullets")
        if not isinstance(raw_existing_bullets, list):
            continue
        known = {
            latex_to_text(str(item.get("latex", ""))).casefold()
            for item in raw_existing_bullets
            if isinstance(item, dict)
        }
        master_bullets = master_entry.get("bullets")
        if not isinstance(master_bullets, list):
            continue
        for bullet in master_bullets:
            assert isinstance(bullet, dict)
            if latex_to_text(str(bullet["latex"])).casefold() not in known:
                raw_existing_bullets.append(bullet)
                additions += 1
    if created or additions:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False
        ) as temporary:
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        try:
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return created, additions, len(payload)


def add_user_experience_bullet(
    path: Path,
    *,
    title: str,
    company: str,
    text: str,
    evidence_id: str,
    tags: Sequence[str] = (),
) -> tuple[str, int]:
    """Append one explicitly confirmed claim and bold its user-supplied metrics."""
    title = title.strip()
    company = company.strip()
    text = " ".join(text.split())
    if not title or not company or not text:
        raise ValueError("role, company, and bullet text must be non-empty")
    if any(character in text for character in ("\\", "^", "~")):
        raise ValueError("experience bullets must be plain text, not LaTeX")
    if path.is_symlink():
        raise ValueError("experience inventory must not be a symlink")
    if path.exists():
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"experience inventory is not valid JSON: {path}") from error
        if not isinstance(payload, list):
            raise ValueError("experience inventory must be a JSON array")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = []
    identity = (
        re.sub(r"[^a-z0-9]+", "", title.casefold()),
        re.sub(r"[^a-z0-9]+", "", company.casefold()),
    )
    entry = next(
        (
            item
            for item in payload
            if isinstance(item, dict)
            and re.sub(r"[^a-z0-9]+", "", str(item.get("title", "")).casefold()) == identity[0]
            and re.sub(r"[^a-z0-9]+", "", str(item.get("company", "")).casefold()) == identity[1]
        ),
        None,
    )
    if entry is None:
        base = re.sub(r"[^a-z0-9]+", "-", f"{company}-{title}".casefold()).strip("-")
        used_ids = {
            str(item.get("id")) for item in payload if isinstance(item, dict) and item.get("id")
        }
        candidate_id = base or "experience"
        suffix = 2
        while candidate_id in used_ids:
            candidate_id = f"{base}-{suffix}"
            suffix += 1
        entry = {
            "id": candidate_id,
            "title": title,
            "company": company,
            "match_terms": [title, company],
            "bullets": [],
        }
        payload.append(entry)
    candidate_id = str(entry["id"])
    bullets = entry.get("bullets")
    if not isinstance(bullets, list):
        raise ValueError("experience inventory entry bullets must be a list")
    if any(
        isinstance(item, dict)
        and latex_to_text(str(item.get("latex", ""))).casefold() == text.casefold()
        for item in bullets
    ):
        raise ValueError("that experience bullet is already stored for this role")

    metrics = tuple(match.group(0) for match in _NUMBER.finditer(text))
    latex = re.sub(r"([%&#_${}])", r"\\\1", text)
    # Replace longest values first so a short number cannot consume part of a longer metric.
    for metric in sorted(metrics, key=len, reverse=True):
        escaped_metric = re.sub(r"([%&#_${}])", r"\\\1", metric)
        latex = latex.replace(escaped_metric, rf"\textbf{{{escaped_metric}}}", 1)
    bullets.append(
        {
            "latex": latex,
            "evidence_ids": [evidence_id],
            "tags": sorted({tag.strip().casefold() for tag in tags if tag.strip()}),
            "metrics": [
                {
                    "value": metric,
                    "provenance": "user_confirmed",
                    "kind": "user_claim",
                    "evidence_ids": [evidence_id],
                }
                for metric in metrics
            ],
        }
    )
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return candidate_id, len(bullets)
