from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from erga_mcp.models import Application, AuditEvent
from erga_mcp.tracking.orbit import (
    _ERGA_ORBIT_PALETTE,
    _NODE_LABEL_GAP,
    _STATUS_COLORS,
    _TREE_CHILDREN,
    _TREE_NODE_SPECS,
    _TREE_RIBBON_COLORS,
    _draw_flow_node,
    build_orbit_snapshot,
    render_orbit_png,
)
from erga_mcp.tracking.tracker import TrackerEntry

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _application(identifier: str, *, status: str, url: str | None = None) -> Application:
    return Application(
        id=identifier,
        company=f"Company {identifier}",
        role="Software Engineer Intern",
        source_url=url or f"https://jobs.example.test/{identifier}",
        status=status,
        evidence_ids=[],
        created_at=NOW,
    )


def _audit(
    application_id: str,
    index: int,
    *,
    action: str,
    payload: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        id=f"audit-{application_id}-{index}",
        action=action,
        subject_id=application_id,
        payload=payload,
        created_at=NOW + timedelta(minutes=index),
    )


def _journey(application_id: str, statuses: tuple[str, ...]) -> list[AuditEvent]:
    events = [
        _audit(
            application_id,
            0,
            action="application.created",
            payload={"status": statuses[0]},
        )
    ]
    for index, (previous, current) in enumerate(zip(statuses, statuses[1:]), start=1):
        events.append(
            _audit(
                application_id,
                index,
                action="application.status_updated",
                payload={"from": previous, "to": current},
            )
        )
    return events


class OrbitTests(unittest.TestCase):
    def test_builds_truthful_funnel_from_recorded_status_transitions(self) -> None:
        applications = [
            _application("one", status="rejected"),
            _application("two", status="interview"),
            _application("three", status="applied"),
            _application("four", status="draft"),
        ]
        audits = [
            *_journey("one", ("draft", "applied", "oa", "rejected")),
            *_journey("two", ("draft", "applied", "interview")),
            *_journey("three", ("draft", "applied")),
            *_journey("four", ("draft",)),
        ]

        snapshot = build_orbit_snapshot(applications, audits)
        links = {(link.source, link.target): link.count for link in snapshot.links}

        self.assertEqual(snapshot.tracked_count, 3)
        self.assertEqual(snapshot.local_application_count, 3)
        self.assertEqual(snapshot.recorded_history_count, 3)
        self.assertEqual(snapshot.snapshot_only_count, 0)
        self.assertEqual(links[("applications", "open")], 2)
        self.assertEqual(links[("applications", "closed")], 1)
        self.assertEqual(links[("open", "active-pipeline")], 1)
        self.assertEqual(links[("open", "no-response")], 1)
        self.assertEqual(links[("active-pipeline", "in-process")], 1)
        self.assertEqual(links[("closed", "other-closed")], 1)
        self.assertEqual(links[("other-closed", "rejection-outcome")], 1)
        self.assertEqual(links[("rejection-outcome", "no-offer")], 1)
        self.assertNotIn("draft", {node.id for node in snapshot.nodes})

    def test_collapses_raw_statuses_into_a_readable_outcome_tree(self) -> None:
        journeys = {
            "accepted": ("draft", "applied", "interview", "offer", "accepted"),
            "declined": ("draft", "applied", "interview", "offer", "declined"),
            "no-offer": ("draft", "applied", "interview", "rejected"),
            "interviewing": ("draft", "applied", "oa", "interview"),
            "rejected-one": ("draft", "applied", "rejected"),
            "rejected-two": ("draft", "applied", "rejected"),
            "pending-one": ("draft", "applied"),
            "pending-two": ("draft", "applied"),
        }
        applications = [
            _application(identifier, status=statuses[-1])
            for identifier, statuses in journeys.items()
        ]
        audits = [
            event
            for identifier, statuses in journeys.items()
            for event in _journey(identifier, statuses)
        ]

        snapshot = build_orbit_snapshot(applications, audits)
        links = {(link.source, link.target): link.count for link in snapshot.links}
        node_colors = {node.id: node.color for node in snapshot.nodes}

        self.assertEqual(links[("applications", "open")], 3)
        self.assertEqual(links[("applications", "closed")], 5)
        self.assertEqual(links[("open", "active-pipeline")], 1)
        self.assertEqual(links[("open", "no-response")], 2)
        self.assertEqual(links[("active-pipeline", "in-process")], 1)
        self.assertEqual(links[("closed", "offer-decided")], 2)
        self.assertEqual(links[("closed", "other-closed")], 3)
        self.assertEqual(links[("offer-decided", "accepted")], 1)
        self.assertEqual(links[("offer-decided", "declined")], 1)
        self.assertEqual(links[("other-closed", "rejection-outcome")], 3)
        self.assertEqual(links[("rejection-outcome", "rejected")], 2)
        self.assertEqual(links[("rejection-outcome", "no-offer")], 1)
        self.assertLessEqual(set(node_colors.values()), _ERGA_ORBIT_PALETTE)
        self.assertEqual(node_colors["applications"], "#171717")
        self.assertEqual(node_colors["open"], "#7C5CFF")
        self.assertEqual(node_colors["offer-decided"], "#7FC2FE")
        self.assertEqual(node_colors["accepted"], "#83FE7F")
        self.assertEqual(node_colors["declined"], "#FE7F7F")
        self.assertEqual(node_colors["no-response"], "#FEF17F")

    def test_node_counts_have_a_clear_gap_before_every_label(self) -> None:
        self.assertGreaterEqual(_NODE_LABEL_GAP, 10)

    def test_flow_nodes_square_only_the_edges_connected_to_ribbons(self) -> None:
        color = "#FE7F7F"

        def corner_pixels(*, incoming: bool, outgoing: bool) -> tuple[object, object]:
            image = Image.new("RGB", (30, 30), "white")
            _draw_flow_node(
                ImageDraw.Draw(image),
                x=8,
                y0=5,
                y1=25,
                color=color,
                scale=1,
                has_incoming=incoming,
                has_outgoing=outgoing,
            )
            return image.getpixel((8, 5)), image.getpixel((22, 5))

        coral = (254, 127, 127)
        white = (255, 255, 255)
        self.assertEqual(corner_pixels(incoming=False, outgoing=True), (white, coral))
        self.assertEqual(corner_pixels(incoming=True, outgoing=True), (coral, coral))
        self.assertEqual(corner_pixels(incoming=True, outgoing=False), (coral, white))

    def test_every_orbit_color_comes_from_the_erga_brand_palette(self) -> None:
        self.assertLessEqual(set(_STATUS_COLORS.values()), _ERGA_ORBIT_PALETTE)
        self.assertLessEqual(
            {color for _, _, color in _TREE_NODE_SPECS.values()},
            _ERGA_ORBIT_PALETTE,
        )
        self.assertLessEqual(set(_TREE_RIBBON_COLORS.values()), _ERGA_ORBIT_PALETTE)

    def test_orbit_topology_is_a_strict_binary_tree(self) -> None:
        self.assertTrue(_TREE_CHILDREN)
        self.assertTrue(all(len(children) <= 2 for children in _TREE_CHILDREN.values()))
        parents: dict[str, str] = {}
        for parent, children in _TREE_CHILDREN.items():
            for child in children:
                self.assertNotIn(child, parents)
                parents[child] = parent
        self.assertNotIn("applications", parents)
        self.assertEqual(set(parents) | {"applications"}, set(_TREE_NODE_SPECS))

    def test_every_applied_role_flows_to_one_visible_current_branch(self) -> None:
        applications = [
            _application("pending-one", status="applied"),
            _application("pending-two", status="applied"),
            _application("assessment", status="oa"),
            _application("rejected", status="rejected"),
        ]

        snapshot = build_orbit_snapshot(applications, [])
        nodes = {node.id: node for node in snapshot.nodes}
        outgoing_from_applications = sum(
            link.count for link in snapshot.links if link.source == "applications"
        )

        self.assertEqual(snapshot.tracked_count, 4)
        self.assertEqual(nodes["no-response"].label, "No response")
        self.assertEqual(nodes["no-response"].count, 2)
        self.assertEqual(nodes["active-pipeline"].count, 1)
        self.assertEqual(nodes["in-process"].count, 1)
        self.assertEqual(nodes["rejected"].count, 1)
        self.assertEqual(outgoing_from_applications, snapshot.tracked_count)

    def test_tracker_only_rows_are_labeled_as_snapshot_only_not_fake_history(self) -> None:
        tracker = TrackerEntry(
            cycle="Summer 2027",
            company="Example",
            role="Software Engineer Intern",
            location="Remote",
            source_url="https://jobs.example.test/tracker-only",
            status="Interview",
            applied="2026-08-01",
            next_action="Prepare",
        )

        snapshot = build_orbit_snapshot([], [], tracker_entries=(tracker,))
        links = {(link.source, link.target, link.recorded): link.count for link in snapshot.links}

        self.assertEqual(snapshot.tracked_count, 1)
        self.assertEqual(snapshot.recorded_history_count, 0)
        self.assertEqual(snapshot.snapshot_only_count, 1)
        self.assertEqual(links[("applications", "open", False)], 1)
        self.assertEqual(links[("open", "active-pipeline", False)], 1)
        self.assertEqual(links[("active-pipeline", "in-process", False)], 1)
        self.assertNotIn("history-unavailable", {node.id for node in snapshot.nodes})

    def test_numbered_interviews_are_real_stages_without_setup_jargon(self) -> None:
        tracker = TrackerEntry(
            cycle="Summer 2027",
            company="Example",
            role="Software Engineer Intern",
            location="Remote",
            source_url="https://jobs.example.test/interview-two",
            status="Interview 2",
            applied="2026-08-01",
            next_action="Prepare",
        )

        snapshot = build_orbit_snapshot([], [], tracker_entries=(tracker,))
        labels = {node.label for node in snapshot.nodes}
        links = {(link.source, link.target, link.recorded) for link in snapshot.links}

        self.assertEqual(
            labels,
            {"Applications", "Open applications", "Active pipeline", "In process"},
        )
        self.assertEqual(
            links,
            {
                ("applications", "open", False),
                ("open", "active-pipeline", False),
                ("active-pipeline", "in-process", False),
            },
        )

    def test_pre_application_roles_are_not_part_of_the_funnel(self) -> None:
        applications = [
            _application("draft", status="draft"),
            _application("research", status="researching"),
        ]

        snapshot = build_orbit_snapshot(applications, [])

        self.assertEqual(snapshot.tracked_count, 0)
        self.assertEqual(snapshot.local_application_count, 0)
        self.assertEqual(snapshot.nodes, ())
        self.assertEqual(snapshot.links, ())

    def test_cycle_filter_uses_tracker_identity_without_duplicating_local_records(self) -> None:
        url = "https://jobs.example.test/role?utm_source=test"
        application = _application("one", status="applied", url=url)
        tracker = TrackerEntry(
            cycle="Summer 2027",
            company="Example",
            role="Software Engineer Intern",
            location="Remote",
            source_url="https://jobs.example.test/role",
            status="Applied",
            applied="2026-08-01",
            next_action="Wait",
        )

        included = build_orbit_snapshot(
            [application],
            _journey("one", ("draft", "applied")),
            tracker_entries=(tracker,),
            cycle="Summer 2027",
        )
        excluded = build_orbit_snapshot(
            [application],
            _journey("one", ("draft", "applied")),
            tracker_entries=(tracker,),
            cycle="Fall 2027",
        )

        self.assertEqual(included.tracked_count, 1)
        self.assertEqual(included.tracker_only_count, 0)
        self.assertEqual(excluded.tracked_count, 0)

    def test_hash_is_stable_for_identical_pipeline_data(self) -> None:
        application = _application("one", status="applied")
        audits = _journey("one", ("draft", "applied"))

        first = build_orbit_snapshot([application], audits, generated_at=NOW)
        second = build_orbit_snapshot([application], audits, generated_at=NOW + timedelta(hours=1))

        self.assertEqual(first.content_hash, second.content_hash)

    def test_reopened_terminal_status_starts_a_current_journey_not_a_backward_link(self) -> None:
        application = _application("one", status="oa")
        audits = _journey("one", ("draft", "applied", "rejected", "oa"))

        snapshot = build_orbit_snapshot([application], audits)
        links = {(link.source, link.target) for link in snapshot.links}

        self.assertEqual(snapshot.corrected_transition_count, 1)
        self.assertIn(("applications", "open"), links)
        self.assertIn(("open", "active-pipeline"), links)
        self.assertIn(("active-pipeline", "in-process"), links)
        self.assertNotIn(("applications", "closed"), links)

    def test_renders_an_erga_branded_png(self) -> None:
        application = _application("one", status="rejected")
        snapshot = build_orbit_snapshot(
            [application],
            _journey("one", ("draft", "applied", "oa", "rejected")),
            generated_at=NOW,
        )

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "erga-orbit.png"
            rendered = render_orbit_png(snapshot, destination)

            self.assertEqual(rendered, destination)
            self.assertEqual(destination.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertGreater(destination.stat().st_size, 10_000)
            rendered_image = Image.open(destination).convert("RGB")
            color_counts = rendered_image.getcolors(
                maxcolors=rendered_image.width * rendered_image.height
            )
            self.assertIsNotNone(color_counts)
            colors = {color for _, color in color_counts or []}
            self.assertTrue(any(b > r + 25 and b > g + 10 for r, g, b in colors))
            self.assertTrue(any(r > g + 25 and r > b + 25 for r, g, b in colors))
            title_colors = rendered_image.crop((500, 20, 1100, 100)).getcolors(maxcolors=600 * 80)
            logo_colors = rendered_image.crop((1320, 804, 1570, 892)).getcolors(maxcolors=250 * 88)
            self.assertTrue(
                any(max(color) < 60 for _, color in title_colors or []),
                "the title should render in simple black text",
            )
            self.assertTrue(
                any(max(color) < 60 for _, color in logo_colors or []),
                "the complete logo should include its black ERGA wordmark",
            )


if __name__ == "__main__":
    unittest.main()
