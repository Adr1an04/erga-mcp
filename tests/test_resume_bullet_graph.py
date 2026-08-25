from __future__ import annotations

import unittest

from erga_mcp.resumes.bullet_graph import (
    align_bullet_to_graph,
    build_evidence_bullet_graph,
)


def _sources() -> list[dict[str, object]]:
    return [
        {
            "kind": "approved_resume_bullet",
            "text": (
                "Built a Python API serving 100 users. "
                "Implemented authenticated requests for the API. "
                "Built a separate Kafka benchmark with authenticated requests."
            ),
            "evidence_ids": ["ev_api"],
        }
    ]


class ResumeBulletGraphTests(unittest.TestCase):
    def test_builds_typed_nodes_and_bottom_up_assembly_paths(self) -> None:
        graph = build_evidence_bullet_graph("api-platform", _sources())

        kinds = {node.kind for node in graph.nodes}
        self.assertTrue({"claim", "action", "object", "method", "scope", "proof"} <= kinds)
        connected = next(path for path in graph.paths if len(path.claim_ids) == 2)
        self.assertEqual(connected.assembly_order[0], "object")
        self.assertEqual(connected.assembly_order[-1], "action")
        self.assertEqual(connected.render_order[0], "action")
        self.assertEqual(connected.render_order[1], "object")
        self.assertGreaterEqual(connected.completeness, 80)

    def test_connects_an_explicitly_referenced_implementation_to_its_subject(self) -> None:
        graph = build_evidence_bullet_graph("api-platform", _sources())
        alignment = align_bullet_to_graph(
            "Engineered a Python API serving 100 users with authenticated requests.",
            ("ev_api",),
            graph,
        )

        self.assertTrue(alignment.passed)
        self.assertIsNotNone(alignment.path_id)
        self.assertIn("proof", alignment.aligned_slots)
        self.assertGreaterEqual(alignment.overlap_score, 80)

    def test_rejects_a_sentence_that_fuses_disconnected_components(self) -> None:
        graph = build_evidence_bullet_graph("api-platform", _sources())
        alignment = align_bullet_to_graph(
            "Engineered a Python Kafka benchmark serving 100 users with authenticated requests.",
            ("ev_api",),
            graph,
        )

        self.assertFalse(alignment.passed)
        self.assertIn("graph.disconnected_claims", alignment.issue_codes)

    def test_rejects_an_unknown_model_selected_path(self) -> None:
        graph = build_evidence_bullet_graph("api-platform", _sources())
        alignment = align_bullet_to_graph(
            "Engineered a Python API serving 100 users with authenticated requests.",
            ("ev_api",),
            graph,
            requested_path_id="bg_not_real",
        )

        self.assertFalse(alignment.passed)
        self.assertIn("graph.unknown_path", alignment.issue_codes)

    def test_graph_identifiers_are_deterministic(self) -> None:
        first = build_evidence_bullet_graph("api-platform", _sources())
        second = build_evidence_bullet_graph("api-platform", _sources())

        self.assertEqual(first, second)

    def test_clause_parsing_preserves_decimal_metrics_and_dotted_technologies(self) -> None:
        graph = build_evidence_bullet_graph(
            "dashboard",
            [
                {
                    "kind": "approved_resume_bullet",
                    "text": (
                        "Built a Next.js dashboard with 99.3% request accuracy. "
                        "Reduced alert triage through schema validation."
                    ),
                    "evidence_ids": ["ev_dashboard"],
                }
            ],
        )

        claims = [node.text for node in graph.nodes if node.kind == "claim"]
        self.assertEqual(len(claims), 2)
        self.assertIn("Next.js", claims[0])
        self.assertIn("99.3%", claims[0])


if __name__ == "__main__":
    unittest.main()
