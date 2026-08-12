from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from erga_mcp.models import Application, AuditEvent
from erga_mcp.tracking.orbit import build_orbit_snapshot, render_orbit_png
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
        self.assertEqual(links[("applied", "oa")], 1)
        self.assertEqual(links[("oa", "rejected-stage-2")], 1)
        self.assertEqual(links[("applied", "interview")], 1)
        self.assertEqual(links[("applied", "awaiting-response")], 1)
        self.assertNotIn("draft", {node.id for node in snapshot.nodes})

    def test_every_applied_role_flows_to_one_visible_current_branch(self) -> None:
        applications = [
            _application("pending-one", status="applied"),
            _application("pending-two", status="applied"),
            _application("assessment", status="oa"),
            _application("rejected", status="rejected"),
        ]

        snapshot = build_orbit_snapshot(applications, [])
        nodes = {node.id: node for node in snapshot.nodes}
        outgoing_from_applied = sum(
            link.count for link in snapshot.links if link.source == "applied"
        )

        self.assertEqual(snapshot.tracked_count, 4)
        self.assertEqual(nodes["awaiting-response"].label, "No response")
        self.assertEqual(nodes["awaiting-response"].count, 2)
        self.assertEqual(nodes["oa"].count, 1)
        self.assertEqual(nodes["rejected-stage-1"].count, 1)
        self.assertEqual(outgoing_from_applied, snapshot.tracked_count)

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
        self.assertEqual(links[("applied", "interview", False)], 1)
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

        self.assertEqual(labels, {"Applied", "Interview 2"})
        self.assertEqual(links, {("applied", "interview-2", False)})

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
        self.assertIn(("applied", "oa"), links)
        self.assertNotIn(("rejected-stage-1", "oa"), links)

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
            self.assertTrue(any(g > r + 25 and g > b + 25 for r, g, b in colors))
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
