from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.mcp.server import build_server
from erga_mcp.store import ErgaStore


class TrackerMcpTests(unittest.TestCase):
    def test_explicit_status_update_synchronizes_the_exact_obsidian_tracker_row(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            job_url = "https://jobs.example.test/role-123"
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            tracker_path.write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                f"| Example | Engineer | Remote | [Posting]({job_url}) | Draft |  | "
                "Prepare and submit application. | Note |\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            application = store.create_application(
                company="Example",
                role="Engineer",
                source_url=job_url,
                evidence_ids=[],
            )

            result: Any = asyncio.run(
                build_server(config_path).call_tool(
                    "update_application_status",
                    {"application_id": application.id, "status": "applied"},
                )
            )
            payload = cast(dict[str, Any], result.structured_content)

            self.assertEqual(payload["status"], "applied")
            self.assertEqual(payload["tracker_updates"], 1)
            rendered = tracker_path.read_text(encoding="utf-8")
            self.assertIn("| Applied |", rendered)
            self.assertIn("Await acknowledgement or recruiting update.", rendered)

    def test_renders_a_token_free_orbit_artifact_from_local_tracking_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            application = store.create_application(
                company="Example",
                role="Engineer Intern",
                source_url="https://jobs.example.test/role",
                evidence_ids=[],
            )
            store.update_application_status(application.id, status="applied")

            result: Any = asyncio.run(build_server(config_path).call_tool("application_orbit", {}))
            payload = cast(dict[str, Any], result.structured_content)
            image_path = Path(payload["image_path"])

            self.assertTrue(image_path.is_file())
            self.assertEqual(image_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            self.assertFalse(payload["model_api_used"])
            self.assertFalse(payload["retain_generated_images"])
            self.assertEqual(payload["snapshot"]["tracked_count"], 1)
            self.assertEqual(payload["message"], "**Orbit**")

            updated: Any = asyncio.run(
                build_server(config_path).call_tool(
                    "update_orbit_preferences",
                    {"retain_generated_images": True},
                )
            )
            updated_payload = cast(dict[str, Any], updated.structured_content)
            self.assertTrue(updated_payload["retain_generated_images"])
            self.assertTrue(load_config(config_path).orbit.retain_generated_images)

    def test_returns_a_rendered_obsidian_tracker_without_writing_the_vault(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            tracker_path = tracker / "Fall 2026 Application Tracker.md"
            original = (
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Example Co | Software Engineer Intern | Remote | Source | Applied | "
                "2026-07-20 | Await update | Note |\n"
            )
            tracker_path.write_text(original, encoding="utf-8")
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )

            result: Any = asyncio.run(
                build_server(config_path).call_tool("application_tracker", {})
            )
            payload = cast(dict[str, Any], result.structured_content)

            self.assertEqual(payload["summary"], {"applied": 1})
            self.assertIn("Erga application tracker", payload["message"])
            self.assertIn("Example Co", payload["message"])
            self.assertEqual(tracker_path.read_text(encoding="utf-8"), original)

    def test_renders_tokens_alongside_a_matching_tracked_application(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            job_url = "https://jobs.example.test/123"
            (tracker / "Fall 2026 Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                f"| Example Co | Engineer | Remote | [Posting]({job_url}) | Applied | "
                "2026-07-20 | Await update | Note |\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            first_application = store.create_application(
                company="Example Co",
                role="Engineer",
                source_url=f"{job_url}?utm_source=tracker",
                evidence_ids=[],
            )
            second_application = store.create_application(
                company="Example Co", role="Engineer", source_url=job_url, evidence_ids=[]
            )
            store.record_token_usage(
                application_id=first_application.id,
                operation="intake",
                input_tokens=1_000,
                output_tokens=500,
            )
            store.record_token_usage(
                application_id=second_application.id,
                operation="brief_research",
                input_tokens=400,
                output_tokens=100,
            )

            result: Any = asyncio.run(
                build_server(config_path).call_tool("application_tracker", {})
            )
            payload = cast(dict[str, Any], result.structured_content)

        self.assertIn("Tokens: 1,400 in · 600 out · 2,000 total", payload["message"])
        self.assertEqual(payload["token_usage"]["total_tokens"], 2_000)

    def test_reports_disabled_tracking_without_reading_a_vault(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.toml"
            config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")

            result: Any = asyncio.run(
                build_server(config_path).call_tool("application_tracker", {})
            )
            payload = cast(dict[str, Any], result.structured_content)

        self.assertFalse(payload["enabled"])
        self.assertIn("not configured", payload["message"])

    def test_marks_only_oa_and_later_active_tracker_rows_for_research(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            (tracker / "Fall 2026 Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| Alpha | Engineer | Remote | [Posting](https://jobs.test/a) | Applied | "
                "| Wait | Note |\n"
                "| Beta | Engineer | Remote | [Posting](https://jobs.test/b) | OA | "
                "| Complete OA | Note |\n"
                "| Gamma | Engineer | Remote | [Posting](https://jobs.test/c) | Interview | "
                "| Prepare | Note |\n"
                "| Delta | Engineer | Remote | [Posting](https://jobs.test/d) | Rejected | "
                "| Archive | Note |\n",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )

            result: Any = asyncio.run(
                build_server(config_path).call_tool("application_tracker", {})
            )
            payload = cast(dict[str, Any], result.structured_content)

        by_company = {entry["company"]: entry for entry in payload["entries"]}
        self.assertEqual(by_company["Alpha"]["research"], {"eligible": False, "stage": None})
        self.assertEqual(by_company["Beta"]["research"], {"eligible": True, "stage": "oa"})
        self.assertEqual(by_company["Gamma"]["research"], {"eligible": True, "stage": "interview"})
        self.assertEqual(by_company["Delta"]["research"], {"eligible": False, "stage": None})

    def test_opens_saved_research_for_one_exact_oa_tracker_row(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tracker = root / "tracker"
            tracker.mkdir()
            job_url = "https://jobs.example.test/role"
            (tracker / "Summer 2027 Application Tracker.md").write_text(
                "| Company | Role | Location / work mode | Source | Status | Applied | "
                "Next action | Contact / link |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                f"| Example | Engineer Intern | Remote | [Posting]({job_url}) | OA | "
                "2026-08-01 | Complete OA | Note |\n",
                encoding="utf-8",
            )
            package = root / "output" / "summer-2027" / "example-engineer"
            (package / "research").mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"job_url": job_url}),
                encoding="utf-8",
            )
            (package / "research" / "discovery-research.md").write_text(
                "[Community report](https://reddit.com/r/example)",
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                DEFAULT_CONFIG.replace(
                    'enabled = false\ntracker_dir = ""',
                    'enabled = true\ntracker_dir = "tracker"',
                ),
                encoding="utf-8",
            )
            store = ErgaStore(load_config(config_path).data_dir / "erga.sqlite3")
            store.create_application(
                company="Example",
                role="Engineer Intern",
                source_url=job_url,
                evidence_ids=[],
            )

            result: Any = asyncio.run(
                build_server(config_path).call_tool(
                    "research_navigator", {"job_url": f"{job_url}?utm_source=test"}
                )
            )
            payload = cast(dict[str, Any], result.structured_content)

        self.assertEqual(payload["stage"], "oa")
        self.assertTrue(payload["package_available"])
        self.assertEqual(payload["saved_artifact_count"], 1)
        self.assertEqual(payload["links"][0]["label"], "Official posting")
        self.assertTrue(payload["links"][1]["unverified"])
        self.assertIn("OA research · Example", payload["card"]["title"])


if __name__ == "__main__":
    unittest.main()
