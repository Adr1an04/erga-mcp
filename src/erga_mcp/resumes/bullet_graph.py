from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass

from erga_mcp.resumes.bullet_editor import analyze_bullet_editorially

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_CLAUSE_BOUNDARY = re.compile(r";+|\n+|(?<=[A-Za-z0-9)%])\.(?=\s+[A-Z])")
_OBJECT_BOUNDARY = re.compile(
    r",?\s+\b(?:across|by|covering|for|serving|supporting|through|to|using|via|with)\b",
    re.I,
)
_REFERENCE = re.compile(r"\b(?:for|of|on|to)\s+(?:the\s+)?([A-Za-z][A-Za-z0-9+#./-]*)", re.I)
_GRAPH_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "approved",
        "authored",
        "built",
        "created",
        "developed",
        "engineered",
        "implemented",
        "project",
        "separate",
        "the",
        "verified",
        "work",
    }
)


@dataclass(frozen=True)
class BulletGraphNode:
    id: str
    kind: str
    text: str
    evidence_ids: tuple[str, ...]
    claim_id: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BulletGraphEdge:
    source: str
    target: str
    relation: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BulletGraphPath:
    id: str
    claim_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    slots: tuple[str, ...]
    assembly_order: tuple[str, ...]
    render_order: tuple[str, ...]
    completeness: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceBulletGraph:
    project_id: str
    nodes: tuple[BulletGraphNode, ...]
    edges: tuple[BulletGraphEdge, ...]
    paths: tuple[BulletGraphPath, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def as_prompt_dict(self) -> dict[str, object]:
        # Paths already encode connectivity, so repeating the complete edge list and internal
        # claim ownership on every node only burns model context. Keep the full graph in memory
        # for deterministic validation and send the writer the smallest lossless blueprint.
        return {
            "project_id": self.project_id,
            "assembly_rule": (
                "Choose one connected path. Build bottom-up in assembly_order, omit unsupported "
                "slots, choose the lead action last, then write one sentence in render_order. "
                "Never combine disconnected paths."
            ),
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "text": node.text,
                    "evidence_ids": list(node.evidence_ids),
                }
                for node in self.nodes
            ],
            "paths": [
                {
                    "id": path.id,
                    "node_ids": list(path.node_ids),
                    "evidence_ids": list(path.evidence_ids),
                    "slots": list(path.slots),
                    "assembly_order": list(path.assembly_order),
                    "render_order": list(path.render_order),
                    "completeness": path.completeness,
                }
                for path in self.paths
            ],
        }


@dataclass(frozen=True)
class BulletGraphAlignment:
    passed: bool
    path_id: str | None
    overlap_score: int
    aligned_slots: tuple[str, ...]
    missing_slots: tuple[str, ...]
    issue_codes: tuple[str, ...]
    claim_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]
    return f"bg_{digest}"


def _normalized_term(value: str) -> str:
    term = value.casefold().rstrip(".")
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _terms(value: str) -> frozenset[str]:
    return frozenset(
        _normalized_term(word)
        for word in _WORD.findall(value)
        if _normalized_term(word) not in _GRAPH_STOP
    )


def _object_phrase(clause: str) -> str:
    words = list(_WORD.finditer(clause))
    if len(words) < 2:
        return ""
    remainder = clause[words[0].end() :].strip(" ,-")
    boundary = _OBJECT_BOUNDARY.search(remainder)
    if boundary is not None:
        remainder = remainder[: boundary.start()]
    return remainder.strip(" ,-")


def _claim_clauses(value: str) -> tuple[str, ...]:
    return tuple(
        clause.strip(" ,-")
        for clause in _CLAUSE_BOUNDARY.split(" ".join(value.split()))
        if clause.strip(" ,-")
    )


def _node(
    *,
    project_id: str,
    source_index: int,
    clause_index: int,
    signal_index: int,
    kind: str,
    text: str,
    evidence_ids: tuple[str, ...],
    claim_id: str,
) -> BulletGraphNode:
    return BulletGraphNode(
        id=_stable_id(
            project_id,
            str(source_index),
            str(clause_index),
            str(signal_index),
            kind,
            text.casefold(),
        ),
        kind=kind,
        text=text,
        evidence_ids=evidence_ids,
        claim_id=claim_id,
    )


def build_evidence_bullet_graph(
    project_id: str,
    sources: list[dict[str, object]],
) -> EvidenceBulletGraph:
    """Turn bounded evidence into connected, evidence-cited bullet building blocks."""
    nodes: list[BulletGraphNode] = []
    edges: list[BulletGraphEdge] = []
    claim_nodes: list[BulletGraphNode] = []
    claim_source: dict[str, int] = {}
    claim_objects: dict[str, frozenset[str]] = {}
    claim_references: dict[str, frozenset[str]] = {}

    for source_index, source in enumerate(sources):
        source_text = source.get("text")
        raw_evidence_ids = source.get("evidence_ids")
        if not isinstance(source_text, str) or not source_text.strip():
            continue
        if not isinstance(raw_evidence_ids, list) or any(
            not isinstance(item, str) for item in raw_evidence_ids
        ):
            continue
        evidence_ids = tuple(dict.fromkeys(raw_evidence_ids))
        for clause_index, clause in enumerate(_claim_clauses(source_text)):
            claim_id = _stable_id(project_id, str(source_index), str(clause_index), "claim")
            claim = BulletGraphNode(
                id=claim_id,
                kind="claim",
                text=clause,
                evidence_ids=evidence_ids,
                claim_id=claim_id,
            )
            nodes.append(claim)
            claim_nodes.append(claim)
            claim_source[claim_id] = source_index
            object_phrase = _object_phrase(clause)
            claim_objects[claim_id] = _terms(object_phrase)
            claim_references[claim_id] = frozenset(
                match.group(1).casefold() for match in _REFERENCE.finditer(clause)
            )

            report = analyze_bullet_editorially(clause)
            first_word = next(iter(_WORD.findall(clause)), "")
            signals: list[tuple[str, str]] = []
            if first_word:
                signals.append(("action", first_word))
            if object_phrase:
                signals.append(("object", object_phrase))
            signals.extend(("method", item) for item in report.structure.technical_terms)
            signals.extend(("method", item) for item in report.structure.functional_signals)
            signals.extend(("scope", item) for item in report.structure.scope_terms)
            signals.extend(("scope", item) for item in report.structure.metrics)
            signals.extend(("proof", item) for item in report.structure.metrics)
            signals.extend(("proof", item) for item in report.structure.functional_signals)
            signals.extend(("outcome", item) for item in report.structure.outcome_terms)
            if report.structure.has_outcome and not report.structure.outcome_terms:
                signals.append(("outcome", first_word))
            for signal_index, (kind, text) in enumerate(dict.fromkeys(signals)):
                signal = _node(
                    project_id=project_id,
                    source_index=source_index,
                    clause_index=clause_index,
                    signal_index=signal_index,
                    kind=kind,
                    text=text,
                    evidence_ids=evidence_ids,
                    claim_id=claim_id,
                )
                nodes.append(signal)
                edges.append(BulletGraphEdge(claim_id, signal.id, f"supports_{kind}"))

    claim_links: dict[str, set[str]] = defaultdict(set)
    for left_index, left in enumerate(claim_nodes):
        for right in claim_nodes[left_index + 1 :]:
            shared_objects = claim_objects[left.id] & claim_objects[right.id]
            explicit_reference = bool(
                (claim_references[left.id] & claim_objects[right.id])
                or (claim_references[right.id] & claim_objects[left.id])
            )
            same_source = claim_source[left.id] == claim_source[right.id]
            if not shared_objects and not explicit_reference:
                continue
            relation = "same_subject" if shared_objects else "explicit_reference"
            if same_source:
                relation = f"same_source_{relation}"
            edges.append(BulletGraphEdge(left.id, right.id, relation))
            claim_links[left.id].add(right.id)
            claim_links[right.id].add(left.id)

    node_by_claim: dict[str, list[BulletGraphNode]] = defaultdict(list)
    for node in nodes:
        node_by_claim[node.claim_id].append(node)
    paths: list[BulletGraphPath] = []
    seen_paths: set[tuple[str, ...]] = set()
    for claim in claim_nodes:
        claim_ids = tuple(sorted({claim.id, *claim_links[claim.id]}))
        if claim_ids in seen_paths:
            continue
        seen_paths.add(claim_ids)
        path_nodes = tuple(node for claim_id in claim_ids for node in node_by_claim[claim_id])
        slots = tuple(
            slot
            for slot in ("action", "object", "method", "scope", "proof", "outcome")
            if any(node.kind == slot for node in path_nodes)
        )
        evidence_ids = tuple(
            dict.fromkeys(evidence_id for node in path_nodes for evidence_id in node.evidence_ids)
        )
        completeness = round(100 * len(slots) / 6)
        path_id = _stable_id(project_id, *claim_ids, "path")
        paths.append(
            BulletGraphPath(
                id=path_id,
                claim_ids=claim_ids,
                node_ids=tuple(node.id for node in path_nodes),
                evidence_ids=evidence_ids,
                slots=slots,
                assembly_order=tuple(
                    slot
                    for slot in ("object", "method", "scope", "proof", "outcome", "action")
                    if slot in slots
                ),
                render_order=tuple(
                    slot
                    for slot in ("action", "object", "method", "scope", "proof", "outcome")
                    if slot in slots
                ),
                completeness=completeness,
            )
        )
    return EvidenceBulletGraph(
        project_id=project_id,
        nodes=tuple(nodes),
        edges=tuple(edges),
        paths=tuple(sorted(paths, key=lambda item: (-item.completeness, item.id))),
    )


def _connected_claims(graph: EvidenceBulletGraph, starting_claims: set[str]) -> set[str]:
    links: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if "subject" not in edge.relation and "reference" not in edge.relation:
            continue
        links[edge.source].add(edge.target)
        links[edge.target].add(edge.source)
    reached: set[str] = set()
    queue = deque(starting_claims)
    while queue:
        claim_id = queue.popleft()
        if claim_id in reached:
            continue
        reached.add(claim_id)
        queue.extend(links[claim_id] - reached)
    return reached


def align_bullet_to_graph(
    text: str,
    evidence_ids: tuple[str, ...],
    graph: EvidenceBulletGraph,
    *,
    requested_path_id: str | None = None,
) -> BulletGraphAlignment:
    """Require generated copy to resolve to one connected evidence-graph path."""
    generated_terms = _terms(text)
    claim_nodes = [node for node in graph.nodes if node.kind == "claim"]
    cited = set(evidence_ids)
    overlaps = {
        node.id: len(generated_terms & _terms(node.text))
        for node in claim_nodes
        if cited & set(node.evidence_ids)
    }
    generated_object_terms = _terms(_object_phrase(text))
    matching_claims = {
        node.id
        for node in claim_nodes
        if node.id in overlaps and generated_object_terms & _terms(_object_phrase(node.text))
    }
    issues: list[str] = []
    if not matching_claims and overlaps:
        matching_claims = {max(overlaps, key=overlaps.__getitem__)}
    if not matching_claims:
        issues.append("graph.no_evidence_path")
    elif not matching_claims <= _connected_claims(graph, {next(iter(matching_claims))}):
        issues.append("graph.disconnected_claims")

    eligible_paths = [
        path
        for path in graph.paths
        if matching_claims <= set(path.claim_ids) and cited & set(path.evidence_ids)
    ]
    if requested_path_id is not None:
        requested = next((path for path in graph.paths if path.id == requested_path_id), None)
        if requested is None:
            issues.append("graph.unknown_path")
        elif requested not in eligible_paths:
            issues.append("graph.path_mismatch")
        else:
            eligible_paths = [requested]
    selected = max(eligible_paths, key=lambda item: (item.completeness, item.id), default=None)
    if selected is None:
        issues.append("graph.no_connected_blueprint")

    editorial = analyze_bullet_editorially(text)
    desired_slots = {"action", "object"}
    if editorial.structure.has_method:
        desired_slots.add("method")
    if editorial.structure.metrics or editorial.structure.scope_terms:
        desired_slots.add("scope")
    if editorial.structure.metrics or editorial.structure.functional_signals:
        desired_slots.add("proof")
    if editorial.structure.has_outcome:
        desired_slots.add("outcome")
    path_slots = set(selected.slots) if selected is not None else set()
    missing_slots = tuple(sorted(desired_slots - path_slots))
    if missing_slots:
        issues.append("graph.unsupported_slots")
    overlap_score = (
        round(
            100
            * len(
                generated_terms
                & {
                    term
                    for claim_id in (selected.claim_ids if selected is not None else ())
                    for node in claim_nodes
                    if node.id == claim_id
                    for term in _terms(node.text)
                }
            )
            / max(1, len(generated_terms))
        )
        if generated_terms
        else 0
    )
    return BulletGraphAlignment(
        passed=not issues,
        path_id=selected.id if selected is not None else None,
        overlap_score=overlap_score,
        aligned_slots=tuple(sorted(desired_slots & path_slots)),
        missing_slots=missing_slots,
        issue_codes=tuple(dict.fromkeys(issues)),
        claim_ids=tuple(sorted(matching_claims)),
    )
