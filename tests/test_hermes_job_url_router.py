from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

_PLUGIN_DIR = (
    Path(__file__).parents[1] / "integrations" / "hermes" / "plugins" / "recruiting-pipeline-router"
)


def _load_router() -> ModuleType:
    plugin_path = _PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("recruiting_pipeline_router", plugin_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakePluginContext:
    def __init__(
        self,
        *,
        result: str = '{"package_dir":"/tmp/example"}',
        results: list[str | BaseException] | None = None,
    ) -> None:
        self.result = result
        self.results = list(results or [])
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.hooks: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}

    def dispatch_tool(self, name: str, arguments: dict[str, str]) -> str:
        """Match Hermes 0.18.2's stable two-positional-argument dispatch usage."""
        self.calls.append((name, arguments))
        if self.results:
            next_result = self.results.pop(0)
            if isinstance(next_result, BaseException):
                raise next_result
            return next_result
        return self.result

    def register_hook(self, name: str, handler: Any) -> None:
        self.hooks[name] = handler

    def register_command(self, name: str, *, handler: Any, **_: Any) -> None:
        self.commands[name] = handler


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class HermesJobUrlRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = _load_router()

    def test_extracts_a_bare_ashby_url_unchanged(self) -> None:
        url = (
            "https://jobs.ashbyhq.com/example/"
            "00000000-0000-0000-0000-000000000000?source=Discord%20preview"
        )

        self.assertEqual(self.router.extract_job_url(url), url)

    def test_extracts_angle_bracket_link_before_unfurled_job_text(self) -> None:
        url = "https://careers.example.com/openings/software-engineering-intern"
        message = (
            f"<{url}>\nSoftware Engineering Internship\n"
            "Company overview, responsibilities, qualifications, and salary"
        )

        self.assertEqual(self.router.extract_job_url(message), url)

    def test_recognizes_common_ats_and_company_careers_links(self) -> None:
        urls = [
            "https://boards.greenhouse.io/example/jobs/12345",
            "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000",
            "https://example.wd1.myworkdayjobs.com/en-US/Careers/job/Example/12345",
            "https://example.breezy.hr/p/12345-software-engineer",
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/jobs/123",
            "https://careers.example.test/positions/software-engineer",
            "https://example.test/open-roles/software-engineer",
            "https://example.test/listing?jk=synthetic-posting-id",
        ]

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.router.extract_job_url(url), url)

    def test_prefers_job_link_over_preview_media_and_other_links(self) -> None:
        job_url = "https://boards.greenhouse.io/example/jobs/12345"
        message = (
            "https://cdn.example.test/preview.png\n"
            "https://example.test/company/about\n"
            f"{job_url}\nJob description"
        )

        self.assertEqual(self.router.extract_job_url(message), job_url)

    def test_does_not_route_non_job_links_or_linkedin_profiles(self) -> None:
        self.assertIsNone(self.router.extract_job_url("https://github.com/example/project"))
        self.assertIsNone(self.router.extract_job_url("https://linkedin.com/in/example-person"))

    def test_respects_explicit_summary_only_opt_out(self) -> None:
        message = (
            "Just summarize, don't intake: "
            "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000"
        )

        self.assertIsNone(self.router.extract_job_url(message))

    def test_negated_summary_request_still_routes_to_pipeline(self) -> None:
        url = "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000"

        self.assertEqual(
            self.router.extract_job_url(f"not just summarize—run the pipeline {url}"),
            url,
        )
        self.assertEqual(
            self.router.extract_job_url(f"Don't just summarize—run the pipeline: {url}"),
            url,
        )
        self.assertEqual(
            self.router.extract_job_url(f"Do not just summarize; use the intake for {url}"),
            url,
        )
        self.assertEqual(
            self.router.extract_job_url(f"Never just summarize; run the pipeline for {url}"),
            url,
        )

    def test_explicit_pipeline_opt_out_wins_over_negated_summary(self) -> None:
        url = "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000"
        message = f"Don't just summarize, but don't run the pipeline either: {url}"

        self.assertIsNone(self.router.extract_job_url(message))
        self.assertIsNone(self.router.extract_job_url(f"don’t run the pipeline {url}"))
        self.assertIsNone(self.router.extract_job_url(f"never run the pipeline {url}"))

    def test_pre_model_hook_dispatches_intake_once_and_injects_the_result(self) -> None:
        context = _FakePluginContext()
        self.router.register(context)
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"

        injected = context.hooks["pre_llm_call"](
            user_message=f"{url}\nSoftware Intern",
            session_id="session-1",
            task_id="task-1",
            turn_id="turn-1",
            platform="discord",
            conversation_history=[],
            is_first_turn=True,
            model="test-model",
            sender_id="synthetic-user",
            telemetry_schema_version="hermes.observer.v1",
        )
        repeated = context.hooks["pre_llm_call"](
            user_message=f"{url}\nSoftware Intern",
            session_id="session-1",
            task_id="task-1",
            turn_id="turn-1",
            platform="discord",
        )

        self.assertEqual(
            context.calls,
            [("mcp__recruiting_pipeline__intake_job_url", {"job_url": url})],
        )
        assert injected is not None
        assert repeated is not None
        self.assertIn("called before this model turn", injected["context"])
        self.assertIn('"package_dir":"/tmp/example"', injected["context"])
        self.assertIn('"package_dir":"/tmp/example"', repeated["context"])
        self.assertIn("Do not call a browser", injected["context"])

    def test_retries_only_transient_mcp_startup_errors(self) -> None:
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        tool_name = "mcp__recruiting_pipeline__intake_job_url"
        context = _FakePluginContext(
            results=[
                json.dumps({"error": f"Unknown tool: {tool_name}"}),
                json.dumps({"error": "MCP server 'recruiting-pipeline' is not connected"}),
                '{"package_dir":"/tmp/ready"}',
            ]
        )
        clock = _FakeClock()
        env = {
            "RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS": "2",
            "RECRUITING_PIPELINE_MCP_READY_RETRY_SECONDS": "0.25",
        }

        with patch.dict(os.environ, env, clear=False):
            self.router.register(context, monotonic=clock.monotonic, sleep=clock.sleep)
        injected = context.hooks["pre_llm_call"](
            user_message=url,
            session_id="session-ready",
            turn_id="turn-ready",
        )

        self.assertEqual(len(context.calls), 3)
        self.assertEqual(clock.sleeps, [0.25, 0.25])
        assert injected is not None
        self.assertIn('"package_dir":"/tmp/ready"', injected["context"])

    def test_does_not_retry_operational_intake_errors(self) -> None:
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        context = _FakePluginContext(
            results=[
                '{"error":"job URL resolved to a private address"}',
                '{"package_dir":"/tmp/must-not-run"}',
            ]
        )
        clock = _FakeClock()

        self.router.register(context, monotonic=clock.monotonic, sleep=clock.sleep)
        injected = context.hooks["pre_llm_call"](
            user_message=url,
            session_id="session-error",
            turn_id="turn-error",
        )

        self.assertEqual(len(context.calls), 1)
        self.assertEqual(clock.sleeps, [])
        assert injected is not None
        self.assertIn("private address", injected["context"])
        self.assertNotIn("must-not-run", injected["context"])

    def test_retry_classifier_rejects_other_tool_and_mcp_errors(self) -> None:
        tool_name = "mcp__recruiting_pipeline__intake_job_url"
        non_readiness_errors = [
            "Unknown tool: browser",
            "MCP server 'recruiting-pipeline' transport is down; reconnect requested",
            "MCP server 'recruiting-pipeline' is unreachable",
            "job URL resolved to a private address",
        ]

        for error in non_readiness_errors:
            with self.subTest(error=error):
                self.assertFalse(
                    self.router._is_retryable_startup_error(error, tool_name=tool_name)
                )

    def test_readiness_retry_is_bounded_by_configured_timeout(self) -> None:
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        tool_name = "mcp__recruiting_pipeline__intake_job_url"
        startup_error = json.dumps({"error": f"Unknown tool: {tool_name}"})
        context = _FakePluginContext(result=startup_error)
        clock = _FakeClock()
        env = {
            "RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS": "0.5",
            "RECRUITING_PIPELINE_MCP_READY_RETRY_SECONDS": "0.2",
        }

        with patch.dict(os.environ, env, clear=False):
            self.router.register(context, monotonic=clock.monotonic, sleep=clock.sleep)
        injected = context.hooks["pre_llm_call"](
            user_message=url,
            session_id="session-timeout",
            turn_id="turn-timeout",
        )

        self.assertLessEqual(sum(clock.sleeps), 0.500001)
        self.assertEqual(clock.now, 0.5)
        assert injected is not None
        self.assertIn("after waiting 0.5s for MCP readiness", injected["context"])

    def test_readiness_defaults_and_environment_are_hard_bounded(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            timeout, retry_interval = self.router._readiness_settings()
        self.assertEqual(timeout, 30)
        self.assertGreater(retry_interval, 0)

        with patch.dict(
            os.environ,
            {
                "RECRUITING_PIPELINE_MCP_READY_TIMEOUT_SECONDS": "999",
                "RECRUITING_PIPELINE_MCP_READY_RETRY_SECONDS": "999",
            },
            clear=True,
        ):
            timeout, retry_interval = self.router._readiness_settings()
        self.assertEqual(timeout, 30)
        self.assertEqual(retry_interval, 5)

    def test_declares_and_checks_hermes_0182_compatibility(self) -> None:
        manifest = (_PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")

        self.assertIn('hermes_requires: ">=0.18.2"', manifest)
        self.assertFalse(self.router.supports_hermes_version("0.18.1"))
        self.assertTrue(self.router.supports_hermes_version("0.18.2"))
        self.assertTrue(self.router.supports_hermes_version("0.19.0-dev1"))

    def test_rejects_an_older_hermes_host_at_registration(self) -> None:
        old_hermes = ModuleType("hermes_cli")
        old_hermes.__version__ = "0.18.1"  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"hermes_cli": old_hermes}):
            with self.assertRaisesRegex(RuntimeError, r"requires Hermes >= 0\.18\.2"):
                self.router.register(_FakePluginContext())

    def test_explicit_slash_command_dispatches_the_same_tool(self) -> None:
        context = _FakePluginContext(result="done")
        self.router.register(context)
        url = "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000"

        result = context.commands["intake-job"](url)

        self.assertEqual(result, "done")
        self.assertEqual(len(context.calls), 1)
        self.assertEqual(context.calls[0][1], {"job_url": url})


if __name__ == "__main__":
    unittest.main()
