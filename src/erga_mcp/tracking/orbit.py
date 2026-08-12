from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path

from defusedxml import ElementTree
from PIL import Image, ImageDraw, ImageFont

from erga_mcp.applications.identity import job_identity
from erga_mcp.models import Application, AuditEvent
from erga_mcp.tracking.tracker import TrackerEntry, read_application_tracker

_STATUS_ALIASES = {
    "assessment": "oa",
    "online assessment": "oa",
    "final round": "final-interview",
    "final interview": "final-interview",
    "ready to apply": "ready",
    "researching": "researching",
    "second interview": "interview-2",
    "third interview": "interview-3",
}
_STATUS_LABELS = {
    "accepted": "Accepted",
    "applied": "Applied",
    "awaiting-response": "Awaiting response",
    "declined": "Declined",
    "draft": "Draft",
    "expired": "Expired",
    "history-unavailable": "History unavailable",
    "interview": "Interview",
    "interview-2": "Interview 2",
    "interview-3": "Interview 3",
    "final-interview": "Final Interview",
    "oa": "Online assessment",
    "offer": "Offer",
    "ready": "Ready to apply",
    "rejected": "Rejected",
    "researching": "Researching",
    "tracked": "Tracked roles",
    "unknown": "Unknown status",
    "withdrawn": "Withdrawn",
}
_STATUS_COLORS = {
    "accepted": "#18A63B",
    "applied": "#18A63B",
    "awaiting-response": "#9ACFFC",
    "declined": "#BF3036",
    "draft": "#A8ACB8",
    "expired": "#BF3036",
    "history-unavailable": "#B8BBC5",
    "interview": "#18A63B",
    "interview-2": "#18A63B",
    "interview-3": "#18A63B",
    "final-interview": "#18A63B",
    "oa": "#18A63B",
    "offer": "#18A63B",
    "ready": "#70C7C2",
    "rejected": "#BF3036",
    "researching": "#A8ACB8",
    "tracked": "#171717",
    "unknown": "#B8BBC5",
    "withdrawn": "#BF3036",
}
_PROGRESS_RIBBON = "#7FD383"
_OUTCOME_RIBBON = "#D78383"
_NEGATIVE_OUTCOMES = frozenset({"declined", "expired", "rejected", "withdrawn"})
_STATUS_COLUMNS = {
    "applied": 0,
    "oa": 1,
    "interview": 2,
    "interview-2": 3,
    "interview-3": 4,
    "final-interview": 5,
    "offer": 6,
    "accepted": 7,
    "declined": 7,
    "expired": 1,
}
_STATUS_PROGRESS = {
    "researching": 0,
    "draft": 0,
    "ready": 1,
    "applied": 2,
    "oa": 3,
    "interview": 4,
    "interview-2": 5,
    "interview-3": 6,
    "final-interview": 7,
    "offer": 8,
    "accepted": 9,
}
_TERMINAL_STATUSES = frozenset({"accepted", "declined", "expired", "rejected", "withdrawn"})
_PRE_APPLICATION_STATUSES = frozenset({"draft", "ready", "researching", "unknown"})
_STATUS_AUDIT_ACTIONS = frozenset(
    {"application.status_updated", "application.status_updated_from_mail"}
)
_ORBIT_RENDER_VERSION = 3


@dataclass(frozen=True)
class OrbitNode:
    id: str
    label: str
    column: int
    count: int
    color: str


@dataclass(frozen=True)
class OrbitLink:
    source: str
    target: str
    count: int
    color: str
    recorded: bool


@dataclass(frozen=True)
class OrbitSnapshot:
    nodes: tuple[OrbitNode, ...]
    links: tuple[OrbitLink, ...]
    tracked_count: int
    local_application_count: int
    tracker_only_count: int
    recorded_history_count: int
    snapshot_only_count: int
    corrected_transition_count: int
    cycle: str
    generated_at: datetime
    content_hash: str

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["generated_at"] = self.generated_at.isoformat()
        return payload


@dataclass(frozen=True)
class OrbitArtifact:
    snapshot: OrbitSnapshot
    image_path: Path
    message: str


def _canonical_status(value: object) -> str:
    normalized = " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())
    normalized = _STATUS_ALIASES.get(normalized, normalized)
    interview_round = re.fullmatch(r"(?:interview(?: round)?|round) ([2-9])", normalized)
    if interview_round is not None:
        normalized = f"interview-{interview_round.group(1)}"
    return (
        normalized
        if normalized in _STATUS_LABELS or normalized in _TERMINAL_STATUSES
        else "unknown"
    )


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("-", " ").title())


def _status_color(status: str) -> str:
    return _STATUS_COLORS.get(status, _STATUS_COLORS["unknown"])


def _is_negative_node(node: OrbitNode) -> bool:
    return any(
        node.id == status or node.id.startswith(f"{status}-stage-") for status in _NEGATIVE_OUTCOMES
    )


def _ribbon_color(target: OrbitNode) -> str:
    return _OUTCOME_RIBBON if _is_negative_node(target) else _PROGRESS_RIBBON


def _recorded_statuses(
    application: Application,
    events: Sequence[AuditEvent],
) -> tuple[list[str], bool, int]:
    statuses: list[str] = []
    corrected = 0
    for event in sorted(events, key=lambda item: (item.created_at, item.id)):
        if event.action == "application.created":
            statuses.append(_canonical_status(event.payload.get("status", "draft")))
        elif event.action in _STATUS_AUDIT_ACTIONS:
            statuses.append(_canonical_status(event.payload.get("to")))
    if not statuses:
        return [_canonical_status(application.status)], False, 0
    current = _canonical_status(application.status)
    complete = statuses[-1] == current
    if not complete:
        return [current], False, 0

    compact: list[str] = []
    for status in statuses:
        if compact and compact[-1] == status:
            continue
        if compact and compact[-1] in _TERMINAL_STATUSES and status not in _TERMINAL_STATUSES:
            # A role can be corrected or reopened after a terminal classification. The live
            # funnel represents its current journey, not an impossible terminal-to-active path.
            corrected += 1
            compact.clear()
        if status in _TERMINAL_STATUSES:
            compact.append(status)
            continue
        progress = _STATUS_PROGRESS.get(status)
        if compact and progress is not None:
            previous_progress = _STATUS_PROGRESS.get(compact[-1])
            if previous_progress is not None and progress < previous_progress:
                corrected += 1
                while compact and _STATUS_PROGRESS.get(compact[-1], -1) >= progress:
                    compact.pop()
        compact.append(status)
    if len(compact) > 1 and compact[0] in {"draft", "researching", "ready"}:
        compact = compact[1:]
    return compact or [current], True, corrected


def _terminal_node(status: str, previous: str) -> tuple[str, str, int]:
    column = max(_STATUS_COLUMNS.get(previous, 0) + 1, 1)
    return f"{status}-stage-{column}", _status_label(status), column


def _journey_nodes(statuses: Sequence[str]) -> list[tuple[str, str, int, str]]:
    nodes: list[tuple[str, str, int, str]] = []
    previous = "applied"
    for status in statuses:
        if status in _TERMINAL_STATUSES:
            identifier, label, column = _terminal_node(status, previous)
            nodes.append((identifier, label, column, status))
            previous = status
            continue
        nodes.append(
            (
                status,
                _status_label(status),
                _STATUS_COLUMNS.get(status, _STATUS_COLUMNS.get(previous, 0) + 1),
                status,
            )
        )
        previous = status
    return nodes


def _pipeline_statuses(statuses: Sequence[str]) -> tuple[list[str], bool]:
    """Return post-application stages and whether Applied was explicitly recorded."""
    pipeline = [status for status in statuses if status not in _PRE_APPLICATION_STATUSES]
    if not pipeline:
        return [], False
    if "applied" in pipeline:
        return pipeline[pipeline.index("applied") :], True
    return ["applied", *pipeline], False


def _tracker_by_identity(entries: Sequence[TrackerEntry], *, cycle: str) -> dict[str, TrackerEntry]:
    selected: dict[str, TrackerEntry] = {}
    normalized_cycle = cycle.strip().casefold()
    for entry in entries:
        if normalized_cycle and entry.cycle.casefold() != normalized_cycle:
            continue
        identity = job_identity(entry.source_url) if entry.source_url else ""
        if not identity:
            identity = "|".join(
                (entry.company.casefold(), entry.role.casefold(), entry.cycle.casefold())
            )
        selected.setdefault(identity, entry)
    return selected


def _snapshot_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def build_orbit_snapshot(
    applications: Sequence[Application],
    audit_events: Sequence[AuditEvent],
    *,
    tracker_entries: Sequence[TrackerEntry] = (),
    cycle: str = "",
    generated_at: datetime | None = None,
) -> OrbitSnapshot:
    """Build an aggregate flow without inventing missing application history."""
    tracker = _tracker_by_identity(tracker_entries, cycle=cycle)
    normalized_cycle = cycle.strip()
    audits_by_application: dict[str, list[AuditEvent]] = defaultdict(list)
    for event in audit_events:
        if event.action == "application.created" or event.action in _STATUS_AUDIT_ACTIONS:
            audits_by_application[event.subject_id].append(event)

    selected_applications: list[Application] = []
    local_identities: set[str] = set()
    for application in applications:
        identity = job_identity(application.source_url)
        if normalized_cycle and identity not in tracker:
            continue
        selected_applications.append(application)
        if identity:
            local_identities.add(identity)

    tracker_only = {
        identity: entry for identity, entry in tracker.items() if identity not in local_identities
    }
    node_applications: dict[str, set[str]] = defaultdict(set)
    node_specs: dict[str, tuple[str, int, str]] = {}
    link_applications: dict[tuple[str, str, bool], set[str]] = defaultdict(set)
    recorded_history_count = 0
    snapshot_only_count = 0
    corrected_transition_count = 0
    local_application_count = 0
    tracker_only_count = 0

    def add_path(
        key: str,
        journey: Iterable[tuple[str, str, int, str]],
        *,
        recorded: bool,
    ) -> None:
        previous: str | None = None
        for identifier, label, column, status in journey:
            node_specs[identifier] = (label, column, status)
            node_applications[identifier].add(key)
            if previous is not None:
                link_applications[(previous, identifier, recorded)].add(key)
            previous = identifier

    for application in selected_applications:
        statuses, recorded, corrected = _recorded_statuses(
            application, audits_by_application.get(application.id, ())
        )
        pipeline, applied_was_recorded = _pipeline_statuses(statuses)
        if not pipeline:
            continue
        corrected_transition_count += corrected
        local_application_count += 1
        path_is_recorded = recorded and applied_was_recorded
        if path_is_recorded:
            recorded_history_count += 1
            add_path(application.id, _journey_nodes(pipeline), recorded=True)
        else:
            snapshot_only_count += 1
            add_path(application.id, _journey_nodes(pipeline), recorded=False)

    for identity, entry in tracker_only.items():
        pipeline, _ = _pipeline_statuses([_canonical_status(entry.status)])
        if not pipeline:
            continue
        tracker_only_count += 1
        snapshot_only_count += 1
        add_path(
            f"tracker:{identity}",
            _journey_nodes(pipeline),
            recorded=False,
        )

    nodes = tuple(
        sorted(
            (
                OrbitNode(
                    id=identifier,
                    label=spec[0],
                    column=spec[1],
                    count=len(node_applications[identifier]),
                    color=_status_color(spec[2]),
                )
                for identifier, spec in node_specs.items()
                if node_applications[identifier]
            ),
            key=lambda item: (item.column, item.label.casefold(), item.id),
        )
    )
    node_colors = {node.id: node.color for node in nodes}
    links = tuple(
        sorted(
            (
                OrbitLink(
                    source=source,
                    target=target,
                    count=len(keys),
                    color=node_colors.get(target, _STATUS_COLORS["unknown"]),
                    recorded=recorded,
                )
                for (source, target, recorded), keys in link_applications.items()
                if keys
            ),
            key=lambda item: (item.source, item.target, not item.recorded),
        )
    )
    tracked_count = len(node_applications["applied"])
    stable_payload: dict[str, object] = {
        "nodes": [asdict(node) for node in nodes],
        "links": [asdict(link) for link in links],
        "tracked_count": tracked_count,
        "local_application_count": local_application_count,
        "tracker_only_count": tracker_only_count,
        "recorded_history_count": recorded_history_count,
        "snapshot_only_count": snapshot_only_count,
        "corrected_transition_count": corrected_transition_count,
        "cycle": normalized_cycle,
        "render_version": _ORBIT_RENDER_VERSION,
    }
    return OrbitSnapshot(
        nodes=nodes,
        links=links,
        tracked_count=tracked_count,
        local_application_count=local_application_count,
        tracker_only_count=tracker_only_count,
        recorded_history_count=recorded_history_count,
        snapshot_only_count=snapshot_only_count,
        corrected_transition_count=corrected_transition_count,
        cycle=normalized_cycle,
        generated_at=generated_at or datetime.now(UTC),
        content_hash=_snapshot_hash(stable_payload),
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(filename, size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def _hex_color(value: str, *, alpha: int = 255) -> tuple[int, int, int, int]:
    stripped = value.lstrip("#")
    return (
        int(stripped[0:2], 16),
        int(stripped[2:4], 16),
        int(stripped[4:6], 16),
        alpha,
    )


def _bezier(start: float, control_a: float, control_b: float, end: float, t: float) -> float:
    inverse = 1 - t
    return (
        inverse**3 * start
        + 3 * inverse**2 * t * control_a
        + 3 * inverse * t**2 * control_b
        + t**3 * end
    )


def _ribbon_points(
    source_x: float,
    source_top: float,
    source_bottom: float,
    target_x: float,
    target_top: float,
    target_bottom: float,
    *,
    scale: int,
) -> list[tuple[int, int]]:
    control_a = source_x + (target_x - source_x) * 0.42
    control_b = source_x + (target_x - source_x) * 0.58
    points: list[tuple[int, int]] = []
    for index in range(25):
        t = index / 24
        points.append(
            (
                round(_bezier(source_x, control_a, control_b, target_x, t) * scale),
                round(_bezier(source_top, source_top, target_top, target_top, t) * scale),
            )
        )
    for index in range(24, -1, -1):
        t = index / 24
        points.append(
            (
                round(_bezier(source_x, control_a, control_b, target_x, t) * scale),
                round(
                    _bezier(source_bottom, source_bottom, target_bottom, target_bottom, t) * scale
                ),
            )
        )
    return points


def _cubic_points(
    start: tuple[float, float],
    control_a: tuple[float, float],
    control_b: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    return [
        (
            _bezier(start[0], control_a[0], control_b[0], end[0], index / 16),
            _bezier(start[1], control_a[1], control_b[1], end[1], index / 16),
        )
        for index in range(1, 17)
    ]


_SVG_PATH_TOKEN = re.compile(r"[a-zA-Z]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_ERGA_LOGO_IDS = (
    "erga-top",
    "erga-left",
    "erga-right",
    "erga-bottom",
    "erga-top-2",
    "erga-left-7",
    "erga-right-3",
    "erga-bottom-7",
    "text8",
)


def _parse_logo_path(path_data: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Flatten the move, line, horizontal, vertical, and cubic commands in Erga's SVG."""
    tokens = _SVG_PATH_TOKEN.findall(path_data)
    contours: list[tuple[tuple[float, float], ...]] = []
    points: list[tuple[float, float]] = []
    current = (0.0, 0.0)
    start = current
    command = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in {"Z", "z"}:
                if points:
                    contours.append(tuple(points))
                    points = []
                current = start
                command = ""
                continue
            if command not in {"M", "L", "H", "V", "C", "m", "l", "h", "v", "c"}:
                raise ValueError(f"unsupported Erga logo path command: {command}")
            continue
        if command in {"M", "m"}:
            if points:
                contours.append(tuple(points))
                points = []
            relative = command.islower()
            next_point = (
                (current[0] if relative else 0.0) + float(tokens[index]),
                (current[1] if relative else 0.0) + float(tokens[index + 1]),
            )
            points.append(next_point)
            current = next_point
            start = next_point
            index += 2
            command = "l" if relative else "L"
            continue
        if command in {"L", "l"}:
            relative = command.islower()
            next_point = (
                (current[0] if relative else 0.0) + float(tokens[index]),
                (current[1] if relative else 0.0) + float(tokens[index + 1]),
            )
            points.append(next_point)
            current = next_point
            index += 2
            continue
        if command in {"H", "h"}:
            x = (current[0] if command.islower() else 0.0) + float(tokens[index])
            current = (x, current[1])
            points.append(current)
            index += 1
            continue
        if command in {"V", "v"}:
            y = (current[1] if command.islower() else 0.0) + float(tokens[index])
            current = (current[0], y)
            points.append(current)
            index += 1
            continue
        if command in {"C", "c"}:
            values = [float(item) for item in tokens[index : index + 6]]
            base = current if command.islower() else (0.0, 0.0)
            control_a = (base[0] + values[0], base[1] + values[1])
            control_b = (base[0] + values[2], base[1] + values[3])
            end = (base[0] + values[4], base[1] + values[5])
            points.extend(_cubic_points(current, control_a, control_b, end))
            current = end
            index += 6
            continue
        raise ValueError("Erga logo path starts without a supported command")
    if points:
        contours.append(tuple(points))
    return tuple(contours)


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    return (
        sum(
            x_a * y_b - x_b * y_a
            for (x_a, y_a), (x_b, y_b) in zip(points, (*points[1:], points[0]))
        )
        / 2
    )


def _polygon_centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    area = _polygon_area(points)
    if abs(area) < 1e-9:
        return points[0]
    x = sum(
        (x_a + x_b) * (x_a * y_b - x_b * y_a)
        for (x_a, y_a), (x_b, y_b) in zip(points, (*points[1:], points[0]))
    ) / (6 * area)
    y = sum(
        (y_a + y_b) * (x_a * y_b - x_b * y_a)
        for (x_a, y_a), (x_b, y_b) in zip(points, (*points[1:], points[0]))
    ) / (6 * area)
    return x, y


def _point_in_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        crosses = (current_y > y) != (previous_y > y)
        if crosses:
            intersection_x = (previous_x - current_x) * (y - current_y) / (
                previous_y - current_y
            ) + current_x
            if x < intersection_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


@lru_cache(maxsize=1)
def _erga_logo_paths() -> tuple[tuple[str, tuple[tuple[tuple[float, float], ...], ...]], ...]:
    """Read the complete mark and outlined wordmark from the README SVG."""
    packaged = resources.files("erga_mcp").joinpath("assets", "erga-logo.svg")
    if packaged.is_file():
        source = packaged.read_text(encoding="utf-8")
    else:
        source = (
            Path(__file__).resolve().parents[3] / "docs" / "assets" / "erga-logo.svg"
        ).read_text(encoding="utf-8")
    root = ElementTree.fromstring(source)
    paths: dict[str, tuple[str, tuple[tuple[tuple[float, float], ...], ...]]] = {}
    for element in root.findall(".//{http://www.w3.org/2000/svg}path"):
        identifier = element.get("id", "")
        if identifier not in _ERGA_LOGO_IDS:
            continue
        style = element.get("style", "")
        color = re.search(r"(?:^|;)fill:(#[0-9a-fA-F]{6})(?:;|$)", style)
        path_data = element.get("d")
        if color is None or not path_data:
            raise ValueError(f"Erga logo path {identifier} has no usable fill or geometry")
        paths[identifier] = (color.group(1), _parse_logo_path(path_data))
    missing = [identifier for identifier in _ERGA_LOGO_IDS if identifier not in paths]
    if missing:
        raise ValueError(f"Erga logo asset is missing visible paths: {', '.join(missing)}")
    return tuple(paths[identifier] for identifier in _ERGA_LOGO_IDS)


def _draw_erga_logo(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    scale: int,
) -> None:
    """Rasterize the complete README logo, including its outlined ERGA wordmark."""
    asset_width = 1500.0
    asset_height = 500.0
    height = width * asset_height / asset_width
    for color, contours in _erga_logo_paths():
        transformed = [
            tuple(
                (
                    round((x + px / asset_width * width) * scale),
                    round((y + py / asset_height * height) * scale),
                )
                for px, py in points
            )
            for points in contours
        ]
        for points in sorted(transformed, key=lambda item: abs(_polygon_area(item)), reverse=True):
            centroid = _polygon_centroid(points)
            nesting = sum(
                _point_in_polygon(centroid, container)
                for container in transformed
                if container is not points
                and abs(_polygon_area(container)) > abs(_polygon_area(points))
            )
            draw.polygon(
                points,
                fill=_hex_color("#FFFFFF" if nesting % 2 else color),
            )


def _visual_label(node: OrbitNode) -> str:
    if node.id.startswith("rejected-stage-"):
        return "Rejected"
    return {"oa": "OA"}.get(node.id, node.label)


def render_orbit_png(
    snapshot: OrbitSnapshot,
    destination: Path,
    *,
    width: int = 1600,
    height: int = 900,
) -> Path:
    """Render one deterministic, company-aggregate Erga Orbit dashboard image."""
    if width < 1000 or height < 600:
        raise ValueError("Orbit image must be at least 1000 by 600 pixels")
    scale = 2
    image = Image.new("RGBA", (width * scale, height * scale), _hex_color("#FFFFFF"))
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = _font(38 * scale)
    label_font = _font(18 * scale, bold=True)

    title = "Erga Orbit Tracker"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_box[2] - title_box[0]
    draw.text(
        ((width * scale - title_width) / 2, 32 * scale),
        title,
        font=title_font,
        fill=_hex_color("#171717"),
    )

    if not snapshot.nodes:
        message = "No applications yet"
        box = draw.textbbox((0, 0), message, font=title_font)
        draw.text(
            (
                (width * scale - (box[2] - box[0])) / 2,
                (height * scale - (box[3] - box[1])) / 2,
            ),
            message,
            font=title_font,
            fill=_hex_color("#8A8E98"),
        )
    else:
        chart_left = 72
        chart_right = width - 112
        chart_top = 132
        chart_bottom = height - 64
        chart_height = chart_bottom - chart_top
        max_column = max(max(node.column for node in snapshot.nodes), 4)
        columns: dict[int, list[OrbitNode]] = defaultdict(list)
        for node in snapshot.nodes:
            columns[node.column].append(node)
        root_count = max(snapshot.tracked_count, 1)
        value_scale = min(58.0, max(9.0, chart_height * 0.8 / root_count))
        geometry: dict[str, tuple[float, float, float]] = {}
        first_column = min(columns)
        for column, nodes in columns.items():
            nodes.sort(key=lambda node: (_is_negative_node(node), node.label.casefold(), node.id))
            gap = 28
            heights = [max(24.0, node.count * value_scale) for node in nodes]
            total = sum(heights) + gap * max(len(nodes) - 1, 0)
            if total > chart_height:
                available = chart_height - gap * max(len(nodes) - 1, 0)
                ratio = available / max(sum(heights), 1)
                heights = [max(18.0, item * ratio) for item in heights]
                total = sum(heights) + gap * max(len(nodes) - 1, 0)
            remaining_height = max(chart_height - total, 0)
            y = chart_top + (
                remaining_height / 2 if column == first_column else remaining_height * 0.15
            )
            x = chart_left + (chart_right - chart_left) * column / max_column
            for node, node_height in zip(nodes, heights):
                geometry[node.id] = (x, y, y + node_height)
                y += node_height + gap

        outgoing: dict[str, int] = defaultdict(int)
        incoming: dict[str, int] = defaultdict(int)
        for link in snapshot.links:
            outgoing[link.source] += link.count
            incoming[link.target] += link.count
        source_offsets: dict[str, float] = defaultdict(float)
        target_offsets: dict[str, float] = defaultdict(float)
        node_by_id = {node.id: node for node in snapshot.nodes}
        for link in sorted(
            snapshot.links,
            key=lambda item: (
                node_by_id[item.source].column,
                geometry[item.source][1],
                _is_negative_node(node_by_id[item.target]),
                -node_by_id[item.target].column,
                geometry[item.target][1],
            ),
        ):
            source_x, source_y0, source_y1 = geometry[link.source]
            target_x, target_y0, target_y1 = geometry[link.target]
            source_unit = (source_y1 - source_y0) / max(outgoing[link.source], 1)
            target_unit = (target_y1 - target_y0) / max(incoming[link.target], 1)
            source_top = source_y0 + source_offsets[link.source]
            target_top = target_y0 + target_offsets[link.target]
            source_bottom = source_top + source_unit * link.count
            target_bottom = target_top + target_unit * link.count
            source_offsets[link.source] += source_unit * link.count
            target_offsets[link.target] += target_unit * link.count
            color = _ribbon_color(node_by_id[link.target])
            draw.polygon(
                _ribbon_points(
                    source_x + 11,
                    source_top,
                    source_bottom,
                    target_x,
                    target_top,
                    target_bottom,
                    scale=scale,
                ),
                fill=_hex_color(color, alpha=216 if link.recorded else 188),
            )

        for node in snapshot.nodes:
            x, y0, y1 = geometry[node.id]
            draw.rounded_rectangle(
                (x * scale, y0 * scale, (x + 11) * scale, y1 * scale),
                radius=4 * scale,
                fill=_hex_color(node.color),
            )
            label = f"{_visual_label(node)}  {node.count}"
            label_box = draw.textbbox((0, 0), label, font=label_font)
            label_width = (label_box[2] - label_box[0]) / scale
            label_height = (label_box[3] - label_box[1]) / scale
            if node.column >= max_column:
                label_x = x - label_width - 14
            else:
                label_x = x + 20
            label_y = (y0 + y1 - label_height) / 2 - 3
            draw.rounded_rectangle(
                (
                    (label_x - 6) * scale,
                    (label_y - 4) * scale,
                    (label_x + label_width + 6) * scale,
                    (label_y + label_height + 6) * scale,
                ),
                radius=5 * scale,
                fill=(255, 255, 255, 232),
            )
            draw.text(
                (label_x * scale, label_y * scale),
                label,
                font=label_font,
                fill=_hex_color("#171717"),
            )
    _draw_erga_logo(draw, x=width - 280, y=height - 96, width=248, scale=scale)

    image = image.resize((width, height), Image.Resampling.LANCZOS).convert("RGB")
    destination = destination.expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".png", prefix=f".{destination.stem}-", dir=destination.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        image.save(temporary_path, format="PNG", optimize=True)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def orbit_message(snapshot: OrbitSnapshot) -> str:
    """Return a compact companion caption suitable for Discord or a terminal."""
    return f"**Orbit · {snapshot.cycle}**" if snapshot.cycle else "**Orbit**"


def create_orbit_artifact(
    *,
    applications: Sequence[Application],
    audit_events: Sequence[AuditEvent],
    output_dir: Path,
    tracker_dir: Path | None = None,
    cycle: str = "",
) -> OrbitArtifact:
    """Create the reusable private Orbit projection consumed by CLI, MCP, and Discord."""
    tracker_entries = read_application_tracker(tracker_dir).entries if tracker_dir else ()
    snapshot = build_orbit_snapshot(
        applications,
        audit_events,
        tracker_entries=tracker_entries,
        cycle=cycle,
    )
    slug = re.sub(r"[^a-z0-9]+", "-", cycle.casefold()).strip("-") or "all"
    image_path = render_orbit_png(snapshot, output_dir / f"erga-orbit-{slug}.png")
    return OrbitArtifact(snapshot=snapshot, image_path=image_path, message=orbit_message(snapshot))


def orbit_cycle_from_request(content: str) -> str:
    """Extract an optional cycle from an exact human Orbit command."""
    normalized = " ".join(content.strip().split())
    match = re.fullmatch(
        r"(?i)(?:/erga-orbit|erga\s+orbit|orbit|tracker\s+orbit|show\s+(?:me\s+)?orbit)"
        r"(?:\s+(?:for\s+|cycle\s+)?(.+))?",
        normalized,
    )
    return match.group(1).strip() if match and match.group(1) else ""


def is_orbit_request(content: str) -> bool:
    normalized = " ".join(content.strip().split())
    return bool(
        re.fullmatch(
            r"(?i)(?:/erga-orbit|erga\s+orbit|orbit|tracker\s+orbit|"
            r"show\s+(?:me\s+)?orbit)(?:\s+(?:for\s+|cycle\s+)?.+)?",
            normalized,
        )
    )


def is_orbit_stop_request(content: str) -> bool:
    """Recognize only an explicit request to stop this channel's live Orbit."""
    normalized = " ".join(content.strip().split())
    return bool(
        re.fullmatch(
            r"(?i)(?:/erga-orbit|erga\s+orbit|orbit|tracker\s+orbit)\s+"
            r"(?:stop|disable|off)",
            normalized,
        )
    )
