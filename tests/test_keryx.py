from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import Mock, patch

from erga_mcp.cli import main
from erga_mcp.config import DEFAULT_CONFIG, load_config
from erga_mcp.keryx import (
    collect_optional_keryx,
    disable_keryx,
    enable_keryx,
    keryx_status,
    search_keryx_jobs,
    sync_keryx,
)
from erga_mcp.mcp_server import build_server


def _job(
    identifier: str,
    *,
    title: str,
    program: str,
    cycle: str,
    status: str = "open",
    url: str | None = "https://jobs.example.com/1",
) -> dict[str, object]:
    return {
        "id": identifier,
        "company": "Example Corp",
        "title": title,
        "location": "New York, NY",
        "program": program,
        "cycle": cycle,
        "posted_at": "2026-08-01",
        "status": status,
        "url": url,
        "url_host": "jobs.example.com",
        "url_fingerprint": (
            hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] if url is not None else "a" * 24
        ),
        "link_status": "cross-source" if url is not None else "unverified",
        "academic_eligibility": {
            "status": "explicit-date",
            "summary": "Expected May 2029 graduation",
            "requirement_level": "required",
            "graduation_evidence": "Applicants must graduate in May 2029.",
            "graduation_years": [2029],
            "graduation_start": "2029-05",
            "graduation_end": "2029-05",
            "checked_at": "2026-08-01",
            "confidence": "direct-ats",
            "source_label": "Greenhouse direct",
        },
    }


def _payload() -> bytes:
    document = {
        "schema_version": 2,
        "country": "United States",
        "jobs": [
            _job(
                "job_111111111111111111111111",
                title="Software Engineer Intern",
                program="internship",
                cycle="summer-2027",
            ),
            _job(
                "job_222222222222222222222222",
                title="Data Engineer, New Grad",
                program="new-grad",
                cycle="2027",
                url=None,
            ),
            _job(
                "job_333333333333333333333333",
                title="Closed Internship",
                program="internship",
                cycle="summer-2027",
                status="closed",
            ),
        ],
    }
    return json.dumps(document).encode("utf-8")


class KeryxTests(unittest.TestCase):
    def _config(self, root: Path, *, profile: str = "default") -> Path:
        config_path = root / "config.toml"
        config_path.write_text(
            DEFAULT_CONFIG.replace('tool_profile = "default"', f'tool_profile = "{profile}"'),
            encoding="utf-8",
        )
        return config_path

    def test_enable_validates_then_caches_without_sending_private_state(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))
            fetch = Mock(return_value=_payload())

            report = enable_keryx(
                config_path,
                fetch=fetch,
                fetched_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            )
            config = load_config(config_path)
            cache_path = config.data_dir / "integrations" / "keryx" / "jobs.json"

            self.assertTrue(config.keryx.enabled)
            self.assertEqual(report.cached_jobs, 3)
            self.assertEqual(fetch.call_args.args, ())
            self.assertTrue(cache_path.is_file())
            self.assertNotIn("resume", cache_path.read_text(encoding="utf-8").casefold())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o600)

    def test_search_is_local_read_only_and_never_creates_application_state(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))
            enable_keryx(config_path, fetch=_payload)

            result = search_keryx_jobs(
                load_config(config_path),
                query="software engineer",
                program="internship",
                cycle="summer-2027",
                location="new york",
            )

            self.assertTrue(result["query_was_local_only"])
            self.assertEqual(result["applications_created"], 0)
            self.assertEqual(result["total_matches"], 1)
            jobs = result["results"]
            assert isinstance(jobs, list)
            self.assertEqual(jobs[0]["title"], "Software Engineer Intern")
            self.assertEqual(
                jobs[0]["academic_eligibility"]["graduation_years"],  # type: ignore[index]
                [2029],
            )
            self.assertEqual(
                jobs[0]["academic_eligibility"]["requirement_level"],  # type: ignore[index]
                "required",
            )
            self.assertIn(
                "must graduate",
                jobs[0]["academic_eligibility"]["graduation_evidence"],  # type: ignore[index]
            )

    def test_closed_jobs_are_not_returned_and_limits_are_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))
            enable_keryx(config_path, fetch=_payload)
            config = load_config(config_path)

            result = search_keryx_jobs(config, program="internship")

            self.assertEqual(result["total_matches"], 1)
            with self.assertRaisesRegex(ValueError, "between 1 and 50"):
                search_keryx_jobs(config, limit=51)

    def test_invalid_link_integrity_fails_before_opt_in_is_recorded(self) -> None:
        document = json.loads(_payload())
        document["jobs"][0]["url_fingerprint"] = "0" * 24
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))

            with self.assertRaisesRegex(ValueError, "fingerprint"):
                enable_keryx(config_path, fetch=lambda: json.dumps(document).encode("utf-8"))

            config = load_config(config_path)
            self.assertFalse(config.keryx.enabled)
            self.assertFalse(keryx_status(config).cache_ready)

    def test_unsafe_url_and_unknown_requirement_modality_are_rejected(self) -> None:
        for mutation, message in (
            ({"url": "https://127.0.0.1/jobs/1", "url_host": "127.0.0.1"}, "IP-literal"),
            ({"academic_eligibility": {"requirement_level": "mandatory-ish"}}, "requirement"),
        ):
            document = json.loads(_payload())
            job = document["jobs"][0]
            if "url" in mutation:
                job.update(mutation)
                job["url_fingerprint"] = hashlib.sha256(job["url"].encode("utf-8")).hexdigest()[:24]
            else:
                job["academic_eligibility"].update(mutation["academic_eligibility"])
            with TemporaryDirectory() as directory:
                config_path = self._config(Path(directory))
                with self.assertRaisesRegex(ValueError, message):
                    enable_keryx(config_path, fetch=lambda: json.dumps(document).encode("utf-8"))

    def test_previous_schema_without_academic_metadata_remains_compatible(self) -> None:
        document = json.loads(_payload())
        document["schema_version"] = 1
        for job in document["jobs"]:
            job.pop("academic_eligibility")
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))

            report = enable_keryx(config_path, fetch=lambda: json.dumps(document).encode("utf-8"))

            self.assertEqual(report.cached_jobs, 3)
            result = search_keryx_jobs(load_config(config_path), query="software")
            self.assertIsNone(result["results"][0]["academic_eligibility"])  # type: ignore[index]

    def test_disable_keeps_public_cache_but_blocks_search_and_sync(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))
            enable_keryx(config_path, fetch=_payload)

            status = disable_keryx(config_path)
            config = load_config(config_path)

            self.assertFalse(status.enabled)
            self.assertTrue(status.cache_ready)
            with self.assertRaisesRegex(ValueError, "disabled"):
                search_keryx_jobs(config)
            with self.assertRaisesRegex(ValueError, "disabled"):
                sync_keryx(config, fetch=_payload)

    def test_career_mcp_tool_searches_cache_without_automatic_intake(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory), profile="career")
            enable_keryx(config_path, fetch=_payload)
            server = build_server(config_path)

            tools = {tool.name for tool in asyncio.run(server.list_tools())}
            response: Any = asyncio.run(
                server.call_tool(
                    "search_keryx_jobs",
                    {"query": "software", "program": "internship"},
                )
            )
            capabilities: Any = asyncio.run(server.call_tool("erga_capabilities", {}))

            self.assertIn("search_keryx_jobs", tools)
            self.assertEqual(response.structured_content["applications_created"], 0)
            self.assertEqual(response.structured_content["total_matches"], 1)
            self.assertEqual(
                capabilities.structured_content["optional_integrations"]["keryx"],
                {"enabled": True, "cache_ready": True},
            )
            self.assertNotIn(str(config_path), json.dumps(capabilities.structured_content))

    def test_onboarding_prompt_is_optional_and_defaults_to_disabled(self) -> None:
        prompt = Mock()
        prompt.ask.return_value = False
        with patch("erga_mcp.keryx.questionary.confirm", return_value=prompt) as confirm:
            selected = collect_optional_keryx()

        self.assertFalse(selected)
        self.assertFalse(confirm.call_args.kwargs["default"])

    def test_cli_search_reads_cache_and_reports_failures_without_tracebacks(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = self._config(Path(directory))
            enable_keryx(config_path, fetch=_payload)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "keryx",
                        "search",
                        "software engineer",
                        "--program",
                        "internship",
                        "--config",
                        str(config_path),
                    ]
                )
            result = json.loads(stdout.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertTrue(result["query_was_local_only"])
            self.assertEqual(result["applications_created"], 0)

            disable_keryx(config_path)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["keryx", "search", "--config", str(config_path)])
            self.assertEqual(exit_code, 1)
            self.assertIn("disabled", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
