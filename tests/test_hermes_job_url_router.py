from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any
from unittest.mock import patch

_PLUGIN_DIR = Path(__file__).parents[1] / "integrations" / "hermes" / "plugins" / "erga-mcp-router"


def _load_router() -> ModuleType:
    plugin_path = _PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location("erga_mcp_router", plugin_path)
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
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.hooks: dict[str, Any] = {}
        self.commands: dict[str, Any] = {}
        self.discord_button_handlers: dict[str, Any] = {}

    def dispatch_tool(self, name: str, arguments: dict[str, Any]) -> str:
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

    def register_discord_button_handler(self, action_id: str, handler: Any) -> None:
        self.discord_button_handlers[action_id] = handler


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

    def test_component_tokens_are_user_bound_single_use_and_expire(self) -> None:
        clock = _FakeClock()
        tokens = self.router._ComponentTokenStore(
            ttl_seconds=10,
            monotonic=clock.monotonic,
        )
        token = tokens.issue("settings.show", {}, owner_user_id="42")

        with self.assertRaisesRegex(ValueError, "different Discord user"):
            tokens.consume(token, user_id="7")
        action, payload = tokens.consume(token, user_id="42")
        self.assertEqual((action, payload), ("settings.show", {}))
        with self.assertRaisesRegex(ValueError, "no longer available"):
            tokens.consume(token, user_id="42")

        expired = tokens.issue("settings.show", {}, owner_user_id="42")
        clock.sleep(11)
        with self.assertRaisesRegex(ValueError, "expired"):
            tokens.consume(expired, user_id="42")

    def test_discord_intake_plan_uses_emoji_questions_review_and_background_generation(
        self,
    ) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        def plan_payload(
            *,
            current_question: dict[str, Any] | None,
            answers: list[dict[str, str]],
            status: str,
        ) -> str:
            questions = [
                {
                    "id": "portfolio",
                    "prompt": "Which project story should this résumé tell?",
                    "options": [
                        {
                            "id": "balanced",
                            "label": "⚖️ Balanced",
                            "description": "Mix relevance and differentiation.",
                            "project_ids": ["robotics", "platform", "tooling"],
                            "project_titles": ["Robotics", "Platform", "Tooling"],
                            "recommended": True,
                        },
                        {
                            "id": "role_fit",
                            "label": "🎯 Closest match",
                            "description": "Prioritize direct posting overlap.",
                            "project_ids": ["robotics", "ml", "platform"],
                            "project_titles": ["Robotics", "ML", "Platform"],
                            "recommended": False,
                        },
                    ],
                },
                {
                    "id": "copy_strategy",
                    "prompt": "How aggressively should Erga tailor the bullets?",
                    "options": [
                        {
                            "id": "synthesize",
                            "label": "✨ Evidence-backed tailoring",
                            "description": "Draft only from approved evidence.",
                            "recommended": True,
                        },
                        {
                            "id": "preserve",
                            "label": "🛡️ Preserve master copy",
                            "description": "Keep established wording.",
                            "recommended": False,
                        },
                    ],
                },
            ]
            return json.dumps(
                {
                    "id": "plan_example",
                    "job_url": "https://jobs.example.test/engineer",
                    "company": "Example",
                    "role": "Software Engineer",
                    "questions": questions,
                    "answers": answers,
                    "current_question": current_question,
                    "status": status,
                    "catalogue_candidate_count": 8,
                }
            )

        portfolio = {
            "id": "portfolio",
            "prompt": "Which project story should this résumé tell?",
            "options": [
                {
                    "id": "balanced",
                    "label": "⚖️ Balanced",
                    "description": "Mix relevance and differentiation.",
                    "project_ids": ["robotics", "platform", "tooling"],
                    "project_titles": ["Robotics", "Platform", "Tooling"],
                    "recommended": True,
                },
                {
                    "id": "role_fit",
                    "label": "🎯 Closest match",
                    "description": "Prioritize direct posting overlap.",
                    "project_ids": ["robotics", "ml", "platform"],
                    "project_titles": ["Robotics", "ML", "Platform"],
                    "recommended": False,
                },
            ],
        }
        copy_question = {
            "id": "copy_strategy",
            "prompt": "How aggressively should Erga tailor the bullets?",
            "options": [
                {
                    "id": "synthesize",
                    "label": "✨ Evidence-backed tailoring",
                    "description": "Draft only from approved evidence.",
                    "recommended": True,
                },
                {
                    "id": "preserve",
                    "label": "🛡️ Preserve master copy",
                    "description": "Keep established wording.",
                    "recommended": False,
                },
            ],
        }
        delivery_directory = TemporaryDirectory()
        self.addCleanup(delivery_directory.cleanup)
        package_dir = Path(delivery_directory.name) / "synthetic-package"
        artifacts_dir = package_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        pdf_path = artifacts_dir / "Candidate_Resume.pdf"
        pdf_path.write_bytes(b"%PDF-1.7\nsynthetic\n")
        context = _FakePluginContext(
            results=[
                plan_payload(current_question=portfolio, answers=[], status="planning"),
                plan_payload(
                    current_question=copy_question,
                    answers=[{"question_id": "portfolio", "option_id": "balanced"}],
                    status="planning",
                ),
                plan_payload(
                    current_question=None,
                    answers=[
                        {"question_id": "portfolio", "option_id": "balanced"},
                        {"question_id": "copy_strategy", "option_id": "synthesize"},
                    ],
                    status="review",
                ),
                json.dumps(
                    {
                        "plan": {"id": "plan_example", "status": "completed"},
                        "intake": {
                            "package_dir": str(package_dir),
                            "application_id": "app_synthetic",
                            "validation": {
                                "pdf": "artifacts/Candidate_Resume.pdf",
                                "returncode": 0,
                            },
                        },
                    }
                ),
            ]
        )
        delivered: list[tuple[str, str, str | None]] = []
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(sys.modules, {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins}):
            self.router.register(
                context,
                background_runner=lambda callback: callback(),
                plan_delivery=lambda channel_id, message, pdf: delivered.append(
                    (channel_id, message, pdf)
                ),
            )
            first = context.commands["intake-job"]("https://jobs.example.test/engineer")
            first_click = type(
                "Interaction",
                (),
                {
                    "payload": first.buttons[0].payload,
                    "user_id": "42",
                    "channel_id": "123",
                },
            )()
            second = context.discord_button_handlers["erga.plan.action"](first_click)
            synthesize = next(button for button in second.buttons if button.label.startswith("✨"))
            second_click = type(
                "Interaction",
                (),
                {"payload": synthesize.payload, "user_id": "42", "channel_id": "123"},
            )()
            review = context.discord_button_handlers["erga.plan.action"](second_click)
            generate = next(button for button in review.buttons if "Generate" in button.label)
            generate_click = type(
                "Interaction",
                (),
                {"payload": generate.payload, "user_id": "42", "channel_id": "123"},
            )()
            generating = context.discord_button_handlers["erga.plan.action"](generate_click)

        self.assertEqual(
            [button.label for button in first.buttons[:2]], ["⚖️ Balanced", "🎯 Closest match"]
        )
        self.assertIn("Question 1 of 2", first.text)
        self.assertIn("Question 2 of 2", second.text)
        self.assertIn("Review before generation", review.text)
        self.assertIn("Robotics, Platform, Tooling", review.text)
        self.assertIn("Generation started", generating.text)
        self.assertEqual(delivered[0][0], "123")
        self.assertIn("app_synthetic", delivered[0][1])
        self.assertEqual(delivered[0][2], str(pdf_path.resolve()))
        self.assertEqual(
            context.calls[0],
            (
                "mcp__erga_mcp__create_tailoring_plan",
                {"job_url": "https://jobs.example.test/engineer"},
            ),
        )
        self.assertEqual(
            context.calls[-1],
            ("mcp__erga_mcp__execute_tailoring_plan", {"plan_id": "plan_example"}),
        )

    def test_onboarding_and_settings_commands_render_shared_cards_without_components(self) -> None:
        onboarding = {
            "title": "Erga onboarding",
            "summary": "2 of 5 stages configured.",
            "fields": [{"name": "Skill inventory", "value": "2 configured", "inline": False}],
            "actions": [
                {
                    "action_id": "onboarding.skills.set",
                    "label": "Set skills",
                    "instruction": "Use /erga-onboard skills set Python, FastAPI",
                    "style": "secondary",
                },
            ],
            "page": 1,
            "page_count": 1,
        }
        settings = {
            "title": "Erga settings",
            "summary": "Credentials are redacted.",
            "fields": [{"name": "Discord", "value": "Configured", "inline": False}],
            "actions": [],
            "page": 1,
            "page_count": 1,
        }
        context = _FakePluginContext(results=[json.dumps(onboarding), json.dumps(settings)])
        self.router.register(context)

        onboarded = context.commands["erga-onboard"]("")
        configured = context.commands["erga-settings"]("")

        self.assertIn("Erga onboarding", onboarded)
        self.assertIn("2 configured", onboarded)
        self.assertIn("/erga-onboard skills set", onboarded)
        self.assertIn("Erga settings", configured)
        self.assertEqual(
            context.calls,
            [
                ("mcp__erga_mcp__onboarding_status", {}),
                (
                    "mcp__erga_mcp__erga_settings_card",
                    {"host_integration": "hermes"},
                ),
            ],
        )

    def test_mobile_settings_import_button_completes_skill_setup_and_refreshes(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        missing = {
            "title": "Erga settings",
            "summary": "Tap a setup action for anything marked Needs setup.",
            "fields": [
                {
                    "name": "Skills",
                    "value": "Needs setup - import approved skills below.",
                    "inline": False,
                }
            ],
            "actions": [
                {
                    "action_id": "onboarding.skills.import",
                    "label": "Import approved skills",
                    "instruction": "One tap import.",
                    "style": "primary",
                }
            ],
            "page": 1,
            "page_count": 1,
        }
        imported = {"skills": [{"skill": "Python"}], "evidence_created": False}
        configured = {
            **missing,
            "fields": [{"name": "Skills", "value": "1 configured", "inline": False}],
            "actions": [],
        }
        context = _FakePluginContext(
            results=[json.dumps(missing), json.dumps(imported), json.dumps(configured)]
        )
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-settings"]("")
            interaction = type(
                "Interaction",
                (),
                {"payload": rendered.buttons[0].payload, "user_id": "42"},
            )()
            refreshed = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertIsInstance(refreshed, Response)
        self.assertIn("1 configured", refreshed.text)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__erga_settings_card",
                    {"host_integration": "hermes"},
                ),
                (
                    "mcp__erga_mcp__update_skill_inventory",
                    {"operation": "import_approved", "skill": "", "skill_csv": ""},
                ),
                (
                    "mcp__erga_mcp__erga_settings_card",
                    {"host_integration": "hermes"},
                ),
            ],
        )

    def test_git_review_card_uses_opaque_button_tokens_and_explicit_approval(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        card = {
            "title": "Git skill review",
            "summary": "1 review group.",
            "fields": [
                {
                    "name": "React - seeded_and_confirmed",
                    "value": "Repositories: 1 | Confidence: 90%",
                    "inline": False,
                }
            ],
            "actions": [
                {
                    "action_id": "git.scan",
                    "label": "Scan Git projects",
                    "instruction": "Scan configured onboarding roots.",
                    "style": "primary",
                },
                {
                    "action_id": "git.group.approve:react",
                    "label": "Approve React",
                    "instruction": "Explicitly approve corroborated Git candidates.",
                    "style": "primary",
                },
            ],
            "page": 1,
            "page_count": 1,
        }
        approved = {"approved_evidence_count": 1, "resume_changed": False}
        context = _FakePluginContext(
            results=[json.dumps(card), json.dumps(approved), json.dumps(card)]
        )
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-git"]("")
            self.assertIsInstance(rendered, Response)
            self.assertEqual(len(rendered.buttons), 2)
            button = next(item for item in rendered.buttons if item.label == "Approve React")
            self.assertEqual(button.action_id, "erga.card.action")
            self.assertNotIn("react", button.payload)
            interaction = type(
                "Interaction",
                (),
                {"payload": button.payload, "user_id": "42"},
            )()
            refreshed = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertIsInstance(refreshed, Response)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__git_skill_review_card",
                    {"page": 1, "page_size": 5, "source_filter": "", "seed_csv": ""},
                ),
                (
                    "mcp__erga_mcp__review_git_skill_group",
                    {"operation": "approve", "skill": "react"},
                ),
                (
                    "mcp__erga_mcp__git_skill_review_card",
                    {"page": 1, "page_size": 5, "source_filter": "", "seed_csv": ""},
                ),
            ],
        )

    def test_git_scan_button_runs_the_unified_pipeline_and_refreshes_the_ui(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        card = {
            "title": "Erga Git",
            "summary": "Ready to scan configured roots.",
            "fields": [{"name": "Results", "value": "No groups yet.", "inline": False}],
            "actions": [
                {
                    "action_id": "git.scan",
                    "label": "Scan Git projects",
                    "instruction": "Scan configured onboarding roots.",
                    "style": "primary",
                }
            ],
            "page": 1,
            "page_count": 1,
        }
        scanned = {
            "repositories_scanned": 2,
            "candidates_created": 3,
            "observations_created": 3,
            "research_drafts": 2,
            "auto_approved": False,
            "drafts": [],
            "card": {
                **card,
                "summary": "Scan complete: 2 repositories and 3 candidates.",
            },
        }
        context = _FakePluginContext(results=[json.dumps(card), json.dumps(scanned)])
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-git"]("")
            interaction = type(
                "Interaction",
                (),
                {"payload": rendered.buttons[0].payload, "user_id": "42"},
            )()
            refreshed = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertIsInstance(refreshed, Response)
        self.assertIn("Scan complete: 2 repositories", refreshed.text)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__git_skill_review_card",
                    {"page": 1, "page_size": 5, "source_filter": "", "seed_csv": ""},
                ),
                ("mcp__erga_mcp__research_git_worktrees", {"roots": []}),
            ],
        )

    def test_git_projects_command_searches_and_paginates_the_shared_catalogue(self) -> None:
        card = {
            "title": "Project catalogue",
            "summary": "1 matching: 0 résumé-eligible and 1 awaiting approved evidence.",
            "fields": [
                {
                    "name": "Python Service",
                    "value": (
                        "Repository: [example/python-service]"
                        "(https://github.com/example/python-service)\n"
                        "Technologies/tags: Python, FastAPI\n"
                        "Git activity: 2026-08-10\n"
                        "Evidence: 0 approved records · 0 supported bullets\n"
                        "Résumé: Needs approved evidence · Source: GitHub discovery"
                    ),
                    "inline": False,
                }
            ],
            "actions": [],
            "page": 2,
            "page_count": 3,
        }
        context = _FakePluginContext(result=json.dumps(card))
        self.router.register(context)

        rendered = context.commands["erga-git"]("projects python page 2")

        self.assertIn("Project catalogue", rendered)
        self.assertIn("example/python-service", rendered)
        self.assertIn("Needs approved evidence", rendered)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__project_catalogue",
                    {"page": 2, "page_size": 4, "query": "python"},
                )
            ],
        )

    def test_project_catalogue_refresh_button_updates_cache_without_approving_evidence(
        self,
    ) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        card = {
            "title": "Project catalogue",
            "summary": "2 total: 1 résumé-eligible and 1 awaiting approved evidence.",
            "fields": [
                {
                    "name": "Approved Project",
                    "value": "Résumé: Eligible · Source: Approved inventory",
                    "inline": False,
                }
            ],
            "actions": [
                {
                    "action_id": "project.catalogue.refresh",
                    "label": "Refresh GitHub projects",
                    "instruction": "Refresh private metadata.",
                    "style": "secondary",
                }
            ],
            "page": 1,
            "page_count": 1,
        }
        refreshed = {
            **card,
            "summary": "3 total: 1 résumé-eligible and 2 awaiting approved evidence.",
            "github_projects_refreshed": 3,
            "evidence_created": False,
            "resume_changed": False,
        }
        context = _FakePluginContext(results=[json.dumps(card), json.dumps(refreshed)])
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-git"]("projects")
            interaction = type(
                "Interaction",
                (),
                {"payload": rendered.buttons[0].payload, "user_id": "42"},
            )()
            updated = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertIsInstance(updated, Response)
        self.assertIn("3 total", updated.text)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__project_catalogue",
                    {"page": 1, "page_size": 4, "query": ""},
                ),
                (
                    "mcp__erga_mcp__refresh_project_catalogue",
                    {"page": 1, "page_size": 4, "query": ""},
                ),
            ],
        )

    def test_project_catalogue_button_replies_always_fit_discord_content_limit(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        def catalogue_card(page: int, action: str, label: str) -> dict[str, Any]:
            return {
                "title": "Project catalogue",
                "summary": "12 total: 6 résumé-eligible, 6 awaiting approved evidence.",
                "fields": [
                    {
                        "name": f"Project {index}",
                        "value": (
                            f"Repository: [example/project-{index}]"
                            f"(https://github.com/example/project-{index})\n"
                            f"Technologies/tags: {'python, fastapi, testing, ' * 12}\n"
                            "Evidence: 2 approved records · 2 supported bullets\n"
                            "Résumé: Eligible · Source: Approved inventory"
                        ),
                        "inline": False,
                    }
                    for index in range(6)
                ],
                "actions": [
                    {
                        "action_id": action,
                        "label": label,
                        "instruction": "Navigate without losing the current catalogue state.",
                        "style": "secondary",
                    }
                ],
                "page": page,
                "page_count": 2,
            }

        first = catalogue_card(1, "project.catalogue.next", "Next")
        second = catalogue_card(2, "project.catalogue.previous", "Previous")
        context = _FakePluginContext(results=[json.dumps(first), json.dumps(second)])
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-git"]("projects")
            interaction = type(
                "Interaction",
                (),
                {"payload": rendered.buttons[0].payload, "user_id": "42"},
            )()
            next_page = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertLessEqual(len(rendered.text), 2_000)
        self.assertLessEqual(len(next_page.text), 2_000)
        self.assertIn("Shortened to fit Discord", rendered.text)
        self.assertIn("Shortened to fit Discord", next_page.text)
        self.assertNotIn("Available actions", rendered.text)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__project_catalogue",
                    {"page": 1, "page_size": 4, "query": ""},
                ),
                (
                    "mcp__erga_mcp__project_catalogue",
                    {"page": 2, "page_size": 4, "query": ""},
                ),
            ],
        )

    def test_git_setup_button_adds_detected_root_and_continues_scan(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        setup_card = {
            "title": "Git skill review",
            "summary": "Set up a root before scanning.",
            "fields": [{"name": "Results", "value": "No groups yet.", "inline": False}],
            "actions": [
                {
                    "action_id": "onboarding.roots.use_detected",
                    "label": "Use detected folder and scan",
                    "instruction": "Configure and continue.",
                    "style": "primary",
                }
            ],
            "page": 1,
            "page_count": 1,
        }
        updated = {"roots": ["/synthetic/projects"]}
        scanned_card = {
            **setup_card,
            "summary": "Scan complete: 2 repositories.",
            "actions": [],
        }
        scanned = {
            "repositories_scanned": 2,
            "scan_started": True,
            "setup_required": False,
            "card": scanned_card,
        }
        context = _FakePluginContext(
            results=[json.dumps(setup_card), json.dumps(updated), json.dumps(scanned)]
        )
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            rendered = context.commands["erga-git"]("")
            interaction = type(
                "Interaction",
                (),
                {"payload": rendered.buttons[0].payload, "user_id": "42"},
            )()
            refreshed = context.discord_button_handlers["erga.card.action"](interaction)

        self.assertIsInstance(refreshed, Response)
        self.assertIn("Scan complete: 2 repositories", refreshed.text)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__git_skill_review_card",
                    {"page": 1, "page_size": 5, "source_filter": "", "seed_csv": ""},
                ),
                (
                    "mcp__erga_mcp__manage_portfolio_roots",
                    {"operation": "add_detected", "root": "", "roots": None},
                ),
                ("mcp__erga_mcp__research_git_worktrees", {"roots": []}),
            ],
        )

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

    def test_job_words_do_not_turn_repository_or_research_links_into_postings(self) -> None:
        messages = (
            "Review this software engineer project https://github.com/example/project.git",
            "Research this internship thread https://reddit.com/r/csMajors/comments/example/",
            "Job prep docs https://modelcontextprotocol.io/docs/learn/server-concepts",
            "Schedule the interview https://calendly.com/example/recruiter-screen",
        )

        for message in messages:
            with self.subTest(message=message):
                self.assertIsNone(self.router.extract_job_url(message))

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
            [("mcp__erga_mcp__intake_job_url", {"job_url": url})],
        )
        assert injected is not None
        assert repeated is not None
        self.assertIn("called before this model turn", injected["context"])
        self.assertIn('"package_dir":"/tmp/example"', injected["context"])
        self.assertIn('"package_dir":"/tmp/example"', repeated["context"])
        self.assertIn("Do not call a browser", injected["context"])
        self.assertIn("whether tailoring made", injected["context"])
        self.assertIn("host-model evidence synthesis", injected["context"])
        self.assertIn("selected projects and their matched role terms", injected["context"])

    def test_records_provider_usage_for_the_application_intaked_in_the_same_turn(self) -> None:
        context = _FakePluginContext(
            result='{"package_dir":"/tmp/example","application_id":"app_example"}'
        )
        self.router.register(context)
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"

        context.hooks["pre_llm_call"](
            user_message=url,
            session_id="session-1",
            turn_id="turn-1",
            platform="discord",
        )
        context.hooks["post_api_request"](
            session_id="session-1",
            turn_id="turn-1",
            api_request_id="request-1",
            model="test-model",
            usage={"input_tokens": 12, "output_tokens": 3},
        )
        context.hooks["post_api_request"](
            session_id="session-1",
            turn_id="turn-1",
            api_request_id="request-1",
            model="test-model",
            usage={"input_tokens": 12, "output_tokens": 3},
        )
        context.hooks["post_llm_call"](session_id="session-1", turn_id="turn-1")

        self.assertEqual(
            context.calls,
            [
                ("mcp__erga_mcp__intake_job_url", {"job_url": url}),
                (
                    "mcp__erga_mcp__record_token_usage",
                    {
                        "application_id": "app_example",
                        "operation": "hermes_llm_call",
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "model": "test-model",
                    },
                ),
            ],
        )

    def test_records_usage_after_an_empty_callback_with_the_same_request_id(self) -> None:
        context = _FakePluginContext(
            result='{"package_dir":"/tmp/example","application_id":"app_example"}'
        )
        self.router.register(context)
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        context.hooks["pre_llm_call"](
            user_message=url,
            session_id="session-1",
            turn_id="turn-1",
            platform="discord",
        )
        for usage in (
            {"input_tokens": 0, "output_tokens": 0},
            {"input_tokens": 3, "output_tokens": 4},
        ):
            context.hooks["post_api_request"](
                session_id="session-1",
                turn_id="turn-1",
                api_request_id="request-1",
                model="test-model",
                usage=usage,
            )

        self.assertEqual(
            context.calls[-1],
            (
                "mcp__erga_mcp__record_token_usage",
                {
                    "application_id": "app_example",
                    "operation": "hermes_llm_call",
                    "input_tokens": 3,
                    "output_tokens": 4,
                    "model": "test-model",
                },
            ),
        )

    def test_message_reply_attaches_a_successfully_validated_resume_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "application"
            artifacts_dir = package_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            pdf_path = artifacts_dir / "Candidate_Resume.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\nsynthetic\n")
            context = _FakePluginContext(
                result=json.dumps(
                    {
                        "package_dir": str(package_dir),
                        "validation": {
                            "returncode": 0,
                            "pdf": str(pdf_path),
                            "skipped": None,
                        },
                    }
                )
            )
            self.router.register(context)
            url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"

            injected = context.hooks["pre_llm_call"](
                user_message=url,
                session_id="discord-session",
                turn_id="attachment-turn",
                platform="discord",
            )
            transformed = context.hooks["transform_llm_output"](
                response_text="Intake complete. Your resume is attached.",
                session_id="discord-session",
                model="test-model",
                platform="discord",
            )
            repeated = context.hooks["transform_llm_output"](
                response_text="Unrelated later response.",
                session_id="discord-session",
                model="test-model",
                platform="discord",
            )

            assert injected is not None
            assert transformed is not None
            self.assertIn("router will add the native message attachment", injected["context"])
            self.assertIn('MEDIA:"', transformed)
            self.assertIn(str(pdf_path.resolve()), transformed)
            self.assertIn("[[as_document]]", transformed)
            self.assertIsNone(repeated)

    def test_attachment_unwraps_the_live_hermes_mcp_result_envelope(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "application"
            artifacts_dir = package_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            pdf_path = artifacts_dir / "Candidate_Resume.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\nsynthetic\n")
            payload = {
                "package_dir": str(package_dir),
                "validation": {"returncode": 0, "pdf": str(pdf_path)},
            }
            context = _FakePluginContext(
                result=json.dumps(
                    {
                        "result": json.dumps(payload),
                        "structuredContent": payload,
                    }
                )
            )
            self.router.register(context)

            context.hooks["pre_llm_call"](
                user_message="https://boards.greenhouse.io/example/jobs/12345",
                session_id="enveloped-session",
                turn_id="enveloped-turn",
                platform="discord",
            )
            transformed = context.hooks["transform_llm_output"](
                response_text="Intake complete.",
                session_id="enveloped-session",
                platform="discord",
            )

            assert transformed is not None
            self.assertIn("[[as_document]]", transformed)
            self.assertIn(f'MEDIA:"{pdf_path.resolve()}"', transformed)

    def test_router_runs_the_unified_role_aware_research_pipeline(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "application"
            research_dir = package_dir / "research"
            research_dir.mkdir(parents=True)
            research_note = research_dir / "role-research.md"
            research_note.write_text("# Example Co — Software Intern research\n", encoding="utf-8")
            intake_result = json.dumps(
                {
                    "package_dir": str(package_dir),
                    "research_note": str(research_note),
                    "validation": {"returncode": 1, "pdf": None},
                }
            )
            context = _FakePluginContext(
                results=[
                    intake_result,
                    json.dumps(
                        {
                            "research_note": "/tmp/discovery-research.md",
                            "candidates_reviewed": 35,
                            "sources_retained": 9,
                        }
                    ),
                ]
            )
            self.router.register(context)
            url = "https://boards.greenhouse.io/example/jobs/12345"

            injected = context.hooks["pre_llm_call"](
                user_message=url,
                session_id="research-session",
                turn_id="research-turn",
                platform="discord",
            )

            assert injected is not None
            self.assertEqual(context.calls[0][0], "mcp__erga_mcp__intake_job_url")
            self.assertEqual(
                context.calls[1],
                ("mcp__erga_mcp__discover_job_research", {"job_url": url}),
            )
            self.assertIn("candidates_reviewed", injected["context"])

    def test_attachment_requires_successful_in_package_pdf_validation(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "application"
            artifacts_dir = package_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            outside_pdf = Path(directory) / "outside.pdf"
            outside_pdf.write_bytes(b"%PDF-1.7\nsynthetic\n")
            scenarios = [
                {"returncode": 1, "pdf": str(outside_pdf)},
                {"returncode": 0, "pdf": str(outside_pdf)},
                {"returncode": 0, "pdf": str(artifacts_dir / "missing.pdf")},
            ]

            for index, validation in enumerate(scenarios):
                with self.subTest(validation=validation):
                    context = _FakePluginContext(
                        result=json.dumps(
                            {"package_dir": str(package_dir), "validation": validation}
                        )
                    )
                    self.router.register(context)
                    context.hooks["pre_llm_call"](
                        user_message=(
                            f"https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-{index:012d}"
                        ),
                        session_id=f"invalid-session-{index}",
                        turn_id=f"invalid-turn-{index}",
                        platform="discord",
                    )

                    transformed = context.hooks["transform_llm_output"](
                        response_text="Intake result.",
                        session_id=f"invalid-session-{index}",
                        platform="discord",
                    )

                    self.assertIsNone(transformed)

    def test_planned_intake_finds_and_requires_its_validated_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "package"
            artifacts_dir = package_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            pdf_path = artifacts_dir / "Candidate_Resume.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\nsynthetic\n")
            result = json.dumps(
                {
                    "structuredContent": {
                        "plan": {"id": "plan_example", "status": "completed"},
                        "intake": {
                            "package_dir": str(package_dir),
                            "application_id": "app_synthetic",
                            "validation": {
                                "returncode": 0,
                                "pdf": "artifacts/Candidate_Resume.pdf",
                            },
                        },
                    }
                }
            )

            message, delivered_pdf = self.router._planned_resume_delivery(result)

            self.assertIn("generated, validated, and attached", message)
            self.assertIn("app_synthetic", message)
            self.assertEqual(delivered_pdf, str(pdf_path.resolve()))

            missing_pdf_result = json.dumps(
                {
                    "plan": {"id": "plan_example", "status": "completed"},
                    "intake": {
                        "package_dir": str(package_dir),
                        "application_id": "app_synthetic",
                        "validation": {"returncode": 0, "pdf": None},
                    },
                }
            )
            missing_message, missing_pdf = self.router._planned_resume_delivery(missing_pdf_result)

            self.assertIn("no validated PDF attachment", missing_message)
            self.assertNotIn("✅", missing_message)
            self.assertIsNone(missing_pdf)

    def test_plan_pdf_delivery_forces_a_document_attachment(self) -> None:
        completed = type("Completed", (), {"returncode": 0})()

        with (
            patch.object(self.router.shutil, "which", return_value="/usr/local/bin/hermes"),
            patch.object(self.router.subprocess, "run", return_value=completed) as run,
        ):
            self.router._deliver_discord_plan_result(
                "123456",
                "Résumé ready",
                "/tmp/Candidate Resume.pdf",
            )

        body = run.call_args.kwargs["input"]
        self.assertIn("[[as_document]]", body)
        self.assertIn('MEDIA:"/tmp/Candidate Resume.pdf"', body)

    def test_erga_orbit_dispatches_token_free_renderer_and_attaches_valid_png(self) -> None:
        with TemporaryDirectory() as directory:
            orbit_dir = Path(directory) / "orbit"
            orbit_dir.mkdir()
            image_path = orbit_dir / "erga-orbit-summer-2027.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
            context = _FakePluginContext(
                result=json.dumps(
                    {
                        "image_path": str(image_path),
                        "mime_type": "image/png",
                        "message": "**Erga Orbit · Summer 2027**\n12 tracked roles",
                        "model_api_used": False,
                    }
                )
            )
            self.router.register(context)

            response = context.commands["erga-orbit"]("Summer 2027")

        self.assertEqual(
            context.calls,
            [("mcp__erga_mcp__application_orbit", {"cycle": "Summer 2027"})],
        )
        self.assertIn("12 tracked roles", response)
        self.assertIn(f'MEDIA:"{image_path.resolve()}"', response)
        self.assertNotIn("[[as_document]]", response)

    def test_plan_delivery_retries_transient_attachment_failures(self) -> None:
        attempts: list[tuple[str, str, str | None]] = []

        def flaky_delivery(channel_id: str, message: str, pdf: str | None) -> None:
            attempts.append((channel_id, message, pdf))
            if len(attempts) < 3:
                raise RuntimeError("temporary Discord failure")

        self.router._deliver_plan_with_retries(
            flaky_delivery,
            channel_id="123456",
            message="Résumé ready",
            pdf="/tmp/Candidate Resume.pdf",
            sleep=lambda _: None,
        )

        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(item[2] == "/tmp/Candidate Resume.pdf" for item in attempts))

    def test_local_cli_does_not_emit_a_media_directive(self) -> None:
        with TemporaryDirectory() as directory:
            package_dir = Path(directory) / "application"
            artifacts_dir = package_dir / "artifacts"
            artifacts_dir.mkdir(parents=True)
            pdf_path = artifacts_dir / "Candidate_Resume.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\nsynthetic\n")
            context = _FakePluginContext(
                result=json.dumps(
                    {
                        "package_dir": str(package_dir),
                        "validation": {"returncode": 0, "pdf": str(pdf_path)},
                    }
                )
            )
            self.router.register(context)
            context.hooks["pre_llm_call"](
                user_message="https://boards.greenhouse.io/example/jobs/12345",
                session_id="cli-session",
                turn_id="cli-turn",
                platform="cli",
            )

            transformed = context.hooks["transform_llm_output"](
                response_text="Intake complete.",
                session_id="cli-session",
                platform="cli",
            )

            self.assertIsNone(transformed)

    def test_unified_git_scan_dispatches_explicit_roots_with_provenance_only(self) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "repositories_scanned": 1,
                    "observations_created": 2,
                    "research_drafts": 1,
                    "auto_approved": False,
                    "drafts": [
                        {
                            "repo_path": "/tmp/projects/example",
                            "work_types": ["UI", "implementation"],
                            "source_commit_shas": ["abc123"],
                            "source_files": ["src/routes.py"],
                            "diff_hashes": ["d" * 64],
                            "needs_review": True,
                            "auto_approved": False,
                        }
                    ],
                }
            )
        )
        self.router.register(context)

        response = context.commands["erga-git"]("scan /tmp/projects")

        self.assertEqual(
            context.calls,
            [("mcp__erga_mcp__research_git_worktrees", {"roots": ["/tmp/projects"]})],
        )
        self.assertIn("1 repositories scanned", response)
        self.assertIn("2 observations created", response)
        self.assertIn("1 review draft", response)
        self.assertIn("Example — work found", response)
        self.assertIn("user-interface work", response)
        self.assertIn("general implementation work", response)
        self.assertIn("Needs your review", response)
        self.assertNotIn("abc123", response)
        self.assertNotIn("src/routes.py", response)
        self.assertNotIn("d" * 64, response)

    def test_unified_git_scan_uses_configured_roots_and_removes_redundant_command(self) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "repositories_scanned": 0,
                    "observations_created": 0,
                    "research_drafts": 0,
                    "auto_approved": False,
                    "drafts": [],
                }
            )
        )
        self.router.register(context)
        response = context.commands["erga-git"]("scan")

        self.assertEqual(context.calls, [("mcp__erga_mcp__research_git_worktrees", {"roots": []})])
        self.assertIn("Erga Git research complete", response)
        self.assertNotIn("erga-git-research", context.commands)

    def test_erga_review_renders_one_manual_draft_without_approving_evidence(self) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "draft": {
                        "id": "gitdraft_manual",
                        "title": "Personal finance tracker",
                        "description": "Built an offline budgeting application.",
                        "source": "manual",
                        "review_status": "pending",
                        "needs_review": True,
                    },
                    "position": 1,
                    "total": 2,
                    "evidence_approved": False,
                    "resume_changed": False,
                }
            )
        )
        self.router.register(context)

        response = context.commands["erga-review"]("")

        self.assertEqual(
            context.calls,
            [("mcp__erga_mcp__review_git_drafts", {"action": "show", "draft_id": None})],
        )
        self.assertIn("Personal finance tracker", response)
        self.assertIn("Source: manual", response)
        self.assertIn("Draft 1 of 2", response)
        self.assertIn("/erga-review next gitdraft_manual", response)
        self.assertIn("No evidence was approved and no résumé was changed", response)

    def test_erga_review_returns_discord_buttons_when_the_host_supports_them(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        context = _FakePluginContext(
            result=json.dumps(
                {
                    "draft": {
                        "id": "gitdraft_manual",
                        "title": "Personal finance tracker",
                        "description": "Built an offline budgeting application.",
                        "source": "manual",
                        "review_status": "pending",
                        "needs_review": True,
                    },
                    "position": 1,
                    "total": 2,
                    "evidence_approved": False,
                    "resume_changed": False,
                }
            )
        )
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            response = context.commands["erga-review"]("")

        self.assertIsInstance(response, Response)
        self.assertEqual(
            [button.action_id for button in response.buttons],
            [
                "erga.review.back",
                "erga.review.skip",
                "erga.review.save",
                "erga.review.next",
            ],
        )
        self.assertEqual(response.buttons[-1].payload, "gitdraft_manual")
        self.assertEqual(
            set(context.discord_button_handlers),
            {
                "erga.card.action",
                "erga.tracker.page",
                "erga.plan.action",
                "erga.review.back",
                "erga.review.skip",
                "erga.review.save",
                "erga.review.next",
            },
        )

    def test_erga_review_parses_edit_arguments_without_treating_them_as_source_content(
        self,
    ) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "draft": {
                        "id": "gitdraft_manual",
                        "title": "Edited title",
                        "description": "Edited description.",
                        "source": "manual",
                        "review_status": "pending",
                        "needs_review": True,
                    },
                    "position": 1,
                    "total": 1,
                    "evidence_approved": False,
                    "resume_changed": False,
                }
            )
        )
        self.router.register(context)

        response = context.commands["erga-review"](
            'edit gitdraft_manual --title "Edited title" --description "Edited description."'
        )

        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__review_git_drafts",
                    {
                        "action": "edit",
                        "draft_id": "gitdraft_manual",
                        "title": "Edited title",
                        "description": "Edited description.",
                    },
                )
            ],
        )
        self.assertIn("Edited title", response)
        self.assertNotIn("gitdraft_manual --title", response)

    def test_retries_only_transient_mcp_startup_errors(self) -> None:
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        tool_name = "mcp__erga_mcp__intake_job_url"
        context = _FakePluginContext(
            results=[
                json.dumps({"error": f"Unknown tool: {tool_name}"}),
                json.dumps({"error": "MCP server 'erga-mcp' is not connected"}),
                '{"package_dir":"/tmp/ready"}',
            ]
        )
        clock = _FakeClock()
        env = {
            "ERGA_MCP_READY_TIMEOUT_SECONDS": "2",
            "ERGA_MCP_READY_RETRY_SECONDS": "0.25",
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
        tool_name = "mcp__erga_mcp__intake_job_url"
        non_readiness_errors = [
            "Unknown tool: browser",
            "MCP server 'erga-mcp' transport is down; reconnect requested",
            "MCP server 'erga-mcp' is unreachable",
            "job URL resolved to a private address",
        ]

        for error in non_readiness_errors:
            with self.subTest(error=error):
                self.assertFalse(
                    self.router._is_retryable_startup_error(error, tool_name=tool_name)
                )

    def test_readiness_retry_is_bounded_by_configured_timeout(self) -> None:
        url = "https://jobs.ashbyhq.com/example/00000000-0000-0000-0000-000000000000"
        tool_name = "mcp__erga_mcp__intake_job_url"
        startup_error = json.dumps({"error": f"Unknown tool: {tool_name}"})
        context = _FakePluginContext(result=startup_error)
        clock = _FakeClock()
        env = {
            "ERGA_MCP_READY_TIMEOUT_SECONDS": "0.5",
            "ERGA_MCP_READY_RETRY_SECONDS": "0.2",
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
                "ERGA_MCP_READY_TIMEOUT_SECONDS": "999",
                "ERGA_MCP_READY_RETRY_SECONDS": "999",
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

    def test_explicit_slash_command_starts_the_review_only_plan(self) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "id": "plan_explicit",
                    "job_url": "https://jobs.lever.co/example/00000000",
                    "company": "Example",
                    "role": "Engineer",
                    "questions": [
                        {
                            "id": "copy_strategy",
                            "prompt": "How should Erga tailor?",
                            "options": [
                                {
                                    "id": "preserve",
                                    "label": "🛡️ Preserve master copy",
                                    "description": "Keep approved copy.",
                                }
                            ],
                        }
                    ],
                    "answers": [],
                    "current_question": {
                        "id": "copy_strategy",
                        "prompt": "How should Erga tailor?",
                        "options": [
                            {
                                "id": "preserve",
                                "label": "🛡️ Preserve master copy",
                                "description": "Keep approved copy.",
                            }
                        ],
                    },
                    "status": "planning",
                    "catalogue_candidate_count": 0,
                }
            )
        )
        self.router.register(context)
        url = "https://jobs.lever.co/example/00000000-0000-0000-0000-000000000000"

        result = context.commands["intake-job"](url)

        self.assertIn("Question 1 of 1", result)
        self.assertIn("no application, résumé, or tracker entry", result)
        self.assertEqual(len(context.calls), 1)
        self.assertEqual(context.calls[0][0], "mcp__erga_mcp__create_tailoring_plan")
        self.assertEqual(context.calls[0][1], {"job_url": url})

    def test_monitor_command_installs_scripts_and_delivers_cron_to_origin(self) -> None:
        context = _FakePluginContext(
            results=[
                json.dumps(
                    {
                        "mail_script": "erga-mcp-mail.py",
                        "history_script": "erga-mcp-history.py",
                    }
                ),
                json.dumps({"jobs": [{"name": "erga-history-digest"}]}),
                json.dumps({"success": True, "name": "erga-mail-monitor"}),
            ]
        )
        self.router.register(context)

        result = json.loads(context.commands["setup-erga-monitor"]("14"))

        self.assertEqual(result["delivery"], "origin")
        self.assertEqual(result["history_days"], 14)
        self.assertEqual(result["created"], 1)
        self.assertEqual(
            context.calls[0],
            (
                "mcp__erga_mcp__install_mail_monitor_scripts",
                {"history_days": 14, "replace": True},
            ),
        )
        self.assertEqual(context.calls[1], ("cronjob", {"action": "list"}))
        create_call = context.calls[2]
        self.assertEqual(create_call[0], "cronjob")
        self.assertEqual(create_call[1]["schedule"], "*/15 * * * *")
        self.assertTrue(create_call[1]["no_agent"])
        self.assertNotIn("deliver", create_call[1])

    def test_monitor_command_falls_back_when_platform_hides_cron_toolset(self) -> None:
        context = _FakePluginContext(
            result="Unknown tool: cronjob",
            results=[
                json.dumps(
                    {
                        "mail_script": "erga-mcp-mail.py",
                        "history_script": "erga-mcp-history.py",
                    }
                )
            ],
        )
        direct_results = [
            json.dumps({"success": True, "jobs": []}),
            json.dumps({"success": True, "name": "erga-mail-monitor"}),
            json.dumps({"success": True, "name": "erga-history-digest"}),
        ]
        self.router.register(context)

        with patch.object(
            self.router, "_direct_cron_dispatch", side_effect=direct_results
        ) as direct:
            result = json.loads(context.commands["setup-erga-monitor"]("7"))

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["delivery"], "origin")
        self.assertEqual(direct.call_count, 3)
        self.assertEqual(direct.call_args_list[0].args[0], {"action": "list"})
        self.assertNotIn("deliver", direct.call_args_list[1].args[0])

    def test_monitor_files_are_mirrored_into_the_active_hermes_profile(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "default" / "scripts"
            target_home = root / "profiles" / "coder"
            source.mkdir(parents=True)
            files = {
                "erga-mcp-monitor.json": '{"history_days": 7}\n',
                "erga-mcp-mail.py": "print('mail')\n",
                "erga-mcp-history.py": "print('history')\n",
            }
            for name, content in files.items():
                (source / name).write_text(content, encoding="utf-8")
            payload = {
                "settings": str(source / "erga-mcp-monitor.json"),
                "mail_script": "erga-mcp-mail.py",
                "history_script": "erga-mcp-history.py",
            }

            with patch.object(self.router, "_active_hermes_home", return_value=target_home):
                self.router._copy_monitor_files_to_active_profile(payload)

            for name, content in files.items():
                self.assertEqual(
                    (target_home / "scripts" / name).read_text(encoding="utf-8"),
                    content,
                )

    def test_export_command_returns_a_native_validated_zip_attachment(self) -> None:
        with TemporaryDirectory() as directory:
            export_root = Path(directory) / "exports"
            export_root.mkdir()
            archive = export_root / "recruiting.zip"
            archive.write_bytes(b"PK\x03\x04synthetic")
            context = _FakePluginContext(
                result=json.dumps({"archive": str(archive), "export_root": str(export_root)})
            )
            self.router.register(context)

            result = context.commands["export-erga"]("")

            self.assertIn("[[as_document]]", result)
            self.assertIn(f'MEDIA:"{archive.resolve()}"', result)
            self.assertEqual(
                context.calls,
                [("mcp__erga_mcp__export_data", {})],
            )

    def test_mail_sync_command_runs_the_configured_recruiting_mail_sync(self) -> None:
        message = (
            "📬 Erga mail sync complete\n\nFetched 1 message; 1 new recruiting event recorded."
        )
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "structuredContent": {
                        "provider": "zoho",
                        "fetched": 1,
                        "created": 1,
                        "message": message,
                    }
                }
            )
        )
        self.router.register(context)

        result = context.commands["erga-mail-sync"]("")

        self.assertEqual(result, message)
        self.assertEqual(context.calls, [("mcp__erga_mcp__sync_recruiting_mail", {})])
        self.assertEqual(
            context.commands["erga-mail-sync"]("now"),
            "Usage: /erga-mail-sync",
        )

    def test_erga_research_command_dispatches_and_renders_a_saved_result(self) -> None:
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "company": "Example",
                    "role": "Software Engineer Intern",
                    "research_note": "/tmp/example/research/discovery-research.md",
                    "sources_scraped": 3,
                    "outreach_leads": 1,
                    "messages_sent": 0,
                    "community_sources_unverified": True,
                }
            )
        )
        self.router.register(context)

        self.assertEqual(
            context.commands["erga-research"](""),
            "Usage: /erga-research <company or role>",
        )
        result = context.commands["erga-research"]("example intern")

        self.assertIn("Research saved for Example — Software Engineer Intern", result)
        self.assertIn("3 sources scraped; 1 public outreach lead.", result)
        self.assertIn("Community reports are unverified. No messages were sent.", result)
        self.assertEqual(
            context.calls,
            [("mcp__erga_mcp__discover_job_research", {"query": "example intern"})],
        )

    def test_tracker_command_returns_the_cross_platform_obsidian_card(self) -> None:
        message = (
            "### Erga application tracker\n\n"
            "**1 roles** · 1 applied\n\n"
            "**Fall 2026**\n"
            "📬 **Example Co** — Software Engineer Intern"
        )
        context = _FakePluginContext(
            result=json.dumps(
                {
                    "structuredContent": {
                        "enabled": True,
                        "summary": {"applied": 1},
                        "message": message,
                    }
                }
            )
        )
        self.router.register(context)

        result = context.commands["erga-tracker"]("")

        self.assertEqual(result, message)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                )
            ],
        )
        self.assertEqual(context.commands["erga-tracker"]("all"), message)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                ),
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                ),
            ],
        )

    def test_tracker_command_paginates_with_discord_buttons(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        def result(page: int) -> str:
            return json.dumps(
                {
                    "structuredContent": {
                        "enabled": True,
                        "summary": {"applied": 13},
                        "page": page,
                        "page_count": 3,
                        "message": f"Page {page} of 3",
                    }
                }
            )

        context = _FakePluginContext(results=[result(1), result(2)])
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(context)
            first = context.commands["erga-tracker"]("all")
            interaction = type(
                "Interaction",
                (),
                {"payload": json.dumps({"query": "", "page": 2})},
            )()
            second = context.discord_button_handlers["erga.tracker.page"](interaction)

        self.assertIsInstance(first, Response)
        self.assertEqual(first.text, "Page 1 of 3")
        self.assertEqual(
            [button.label for button in first.buttons],
            ["Page 1/3", "Next", "Orbit"],
        )
        self.assertIsInstance(second, Response)
        self.assertEqual(second.text, "Page 2 of 3")
        self.assertEqual(
            [button.label for button in second.buttons],
            ["Previous", "Page 2/3", "Next", "Orbit"],
        )
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                ),
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 2, "page_size": 6},
                ),
            ],
        )

    def test_tracker_oa_row_opens_a_research_navigator_with_links_and_actions(self) -> None:
        class Button:
            def __init__(self, **kwargs: Any) -> None:
                self.__dict__.update(kwargs)

        class Response:
            def __init__(self, *, text: str, buttons: tuple[Any, ...]) -> None:
                self.text = text
                self.buttons = buttons

        tracker_result = json.dumps(
            {
                "structuredContent": {
                    "enabled": True,
                    "summary": {"assessment": 1},
                    "page": 1,
                    "page_count": 1,
                    "entries": [
                        {
                            "company": "Example",
                            "role": "Software Engineer Intern",
                            "source_url": "https://jobs.example.test/role",
                            "research": {"eligible": True, "stage": "oa"},
                        }
                    ],
                    "message": "Example — Software Engineer Intern · OA",
                }
            }
        )
        navigator_result = json.dumps(
            {
                "structuredContent": {
                    "company": "Example",
                    "role": "Software Engineer Intern",
                    "job_url": "https://jobs.example.test/role",
                    "stage": "oa",
                    "research_query": "Example Software Engineer Intern",
                    "card": {
                        "title": "OA research · Example",
                        "summary": "2 saved notes · 2 useful links.",
                        "fields": [
                            {
                                "name": "Open on mobile",
                                "value": (
                                    "[Official posting](https://jobs.example.test/role)\n"
                                    "[Community report (unverified)](https://reddit.com/r/test)"
                                ),
                                "inline": False,
                            }
                        ],
                        "actions": [
                            {
                                "action_id": "research.refresh",
                                "label": "Refresh sources",
                                "instruction": "Run bounded public research.",
                                "style": "primary",
                            },
                            {
                                "action_id": "research.brief",
                                "label": "Create OA brief",
                                "instruction": "Create the local OA checklist.",
                                "style": "secondary",
                            },
                            {
                                "action_id": "research.back",
                                "label": "Back to tracker",
                                "instruction": "Return to the tracker.",
                                "style": "secondary",
                            },
                        ],
                        "page": 1,
                        "page_count": 1,
                        "footer": "Community sources are unverified.",
                    },
                }
            }
        )
        context = _FakePluginContext(
            results=[
                tracker_result,
                navigator_result,
                json.dumps({"structuredContent": {"sources_scraped": 2}}),
                navigator_result,
                json.dumps({"structuredContent": {"research_brief": "oa-brief.md"}}),
                navigator_result,
                tracker_result,
            ]
        )
        background_callbacks: list[Callable[[], None]] = []
        delivered: list[tuple[str, str, str | None]] = []
        plugins = ModuleType("hermes_cli.plugins")
        plugins.DiscordButton = Button
        plugins.DiscordCommandResponse = Response
        hermes_cli = ModuleType("hermes_cli")
        hermes_cli.__version__ = "0.18.2"
        hermes_cli.plugins = plugins

        with patch.dict(
            sys.modules,
            {"hermes_cli": hermes_cli, "hermes_cli.plugins": plugins},
        ):
            self.router.register(
                context,
                background_runner=background_callbacks.append,
                plan_delivery=lambda channel_id, message, pdf: delivered.append(
                    (channel_id, message, pdf)
                ),
            )
            tracker = context.commands["erga-tracker"]("")
            opened = context.discord_button_handlers["erga.card.action"](
                type(
                    "Interaction",
                    (),
                    {"payload": tracker.buttons[0].payload, "user_id": "42"},
                )()
            )
            refreshed = context.discord_button_handlers["erga.card.action"](
                type(
                    "Interaction",
                    (),
                    {
                        "payload": opened.buttons[0].payload,
                        "user_id": "42",
                        "channel_id": "123",
                    },
                )()
            )
            briefed = context.discord_button_handlers["erga.card.action"](
                type(
                    "Interaction",
                    (),
                    {
                        "payload": opened.buttons[1].payload,
                        "user_id": "42",
                        "channel_id": "123",
                    },
                )()
            )
            self.assertEqual(len(background_callbacks), 2)
            self.assertEqual(len(context.calls), 2)
            for callback in background_callbacks:
                callback()
            returned = context.discord_button_handlers["erga.card.action"](
                type(
                    "Interaction",
                    (),
                    {"payload": opened.buttons[2].payload, "user_id": "42"},
                )()
            )

        self.assertIsInstance(tracker, Response)
        self.assertEqual(
            [button.label for button in tracker.buttons],
            ["Research · Example", "Orbit"],
        )
        self.assertEqual(tracker.buttons[0].action_id, "erga.card.action")
        self.assertIsInstance(opened, Response)
        self.assertIn("[Official posting](https://jobs.example.test/role)", opened.text)
        self.assertEqual(
            [button.label for button in opened.buttons],
            ["Refresh sources", "Create OA brief", "Back to tracker"],
        )
        self.assertIsInstance(refreshed, Response)
        self.assertIn("Research refresh started", refreshed.text)
        self.assertEqual(refreshed.buttons, ())
        self.assertIsInstance(briefed, Response)
        self.assertIn("OA brief started", briefed.text)
        self.assertEqual(briefed.buttons, ())
        self.assertIsInstance(returned, Response)
        self.assertEqual(returned.text, "Example — Software Engineer Intern · OA")
        self.assertEqual(len(delivered), 2)
        self.assertEqual(delivered[0][0], "123")
        self.assertIn("Sources refreshed", delivered[0][1])
        self.assertIn("2 saved notes · 2 useful links", delivered[0][1])
        self.assertEqual(delivered[0][2], None)
        self.assertEqual(delivered[1][0], "123")
        self.assertIn("OA brief created", delivered[1][1])
        self.assertIn("OA research · Example", delivered[1][1])
        self.assertEqual(delivered[1][2], None)
        self.assertEqual(
            context.calls,
            [
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                ),
                (
                    "mcp__erga_mcp__research_navigator",
                    {"job_url": "https://jobs.example.test/role"},
                ),
                (
                    "mcp__erga_mcp__discover_job_research",
                    {
                        "query": "Example Software Engineer Intern",
                        "job_url": "https://jobs.example.test/role",
                    },
                ),
                (
                    "mcp__erga_mcp__research_navigator",
                    {"job_url": "https://jobs.example.test/role"},
                ),
                (
                    "mcp__erga_mcp__create_research_brief",
                    {"job_url": "https://jobs.example.test/role", "stage": "oa"},
                ),
                (
                    "mcp__erga_mcp__research_navigator",
                    {"job_url": "https://jobs.example.test/role"},
                ),
                (
                    "mcp__erga_mcp__application_tracker",
                    {"query": "", "page": 1, "page_size": 6},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
