from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from erga_mcp.applications.discovery import discover_job_research
from erga_mcp.applications.identity import job_identity, posting_identifier, slug_with_identifier
from erga_mcp.applications.intake import fetch_job_snapshot, select_relevant_evidence
from erga_mcp.applications.lookup import select_tracked_application
from erga_mcp.applications.research import analyze_job_snapshot, official_job_text
from erga_mcp.config import (
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_PATH,
    ErgaConfig,
    ResumeSettings,
    load_config,
)
from erga_mcp.integrations.discord.bridge import (
    ErgaUpdateError,
    connect_discord_bridge,
    discord_status,
    erga_checkout_root,
    run_discord_bridge,
    start_discord_bridge,
    stop_discord_bridge,
    store_discord_token,
    update_erga_checkout,
)
from erga_mcp.integrations.discord.setup import (
    configure_discord_interactive,
    render_discord_setup_report,
)
from erga_mcp.integrations.hermes import (
    install_hermes_monitor_scripts,
    install_hermes_update_script,
    request_hermes_gateway_restart,
    synchronize_router_plugin,
)
from erga_mcp.integrations.hosts import (
    SUPPORTED_HOSTS,
    HostName,
    collect_optional_hosts,
    configure_hosts,
)
from erga_mcp.integrations.keryx import (
    disable_keryx,
    enable_keryx,
    keryx_status,
    search_keryx_jobs,
    sync_keryx,
)
from erga_mcp.integrations.mail.provider import build_mail_provider
from erga_mcp.integrations.mail.settings import as_json as mail_settings_as_json
from erga_mcp.integrations.mail.settings import update_settings as update_mail_settings
from erga_mcp.integrations.mail.zoho import ingest_fixture
from erga_mcp.integrations.mail.zoho_live import (
    fetch_inbox_metadata,
    format_recruiting_alerts,
    sync_metadata,
)
from erga_mcp.integrations.mail.zoho_oauth import (
    connect,
    read_client_secret,
    refresh_access_token,
    store_client_secret,
)
from erga_mcp.integrations.obsidian.importer import import_markdown_evidence
from erga_mcp.integrations.obsidian.tracker import reconcile_application_status_tracker_rows
from erga_mcp.models import Application
from erga_mcp.operations.doctor import check_installation
from erga_mcp.operations.exporting import export_bundle
from erga_mcp.operations.private_files import restrict_private_directory, restrict_private_file
from erga_mcp.operations.setup_wizard import (
    WizardCancelled,
    apply_core_setup,
    collect_core_setup_selections,
    render_core_setup_report,
    write_core_setup_plan,
)
from erga_mcp.operations.uninstall import (
    apply_uninstall,
    build_uninstall_plan,
    confirmation_phrase,
    render_uninstall_plan,
)
from erga_mcp.portfolio.catalogue import build_project_catalogue
from erga_mcp.portfolio.git_evidence import (
    analyze_commits,
    commits_missing_observations,
    discover_worktrees,
    scan_commits,
    synthesize_diff_research,
    synthesize_project_research,
    validate_worktree,
)
from erga_mcp.portfolio.github import discover_github_projects
from erga_mcp.portfolio.inventory import load_project_inventory
from erga_mcp.portfolio.roots import update_portfolio_roots
from erga_mcp.portfolio.skill_inventory import parse_skill_seed_csv
from erga_mcp.portfolio.skills import (
    approve_git_skill_group,
    build_git_skill_review_card,
    reconcile_git_skill_groups,
)
from erga_mcp.resumes.artifacts import (
    create_job_package,
    create_resume_proposal,
    create_section_resume_proposal,
    validate_latex_proposal,
)
from erga_mcp.resumes.cover_letter import create_cover_letter_proposal, load_style_context
from erga_mcp.resumes.cover_letter_settings import as_json as cover_letter_settings_as_json
from erga_mcp.resumes.cover_letter_settings import update_settings as update_cover_letter_settings
from erga_mcp.resumes.experience_inventory import (
    add_user_experience_bullet,
    load_experience_inventory,
)
from erga_mcp.resumes.outcomes import build_resume_outcome_report
from erga_mcp.resumes.render_validation import validate_resume_render
from erga_mcp.resumes.settings import as_json as resume_settings_as_json
from erga_mcp.resumes.settings import update_settings
from erga_mcp.resumes.sources import (
    import_master_resume,
    load_resume_source,
    resume_source_context,
    snapshot_resume_source,
)
from erga_mcp.resumes.tailoring import create_automatic_resume_proposal
from erga_mcp.resumes.template import ensure_resume_template, reset_resume_template
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.contact_projection import project_recruiter_contacts
from erga_mcp.tracking.mail_reconciliation import reconcile_mail_events
from erga_mcp.tracking.onboarding import build_onboarding_card
from erga_mcp.tracking.orbit import create_orbit_artifact, render_orbit_png
from erga_mcp.tracking.reporting import render_history_digest
from erga_mcp.tracking.settings import build_settings_card


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=argparse.SUPPRESS,
    )


def _welcome_text() -> str:
    return """Welcome to Erga — your private career assistant.

First time here?
  1. Run: erga setup
  2. Run: erga tailor <job link>
  3. Or paste a description: erga tailor --job-file job.txt

Already set up?
  • Tailor my résumé: erga tailor <job link>
  • Track an application: erga applications add --company NAME --role ROLE --source-url URL
  • Check setup: erga status

Erga creates reviewable drafts. It never applies, submits, or messages anyone for you."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="erga",
        description="Your private career assistant. Start with `erga setup`.",
    )
    subcommands = parser.add_subparsers(dest="command")

    tailor = subcommands.add_parser(
        "tailor",
        help="tailor your résumé for a job link and check the finished PDF",
    )
    tailor.add_argument("job_url", nargs="?", help="the public job-posting link")
    _config_argument(tailor)
    tailor.add_argument("--job-text", help="paste the job description directly")
    tailor.add_argument("--job-file", type=Path, help="read a job description from a text file")
    tailor.add_argument("--company", help="correct or supply the company name")
    tailor.add_argument("--role", help="correct or supply the role title")
    tailor.add_argument(
        "--preset",
        choices=("concise", "balanced", "technical"),
        default="balanced",
        help="concise, balanced (default), or technical evidence emphasis",
    )
    tailor.add_argument("--project-count", type=int, help="projects to select for this resume")
    tailor.add_argument("--max-pages", type=int, help="page limit for this resume")
    tailor.add_argument("--experience-min-bullets", type=int)
    tailor.add_argument("--experience-max-bullets", type=int)
    tailor.add_argument(
        "--experience-tailoring",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="select approved alternate bullets for each experience (saved setting by default)",
    )
    tailor.add_argument("--project-min-bullets", type=int)
    tailor.add_argument("--project-max-bullets", type=int)
    tailor.add_argument("--minimum-page-fill", type=float)
    tailor.add_argument("--json", action="store_true", help="print machine-readable results")
    tailor.add_argument(
        "--output-dir",
        type=Path,
        help="advanced: override Erga's automatic private output folder",
    )
    tailor.add_argument(
        "--validate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="check the finished PDF (enabled by default)",
    )

    init = subcommands.add_parser(
        "init", help="create a local non-secret configuration and database"
    )
    _config_argument(init)

    setup = subcommands.add_parser("setup", help="guided private résumé and tracker setup")
    _config_argument(setup)
    setup.add_argument("--vault", type=Path)
    setup.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and review choices without changing local state",
    )

    uninstall = subcommands.add_parser(
        "uninstall",
        help="remove Erga-owned local data, credentials, and connections",
    )
    _config_argument(uninstall)
    uninstall.add_argument(
        "--project-dir",
        type=Path,
        action="append",
        default=[],
        help="additional workspace whose shared MCP config should have only Erga removed",
    )
    uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="print the complete bounded deletion plan without changing anything",
    )
    uninstall.add_argument(
        "--yes",
        action="store_true",
        help="apply the displayed plan without typing the confirmation phrase",
    )

    connect_host = subcommands.add_parser(
        "connect",
        help="advanced: connect Erga to local AI apps",
    )
    _config_argument(connect_host)
    connect_host.add_argument(
        "--host",
        action="append",
        choices=SUPPORTED_HOSTS,
        default=[],
        help="host to connect; repeat for multiple hosts, or omit for the arrow-key picker",
    )
    connect_host.add_argument("--project-dir", type=Path, default=Path.cwd())
    connect_host.add_argument("--server-command", type=Path)
    connect_host.add_argument(
        "--dry-run",
        action="store_true",
        help="preview exact host configuration without writing it",
    )

    discord_bridge = subcommands.add_parser(
        "discord",
        help="connect and run your private Discord assistant",
    )
    discord_commands = discord_bridge.add_subparsers(
        dest="discord_command",
        required=True,
    )
    discord_configure = discord_commands.add_parser(
        "configure",
        help="guided Discord connection",
    )
    _config_argument(discord_configure)
    discord_configure.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help=argparse.SUPPRESS,
    )
    discord_configure.add_argument(
        "--advanced",
        action="store_true",
        help="show runtime and workspace choices for maintainers",
    )
    discord_configure.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    for name, help_text in (
        ("run", "run the configured Discord bridge in the foreground"),
        ("connect", "reconnect using the existing Discord and coding-host setup"),
        ("start", "start the configured Discord bridge and wait for readiness"),
        ("status", "show whether the optional Discord bridge is configured and running"),
        ("stop", "stop the recorded background Discord bridge"),
        ("set-token", "replace the saved Discord bot token without rerunning setup"),
    ):
        discord_command = discord_commands.add_parser(name, help=help_text)
        _config_argument(discord_command)
        if name in {"connect", "start", "status", "stop", "set-token"}:
            discord_command.add_argument(
                "--json",
                action="store_true",
                help="print machine-readable runtime details",
            )

    status = subcommands.add_parser("status", help="show what is ready and what to do next")
    _config_argument(status)
    status.add_argument("--json", action="store_true", help="print machine-readable counts")
    review = subcommands.add_parser(
        "review", help="plain-language résumé, evidence, and generation readiness check"
    )
    _config_argument(review)
    review.add_argument("--json", action="store_true", help="print machine-readable readiness")
    tracker = subcommands.add_parser("tracker", help="inspect local application tracking")
    _config_argument(tracker)
    tracker_commands = tracker.add_subparsers(dest="tracker_command")
    tracker_orbit = tracker_commands.add_parser(
        "orbit", help="render the aggregate Erga Orbit application-flow image"
    )
    _config_argument(tracker_orbit)
    tracker_orbit.add_argument("--cycle", default="")
    tracker_orbit.add_argument("--output", type=Path)
    doctor = subcommands.add_parser("doctor", help="run advanced local diagnostics")
    _config_argument(doctor)

    onboarding = subcommands.add_parser("onboarding", help="review and update guided setup")
    onboarding_commands = onboarding.add_subparsers(dest="onboarding_command", required=True)
    onboarding_status = onboarding_commands.add_parser(
        "status", help="show the shared onboarding completion card"
    )
    _config_argument(onboarding_status)
    onboarding_status.add_argument("--json", action="store_true")
    onboarding_skills = onboarding_commands.add_parser(
        "skills", help="manage self-reported review-only skill seeds"
    )
    onboarding_skill_commands = onboarding_skills.add_subparsers(
        dest="onboarding_skill_command", required=True
    )
    for action in ("list", "set", "add", "check", "uncheck", "remove"):
        skill_command = onboarding_skill_commands.add_parser(action)
        _config_argument(skill_command)
        if action == "set":
            skill_command.add_argument("--csv", required=True)
        elif action != "list":
            skill_command.add_argument("skill")
    onboarding_roots = onboarding_commands.add_parser(
        "roots", help="manage explicit local Git/project roots"
    )
    onboarding_root_commands = onboarding_roots.add_subparsers(
        dest="onboarding_root_command", required=True
    )
    for action in ("list", "add", "remove"):
        root_command = onboarding_root_commands.add_parser(action)
        _config_argument(root_command)
        if action != "list":
            root_command.add_argument("root", type=Path)

    settings = subcommands.add_parser("settings", help="show private setup status")
    _config_argument(settings)
    settings.add_argument("--json", action="store_true")
    keryx = subcommands.add_parser(
        "keryx",
        help="optionally cache and search Keryx's public US opportunity index",
    )
    keryx_commands = keryx.add_subparsers(dest="keryx_command", required=True)
    for name, help_text in (
        ("enable", "explicitly enable Keryx and download its public index"),
        ("disable", "disable Keryx discovery without deleting private Erga state"),
        ("sync", "refresh the enabled local Keryx cache"),
        ("status", "show whether the optional Keryx cache is ready"),
    ):
        keryx_command = keryx_commands.add_parser(name, help=help_text)
        _config_argument(keryx_command)
    keryx_search = keryx_commands.add_parser(
        "search",
        help="search the local Keryx cache without sending the query anywhere",
    )
    _config_argument(keryx_search)
    keryx_search.add_argument("query", nargs="?", default="")
    keryx_search.add_argument("--program", choices=("internship", "new-grad"), default="")
    keryx_search.add_argument("--cycle", default="")
    keryx_search.add_argument("--location", default="")
    keryx_search.add_argument("--limit", type=int, default=20)

    evidence = subcommands.add_parser("evidence", help="advanced: manage approved career facts")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_add = evidence_commands.add_parser("add", help="add local career evidence")
    _config_argument(evidence_add)
    evidence_add.add_argument("--source-ref", required=True)
    evidence_add.add_argument("--text", required=True)
    evidence_add.add_argument("--approved", action="store_true")

    git = subcommands.add_parser(
        "git", help="advanced: find reviewable experience in local projects"
    )
    git_commands = git.add_subparsers(dest="git_command", required=True)
    git_scan = git_commands.add_parser(
        "scan", help="scan bounded new commits into unapproved candidates"
    )
    _config_argument(git_scan)
    git_scan.add_argument("repo", type=Path, nargs="?")
    git_scan.add_argument("--all", action="store_true", help="scan every worktree below --root")
    git_scan.add_argument(
        "--configured-roots",
        action="store_true",
        help="also scan worktrees below explicitly saved portfolio roots",
    )
    git_scan.add_argument(
        "--seed-csv",
        help="one-shot review hints; parsed and returned but never persisted as evidence",
    )
    git_scan.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="directory to search when using --all",
    )
    git_candidates = git_commands.add_parser("candidates", help="list git evidence candidates")
    _config_argument(git_candidates)
    git_manual_add = git_commands.add_parser(
        "manual-add", help="add a user-supplied project as an unapproved review draft"
    )
    _config_argument(git_manual_add)
    git_manual_add.add_argument("--title", required=True)
    git_manual_add.add_argument("--description", required=True)
    git_review = git_commands.add_parser(
        "review", help="review one persisted Git or manual project draft"
    )
    _config_argument(git_review)
    git_review.add_argument("action", choices=("show", "next", "back", "save", "skip", "edit"))
    git_review.add_argument("draft_id", nargs="?")
    git_review.add_argument("--title")
    git_review.add_argument("--description")
    git_research = git_commands.add_parser(
        "research", help="run or list local review-only git research drafts"
    )
    _config_argument(git_research)
    git_research.add_argument("--all", action="store_true", help="run research below --root")
    git_research.add_argument(
        "--root",
        type=Path,
        action="append",
        default=[],
        help="directory to search when using --all",
    )
    git_approve = git_commands.add_parser(
        "approve", help="approve one candidate as regular evidence"
    )
    _config_argument(git_approve)
    git_approve.add_argument("candidate_id")
    git_skills = git_commands.add_parser(
        "skills", help="review reconciled self-reported and Git-corroborated skill groups"
    )
    git_skill_commands = git_skills.add_subparsers(dest="git_skill_command", required=True)
    git_skills_show = git_skill_commands.add_parser("show")
    _config_argument(git_skills_show)
    git_skills_show.add_argument("--page", type=int, default=1)
    git_skills_show.add_argument("--page-size", type=int, default=5)
    git_skills_show.add_argument("--filter", dest="source_filter")
    git_skills_show.add_argument("--seed-csv")
    git_skills_show.add_argument("--json", action="store_true")
    for action in ("approve", "skip", "restore"):
        git_skill_action = git_skill_commands.add_parser(action)
        _config_argument(git_skill_action)
        git_skill_action.add_argument("skill")
    git_projects = git_commands.add_parser(
        "projects", help="browse the approved and GitHub-discovered project catalogue"
    )
    _config_argument(git_projects)
    git_projects.add_argument("--page", type=int, default=1)
    git_projects.add_argument("--page-size", type=int, default=6)
    git_projects.add_argument("--query", default="")
    git_projects.add_argument(
        "--refresh",
        action="store_true",
        help="refresh the private GitHub project cache before rendering",
    )
    git_projects.add_argument("--json", action="store_true")

    obsidian = subcommands.add_parser("obsidian", help="import configured Obsidian evidence")
    obsidian_commands = obsidian.add_subparsers(dest="obsidian_command", required=True)
    obsidian_import = obsidian_commands.add_parser(
        "import", help="read a configured Markdown note without modifying the vault"
    )
    _config_argument(obsidian_import)
    obsidian_import.add_argument("--note", type=Path, required=True)

    mail = subcommands.add_parser("mail", help="synchronize the configured read-only mail provider")
    mail_commands = mail.add_subparsers(dest="mail_command", required=True)
    mail_sync = mail_commands.add_parser(
        "sync", help="read bounded metadata and update local events"
    )
    _config_argument(mail_sync)
    mail_sync.add_argument("--limit", type=int, default=20)
    mail_sync.add_argument(
        "--notify",
        action="store_true",
        help="print only a private notification for new relevant events; stay silent otherwise",
    )
    mail_history = mail_commands.add_parser(
        "history", help="render a metadata-only application and recruiting-event digest"
    )
    _config_argument(mail_history)
    mail_history.add_argument("--days", type=int, default=7)
    mail_configure = mail_commands.add_parser(
        "configure", help="update non-secret mail provider settings"
    )
    _config_argument(mail_configure)
    mail_configure.add_argument("--provider", choices=("gmail", "zoho"))
    mail_configure.add_argument("--gws-command")
    mail_configure.add_argument("--client-id")
    mail_configure.add_argument("--accounts-url")
    mail_configure.add_argument("--folder")

    zoho = subcommands.add_parser("zoho", help="run bounded local Zoho adapter checks")
    zoho_commands = zoho.add_subparsers(dest="zoho_command", required=True)
    zoho_fixture = zoho_commands.add_parser(
        "ingest-fixture", help="classify local synthetic metadata without OAuth or network access"
    )
    _config_argument(zoho_fixture)
    zoho_fixture.add_argument("--fixture", type=Path, required=True)
    zoho_secret = zoho_commands.add_parser(
        "set-client-secret", help="store a Zoho OAuth client secret in the OS credential store"
    )
    zoho_secret.add_argument("--client-id", required=True)
    zoho_connect = zoho_commands.add_parser(
        "connect", help="open Zoho's read-only OAuth consent flow"
    )
    zoho_connect.add_argument("--client-id", required=True)
    zoho_connect.add_argument("--accounts-url", default="https://accounts.zoho.com")
    zoho_sync = zoho_commands.add_parser(
        "sync", help="read recent Inbox metadata and record local events"
    )
    _config_argument(zoho_sync)
    zoho_sync.add_argument("--client-id", required=True)
    zoho_sync.add_argument("--limit", type=int, default=20)

    resume = subcommands.add_parser(
        "resume", help="advanced résumé controls; normal use is `erga tailor`"
    )
    resume_commands = resume.add_subparsers(dest="resume_command", required=True)
    resume_propose = resume_commands.add_parser(
        "propose", help="create a local proposal without modifying or syncing the source"
    )
    _config_argument(resume_propose)
    resume_propose.add_argument("--resume", type=Path, required=True)
    resume_propose.add_argument("--output-dir", type=Path, required=True)
    resume_propose.add_argument("--latex-snippet", required=True)
    resume_propose.add_argument("--evidence-id", action="append", default=[])
    resume_tailor = resume_commands.add_parser(
        "tailor", help="create a section-only reviewable proposal"
    )
    _config_argument(resume_tailor)
    resume_tailor.add_argument("--section", required=True)
    resume_tailor.add_argument("--latex-content", required=True)
    resume_tailor.add_argument("--output-dir", type=Path, required=True)
    resume_tailor.add_argument("--evidence-id", action="append", default=[])
    resume_tailor_job = resume_commands.add_parser(
        "tailor-job",
        help="fetch a job posting and create a complete deterministic evidence-backed proposal",
    )
    _config_argument(resume_tailor_job)
    resume_tailor_job.add_argument("--job-url")
    resume_tailor_job.add_argument("--job-text")
    resume_tailor_job.add_argument("--job-file", type=Path)
    resume_tailor_job.add_argument("--company")
    resume_tailor_job.add_argument("--role")
    resume_tailor_job.add_argument(
        "--preset", choices=("concise", "balanced", "technical"), default="balanced"
    )
    resume_tailor_job.add_argument("--project-count", type=int)
    resume_tailor_job.add_argument("--max-pages", type=int)
    resume_tailor_job.add_argument("--experience-min-bullets", type=int)
    resume_tailor_job.add_argument("--experience-max-bullets", type=int)
    resume_tailor_job.add_argument("--project-min-bullets", type=int)
    resume_tailor_job.add_argument("--project-max-bullets", type=int)
    resume_tailor_job.add_argument("--minimum-page-fill", type=float)
    resume_tailor_job.add_argument(
        "--output-dir",
        type=Path,
        help="advanced: override Erga's automatic private output folder",
    )
    resume_tailor_job.add_argument(
        "--validate",
        action="store_true",
        help="check that the finished PDF opens and meets your layout settings",
    )
    resume_insights = resume_commands.add_parser(
        "insights",
        help="show local directional signals from explicitly used résumé versions and outcomes",
    )
    _config_argument(resume_insights)
    resume_validate = resume_commands.add_parser(
        "validate",
        help="compile an explicitly selected local proposal without remote synchronization",
    )
    _config_argument(resume_validate)
    resume_validate.add_argument("--proposal", type=Path, required=True)
    resume_validate.add_argument("--latexmk", type=Path, default=Path("latexmk"))
    resume_template = resume_commands.add_parser(
        "template", help="generate or inspect the private editable LaTeX template"
    )
    resume_template_commands = resume_template.add_subparsers(
        dest="resume_template_command", required=True
    )
    resume_template_ensure = resume_template_commands.add_parser(
        "ensure", help="generate or reuse a template from the approved master"
    )
    _config_argument(resume_template_ensure)
    resume_template_reset = resume_template_commands.add_parser(
        "reset",
        help="clear the style/custom template and regenerate Erga's default Jake-style template",
    )
    _config_argument(resume_template_reset)
    resume_template_set = resume_template_commands.add_parser(
        "set",
        help="add or replace the visual template while preserving the approved master",
    )
    _config_argument(resume_template_set)
    resume_template_set.add_argument(
        "source", type=Path, metavar="PATH", help="PDF, DOCX, or .tex visual reference"
    )
    resume_master = resume_commands.add_parser(
        "master", help="add or replace the factual master resume"
    )
    resume_master_commands = resume_master.add_subparsers(
        dest="resume_master_command", required=True
    )
    resume_master_set = resume_master_commands.add_parser(
        "set",
        help="add or replace the master and regenerate the current visual template",
    )
    _config_argument(resume_master_set)
    resume_master_set.add_argument(
        "source", type=Path, metavar="PATH", help="PDF, DOCX, or .tex factual master"
    )
    resume_settings = resume_commands.add_parser("settings", help="manage generic resume settings")
    resume_settings_commands = resume_settings.add_subparsers(
        dest="resume_settings_command", required=True
    )
    resume_settings_show = resume_settings_commands.add_parser("show", help="show resume settings")
    _config_argument(resume_settings_show)
    resume_settings_set = resume_settings_commands.add_parser("set", help="update resume settings")
    _config_argument(resume_settings_set)
    resume_settings_set.add_argument("--template-path")
    resume_settings_set.add_argument("--editable-section", action="append")
    resume_settings_set.add_argument("--bullet-min-chars", type=int)
    resume_settings_set.add_argument("--bullet-target-chars", type=int)
    resume_settings_set.add_argument("--bullet-max-chars", type=int)
    resume_settings_set.add_argument("--single-line-bullets", action=argparse.BooleanOptionalAction)
    resume_settings_set.add_argument(
        "--experience-tailoring", action=argparse.BooleanOptionalAction
    )
    resume_settings_set.add_argument("--experience-inventory-path")
    resume_settings_set.add_argument("--max-pages", type=int)
    resume_settings_set.add_argument("--experience-min-bullets", type=int)
    resume_settings_set.add_argument("--experience-max-bullets", type=int)
    resume_settings_set.add_argument("--project-min-bullets", type=int)
    resume_settings_set.add_argument("--project-max-bullets", type=int)
    resume_settings_set.add_argument("--project-count", type=int)
    resume_settings_set.add_argument("--minimum-page-fill-ratio", type=float)
    resume_settings_set.add_argument(
        "--require-unique-lead-verbs", action=argparse.BooleanOptionalAction
    )
    resume_settings_set.add_argument("--output-root")
    resume_settings_set.add_argument("--output-pdf-name")
    resume_settings_set.add_argument("--latexmk")
    resume_experience = resume_commands.add_parser(
        "experience", help="manage approved alternative bullets for your work experience"
    )
    resume_experience_commands = resume_experience.add_subparsers(
        dest="resume_experience_command", required=True
    )
    resume_experience_list = resume_experience_commands.add_parser(
        "list", help="show stored roles and available bullet counts"
    )
    _config_argument(resume_experience_list)
    resume_experience_add = resume_experience_commands.add_parser(
        "add", help="add a fact you personally confirm for one role"
    )
    _config_argument(resume_experience_add)
    resume_experience_add.add_argument("--role", required=True)
    resume_experience_add.add_argument("--company", required=True)
    resume_experience_add.add_argument("--bullet", required=True)
    resume_experience_add.add_argument("--tag", action="append", default=[])
    resume_sources = resume_commands.add_parser(
        "sources", help="manage durable master knowledge and style references"
    )
    resume_sources_commands = resume_sources.add_subparsers(
        dest="resume_sources_command", required=True
    )
    resume_sources_import = resume_sources_commands.add_parser(
        "import", help="copy and register user-selected resume sources"
    )
    _config_argument(resume_sources_import)
    resume_sources_import.add_argument("--master", type=Path, required=True)
    resume_sources_import.add_argument("--style", type=Path)
    resume_sources_context = resume_sources_commands.add_parser(
        "context", help="show approved master context and non-factual style metadata"
    )
    _config_argument(resume_sources_context)
    resume_package = resume_commands.add_parser(
        "create-package", help="create an isolated job output package"
    )
    _config_argument(resume_package)
    resume_package.add_argument("--cycle", required=True)
    resume_package.add_argument("--application-slug", required=True)
    resume_package.add_argument("--job-url", required=True)

    cover_letter = subcommands.add_parser(
        "cover-letter", help="create reviewable local cover-letter proposals"
    )
    cover_letter_commands = cover_letter.add_subparsers(dest="cover_letter_command", required=True)
    cover_letter_context = cover_letter_commands.add_parser(
        "context", help="read configured template and style sample without modifying either"
    )
    _config_argument(cover_letter_context)
    cover_letter_propose = cover_letter_commands.add_parser(
        "propose", help="render a reviewed draft into the configured template"
    )
    _config_argument(cover_letter_propose)
    cover_letter_propose.add_argument("--output-dir", type=Path, required=True)
    cover_letter_body = cover_letter_propose.add_mutually_exclusive_group(required=True)
    cover_letter_body.add_argument("--body")
    cover_letter_body.add_argument(
        "--body-file",
        type=Path,
        help="read the draft body from a local UTF-8 text or Markdown file",
    )
    cover_letter_propose.add_argument("--evidence-id", action="append", default=[])
    cover_letter_settings = cover_letter_commands.add_parser(
        "settings", help="manage generic cover-letter settings"
    )
    cover_letter_settings_commands = cover_letter_settings.add_subparsers(
        dest="cover_letter_settings_command", required=True
    )
    cover_letter_settings_show = cover_letter_settings_commands.add_parser(
        "show", help="show cover-letter settings"
    )
    _config_argument(cover_letter_settings_show)
    cover_letter_settings_set = cover_letter_settings_commands.add_parser(
        "set", help="update cover-letter settings"
    )
    _config_argument(cover_letter_settings_set)
    cover_letter_settings_set.add_argument("--template-path")
    cover_letter_settings_set.add_argument("--writing-sample-path")

    notes = subcommands.add_parser(
        "notes", help="show one tracked application's status and all saved research"
    )
    _config_argument(notes)
    notes.add_argument("query", help="company or role words, for example: erga notes uber")

    research = subcommands.add_parser(
        "research",
        help="search, scrape, and save cited public research for one tracked application",
    )
    _config_argument(research)
    research.add_argument("query", help="company or role words, for example: erga research uber")

    applications = subcommands.add_parser("applications", help="manage local applications")
    _config_argument(applications)
    application_commands = applications.add_subparsers(dest="applications_command", required=False)
    applications_list = application_commands.add_parser("list", help="list applications")
    _config_argument(applications_list)
    applications_add = application_commands.add_parser("add", help="add a draft application")
    _config_argument(applications_add)
    applications_add.add_argument("--company", required=True)
    applications_add.add_argument("--role", required=True)
    applications_add.add_argument("--source-url", required=True)
    applications_add.add_argument("--evidence-id", action="append", default=[])
    applications_status = application_commands.add_parser(
        "update-status", help="record a user-approved local application status change"
    )
    _config_argument(applications_status)
    applications_status.add_argument("--application-id", required=True)
    applications_status.add_argument("--status", required=True)

    tokens = subcommands.add_parser(
        "tokens", help="show recorded model token usage without estimating a dollar cost"
    )
    _config_argument(tokens)
    tokens.add_argument("--application-id")

    export = subcommands.add_parser(
        "export", help="create a private ZIP bundle of pipeline state and job packages"
    )
    _config_argument(export)
    export.add_argument("--output", type=Path, required=True)

    update = subcommands.add_parser(
        "update", help="safely update an official clean Erga main checkout"
    )
    _config_argument(update)
    update.add_argument("--scheduled", action="store_true", help=argparse.SUPPRESS)
    update.add_argument(
        "--hermes-home",
        type=Path,
        default=Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")),
        help=argparse.SUPPRESS,
    )

    monitor = subcommands.add_parser(
        "monitor", help="advanced: prepare scheduled background checks"
    )
    monitor_commands = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_install = monitor_commands.add_parser(
        "install-hermes-scripts",
        help="install no-agent mail and history scripts under the Hermes scripts directory",
    )
    _config_argument(monitor_install)
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    monitor_install.add_argument("--scripts-dir", type=Path, default=hermes_home / "scripts")
    monitor_install.add_argument("--history-days", type=int, default=7)
    monitor_install.add_argument("--replace", action="store_true")
    update_install = monitor_commands.add_parser(
        "install-hermes-update",
        help="install the opt-in no-agent Erga update runner under Hermes",
    )
    _config_argument(update_install)
    update_install.add_argument("--scripts-dir", type=Path, default=hermes_home / "scripts")
    update_install.add_argument("--replace", action="store_true")
    return parser


def _set_owner_only_permissions(path: Path, mode: int) -> None:
    """Restrict newly created private state on POSIX platforms."""
    if os.name == "posix":
        path.chmod(mode)


def _initialize(config_path: Path) -> int:
    config_path = config_path.expanduser()
    if config_path.exists():
        print(f"Config already exists: {config_path}")
        return 2
    config_parent_created = not config_path.parent.exists()
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if config_parent_created:
        _set_owner_only_permissions(config_path.parent, 0o700)
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    restrict_private_file(config_path)
    config = load_config(config_path)
    data_dir_created = not config.data_dir.exists()
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data_dir_created:
        _set_owner_only_permissions(config.data_dir, 0o700)
    database_path = config.data_dir / "erga.sqlite3"
    database_created = not database_path.exists()
    ErgaStore(database_path).initialize()
    if database_created:
        _set_owner_only_permissions(database_path, 0o600)
    print(f"Created local configuration: {config.config_path}")
    print(f"Created local data directory: {config.data_dir}")
    print("Next: run `erga setup` to add your résumé.")
    return 0


def _store_for(config_path: Path) -> ErgaStore:
    config = load_config(config_path)
    store = ErgaStore(config.data_dir / "erga.sqlite3")
    store.initialize()
    return store


def _print_json(value: object) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


def _render_human_status(config: ErgaConfig, store: ErgaStore) -> str:
    resume_ready = config.resume.master_path is not None
    try:
        discord = discord_status(config.config_path)
    except (OSError, RuntimeError, ValueError):
        discord = {"configured": False, "running": False, "ready": False}
    if discord.get("ready"):
        discord_line = "✓ Discord is online"
    elif discord.get("configured"):
        discord_line = "! Discord is connected but offline"
    else:
        discord_line = "! Discord is not connected"
    applications = store.list_applications()
    approved = sum(item.approved for item in store.list_evidence())
    lines = [
        "Erga status",
        "",
        "✓ Private workspace is ready",
        "✓ Résumé is ready" if resume_ready else "! Résumé needed",
        f"○ Optional connection: {discord_line.removeprefix('! ').removeprefix('✓ ')}",
        f"{'✓' if approved else '○'} Career evidence: {approved} approved",
        f"○ Applications tracked: {len(applications)}",
        "",
        "Next:",
    ]
    if not resume_ready:
        lines.append("  Run `erga setup` to add your résumé.")
    else:
        lines.append("  Run `erga tailor <job link>` or `erga tailor --job-file job.txt`.")
        if discord.get("configured") and not discord.get("ready"):
            lines.append("  Optional Discord connection is offline; core CLI features still work.")
    lines.append("Nothing is sent or submitted without your action.")
    return "\n".join(lines)


def _resume_readiness(config: ErgaConfig, store: ErgaStore) -> dict[str, object]:
    approved = [item for item in store.list_evidence() if item.approved]
    projects: Sequence[object] = ()
    project_warning: str | None = None
    if config.resume.project_inventory_path is not None:
        try:
            projects = load_project_inventory(config.resume.project_inventory_path, approved)
        except (OSError, ValueError) as error:
            project_warning = str(error)
    template_ready = (
        config.resume.template_path is not None and config.resume.template_path.is_file()
    )
    return {
        "ready": bool(template_ready and approved),
        "master_ready": bool(
            config.resume.master_path is not None and config.resume.master_path.is_file()
        ),
        "template_ready": template_ready,
        "approved_evidence_count": len(approved),
        "project_count": len(projects),
        "project_warning": project_warning,
        "style_reference_configured": config.resume.reference_path is not None,
        "defaults": {
            "max_pages": config.resume.max_pages,
            "project_count": config.resume.project_count,
            "experience_bullets": [
                config.resume.experience_min_bullets,
                config.resume.experience_max_bullets,
            ],
            "project_bullets": [
                config.resume.project_min_bullets,
                config.resume.project_max_bullets,
            ],
            "minimum_page_fill_ratio": config.resume.minimum_page_fill_ratio,
        },
    }


def _render_resume_readiness(readiness: dict[str, object]) -> str:
    defaults = cast(dict[str, object], readiness["defaults"])
    experience = cast(list[int], defaults["experience_bullets"])
    projects = cast(list[int], defaults["project_bullets"])
    lines = [
        "Erga résumé review",
        "",
        f"{'✓' if readiness['master_ready'] else '!'} Factual master résumé",
        f"{'✓' if readiness['template_ready'] else '!'} Layout-preserving LaTeX template",
        f"✓ Approved evidence: {readiness['approved_evidence_count']} source(s)",
        f"{'✓' if readiness['project_count'] else '○'} Approved projects: "
        f"{readiness['project_count']}",
        (
            "✓ Separate style reference is configured"
            if readiness["style_reference_configured"]
            else "○ Using the master/default layout; no separate style reference"
        ),
        "",
        "Generation defaults",
        f"  Pages: {defaults['max_pages']}",
        f"  Projects selected: {defaults['project_count']}",
        f"  Experience bullets per entry: {experience[0]}–{experience[1]}",
        f"  Project bullets per entry: {projects[0]}–{projects[1]}",
        f"  Minimum one-page fill: {cast(float, defaults['minimum_page_fill_ratio']):.0%}",
    ]
    if readiness["project_warning"]:
        lines.extend(("", f"Needs attention: {readiness['project_warning']}"))
    elif not readiness["project_count"]:
        lines.extend(
            (
                "",
                "No projects were parsed from the master. Erga will not invent them; add "
                "approved project evidence before expecting project selection.",
            )
        )
    lines.extend(("", "Next: erga tailor <job link>"))
    return "\n".join(lines)


def _render_discord_runtime(report: dict[str, object], *, action: str = "") -> str:
    if action == "stop" and not report.get("running"):
        return "Discord is offline. Your private Erga data is unchanged."
    if report.get("ready"):
        return (
            "Discord is connected and Erga is online.\n\n"
            "Open Discord and send:\n"
            "  Tailor my résumé for this job: <paste link>\n\n"
            "Send `help` in Discord for more examples."
        )
    if report.get("running"):
        return "Discord is starting. Run `erga discord status` again in a moment."
    if report.get("configured"):
        return "Discord is connected but offline. Start it with `erga discord start`."
    return "Discord is not connected yet. Run `erga discord configure`."


def _update_runtime(*, config_path: Path, scheduled: bool, hermes_home: Path) -> dict[str, object]:
    bridge_was_running = False
    if config_path.expanduser().is_file():
        try:
            bridge_was_running = bool(discord_status(config_path).get("running"))
        except (OSError, RuntimeError, ValueError):
            bridge_was_running = False

    result = update_erga_checkout()
    checkout_root = erga_checkout_root()
    plugin_updated = False
    gateway_restart_requested = False
    upstream_revision = getattr(result, "upstream_revision", result.current_revision)
    if scheduled and upstream_revision == result.current_revision:
        plugin_updated = synchronize_router_plugin(
            checkout_root=checkout_root,
            hermes_home=hermes_home,
        )
        if plugin_updated:
            gateway_restart_requested = request_hermes_gateway_restart(hermes_home=hermes_home)

    bridge_restarted = False
    if result.updated and bridge_was_running:
        stopped = stop_discord_bridge(config_path)
        if not stopped.get("running"):
            started = start_discord_bridge(config_path)
            bridge_restarted = bool(started.get("running"))

    return {
        "updated": result.updated,
        "previous_revision": result.previous_revision,
        "current_revision": result.current_revision,
        "discord_bridge_restarted": bridge_restarted,
        "hermes_plugin_updated": plugin_updated,
        "hermes_gateway_restart_requested": gateway_restart_requested,
    }


def _notes_application(query: str, applications: list[Application]) -> Application:
    """Compatibility wrapper for the application-layer selector."""
    return select_tracked_application(query, applications)


def _package_for_application(output_root: Path, application: Application) -> Path | None:
    if not output_root.is_dir():
        return None
    identity = job_identity(application.source_url)
    for manifest_path in output_root.glob("*/*/package.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_url = manifest.get("job_url") if isinstance(manifest, dict) else None
        if isinstance(job_url, str) and job_identity(job_url) == identity:
            return manifest_path.parent
    return None


def _render_application_notes(application: Application, package_dir: Path | None) -> str:
    rendered = (
        f"# {application.company} - {application.role}\n\n"
        f"Status: {application.status}\n"
        f"Source: {application.source_url}\n"
        f"Tracked: {application.created_at.isoformat()}\n"
    )
    if package_dir is None:
        return rendered + "\n## Research\n\nNo local Erga package was found for this application.\n"
    research_dir = package_dir / "research"
    research_files = sorted(research_dir.glob("*.md")) if research_dir.is_dir() else []
    rendered += f"Package: {package_dir}\n\n## Research\n"
    if not research_files:
        return rendered + "\nNo saved research yet.\n"
    for path in research_files:
        content = path.read_text(encoding="utf-8").strip()
        rendered += f"\n---\n\n## {path.stem.replace('-', ' ').title()}\n\n{content}\n"
    return rendered


def _tailor_job_input(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve exactly one safe job source for both friendly and advanced CLI routes."""
    sources = [
        bool(getattr(args, "job_url", None)),
        bool(getattr(args, "job_text", None)),
        getattr(args, "job_file", None) is not None,
    ]
    if sum(sources) != 1:
        raise ValueError("provide exactly one job source: a job link, --job-text, or --job-file")
    job_url = getattr(args, "job_url", None)
    if job_url:
        return fetch_job_snapshot(str(job_url)), str(job_url)
    job_file = getattr(args, "job_file", None)
    if job_file is not None:
        path = Path(job_file).expanduser().absolute()
        if not path.is_file():
            raise ValueError(f"job description file does not exist: {path}")
        if path.stat().st_size > 2_000_000:
            raise ValueError("job description file must be 2 MB or smaller")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("job description file must be UTF-8 text") from error
        source = f"https://local.invalid/{path.stem or 'job-description'}"
    else:
        text = str(getattr(args, "job_text", ""))
        source = "https://local.invalid/job-description"
    normalized = text.strip()
    if len(normalized) < 40:
        raise ValueError("job description must contain at least 40 characters")
    return normalized, source


def _tailor_limits(args: argparse.Namespace, settings: ResumeSettings) -> dict[str, object]:
    """Resolve per-job controls without mutating the user's saved defaults."""
    preset = getattr(args, "preset", "balanced")
    project_count = getattr(args, "project_count", None) or settings.project_count
    max_pages = getattr(args, "max_pages", None) or settings.max_pages
    experience_min = (
        getattr(args, "experience_min_bullets", None) or settings.experience_min_bullets
    )
    experience_max = (
        getattr(args, "experience_max_bullets", None) or settings.experience_max_bullets
    )
    project_min = getattr(args, "project_min_bullets", None) or settings.project_min_bullets
    project_max = getattr(args, "project_max_bullets", None) or settings.project_max_bullets
    experience_tailoring_arg = getattr(args, "experience_tailoring", None)
    experience_tailoring = (
        settings.experience_tailoring
        if experience_tailoring_arg is None
        else bool(experience_tailoring_arg)
    )
    minimum_fill = cast(
        float,
        getattr(args, "minimum_page_fill", None)
        if getattr(args, "minimum_page_fill", None) is not None
        else settings.minimum_page_fill_ratio,
    )
    if preset == "concise":
        max_pages = 1
        project_count = min(project_count, 2)
        experience_max = min(experience_max, 3)
        project_max = min(project_max, 3)
    elif preset == "technical":
        project_count = max(project_count, 3)
    if project_count < 1 or max_pages < 1:
        raise ValueError("project count and page limit must be positive")
    if not 1 <= experience_min <= experience_max:
        raise ValueError("experience bullet limits must be ordered positive values")
    if not 1 <= project_min <= project_max:
        raise ValueError("project bullet limits must be ordered positive values")
    if not 0 <= minimum_fill <= 1:
        raise ValueError("minimum page fill must be between zero and one")
    return {
        "preset": preset,
        "project_count": project_count,
        "max_pages": max_pages,
        "experience_min_bullets": experience_min,
        "experience_max_bullets": experience_max,
        "experience_tailoring": experience_tailoring,
        "project_min_bullets": project_min,
        "project_max_bullets": project_max,
        "minimum_page_fill_ratio": minimum_fill,
    }


def _tailor_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"  {message}", file=sys.stderr, flush=True)


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command is None:
        print(_welcome_text())
        return 0
    friendly_tailor = args.command == "tailor" and not getattr(args, "json", False)
    if args.command == "tailor":
        args.command = "resume"
        args.resume_command = "tailor-job"
    if args.command == "update":
        try:
            update_report = _update_runtime(
                config_path=args.config,
                scheduled=args.scheduled,
                hermes_home=args.hermes_home,
            )
        except (ErgaUpdateError, OSError, RuntimeError, ValueError) as error:
            print(f"Erga update failed: {error}", file=sys.stderr)
            return 1
        if not args.scheduled or update_report["updated"] or update_report["hermes_plugin_updated"]:
            _print_json(update_report)
        return 0
    if args.command == "uninstall":
        plan = build_uninstall_plan(
            args.config,
            project_dirs=tuple(args.project_dir),
        )
        if args.dry_run:
            _print_json(plan.as_json())
            return 0
        print(render_uninstall_plan(plan))
        if not args.yes:
            try:
                response = input(f"\nType {confirmation_phrase()} to continue: ")
            except EOFError:
                response = ""
            if response != confirmation_phrase():
                print("Erga uninstall cancelled; nothing was deleted.")
                return 130
        try:
            _print_json(apply_uninstall(plan))
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Erga uninstall could not finish: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "init":
        return _initialize(args.config)
    if args.command == "setup":
        try:
            selections = collect_core_setup_selections(
                default_config_path=args.config,
                default_vault_path=args.vault,
            )
        except WizardCancelled as error:
            print(str(error))
            return 130
        if args.dry_run:
            print(write_core_setup_plan(selections))
            return 0
        try:
            report = apply_core_setup(selections)
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as error:
            print(f"Setup could not continue: {error}", file=sys.stderr)
            return 1
        print(render_core_setup_report(report))
        return 0
    if args.command == "connect":
        hosts = (
            tuple(cast(HostName, host) for host in args.host)
            if args.host
            else collect_optional_hosts(ask_to_connect=False)
        )
        _print_json(
            configure_hosts(
                hosts,
                project_dir=args.project_dir,
                config_path=args.config,
                server_command=args.server_command,
                write=not args.dry_run,
            )
        )
        return 0
    if args.command == "discord":
        try:
            if args.discord_command == "configure":
                discord_setup_report = configure_discord_interactive(
                    config_path=args.config,
                    default_project_dir=args.project_dir,
                    advanced=args.advanced,
                )
                if args.json:
                    _print_json(discord_setup_report.as_json())
                else:
                    print(render_discord_setup_report(discord_setup_report))
                return 0
            if args.discord_command == "run":
                return run_discord_bridge(args.config)
            if args.discord_command == "connect":
                discord_runtime_report = connect_discord_bridge(args.config)
            elif args.discord_command == "start":
                discord_runtime_report = start_discord_bridge(args.config)
            elif args.discord_command == "stop":
                discord_runtime_report = stop_discord_bridge(args.config)
            elif args.discord_command == "set-token":
                token = getpass.getpass(
                    "Discord bot token (stored only in the OS credential store): "
                )
                store_discord_token(args.config, token)
                if args.json:
                    _print_json({"stored": "OS credential store"})
                else:
                    print("Discord token updated securely. Start Erga with `erga discord start`.")
                return 0
            else:
                discord_runtime_report = discord_status(args.config)
            if args.json:
                _print_json(discord_runtime_report)
            else:
                print(
                    _render_discord_runtime(
                        discord_runtime_report,
                        action=args.discord_command,
                    )
                )
            return 0
        except WizardCancelled as error:
            print(str(error))
            return 130
        except (
            FileNotFoundError,
            NotADirectoryError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            print(f"Optional Discord bridge failed: {error}", file=sys.stderr)
            return 1
    if args.command == "zoho" and args.zoho_command == "set-client-secret":
        secret = getpass.getpass(
            "Zoho OAuth client secret (stored only in the OS credential store): "
        )
        store_client_secret(args.client_id, secret)
        _print_json({"client_id": args.client_id, "stored": "OS credential store"})
        return 0
    if args.command == "zoho" and args.zoho_command == "connect":
        tokens = connect(
            accounts_url=args.accounts_url,
            client_id=args.client_id,
            client_secret=read_client_secret(args.client_id),
        )
        _print_json(
            {
                "client_id": args.client_id,
                "connected": True,
                "refresh_token_stored": bool(tokens.get("refresh_token")),
            }
        )
        return 0

    if args.command == "doctor":
        _print_json(asdict(check_installation(args.config)))
        return 0
    if args.command == "keryx":
        try:
            if args.keryx_command == "enable":
                _print_json(enable_keryx(args.config).as_json())
            elif args.keryx_command == "disable":
                _print_json(disable_keryx(args.config).as_json())
            elif args.keryx_command == "sync":
                _print_json(sync_keryx(load_config(args.config)).as_json())
            elif args.keryx_command == "status":
                _print_json(keryx_status(load_config(args.config)).as_json())
            else:
                _print_json(
                    search_keryx_jobs(
                        load_config(args.config),
                        query=args.query,
                        program=args.program,
                        cycle=args.cycle,
                        location=args.location,
                        limit=args.limit,
                    )
                )
            return 0
        except (FileNotFoundError, OSError, ValueError) as error:
            print(f"Optional Keryx integration failed: {error}", file=sys.stderr)
            return 1
    if args.command == "mail" and args.mail_command == "configure":
        configured = update_mail_settings(
            args.config,
            {
                "provider": args.provider,
                "gws_command": args.gws_command,
                "client_id": args.client_id,
                "accounts_url": args.accounts_url,
                "folder": args.folder,
            },
        )
        _print_json(mail_settings_as_json(configured))
        return 0
    if args.command == "monitor" and args.monitor_command == "install-hermes-scripts":
        _print_json(
            install_hermes_monitor_scripts(
                config_path=args.config,
                scripts_dir=args.scripts_dir,
                history_days=args.history_days,
                replace=args.replace,
            )
        )
        return 0
    if args.command == "monitor" and args.monitor_command == "install-hermes-update":
        _print_json(
            install_hermes_update_script(
                config_path=args.config,
                scripts_dir=args.scripts_dir,
                replace=args.replace,
            )
        )
        return 0

    store = _store_for(args.config)
    if args.command == "onboarding":
        config = load_config(args.config)
        if args.onboarding_command == "status":
            card = build_onboarding_card(config, store)
            if args.json:
                _print_json(card.as_dict())
            else:
                print(_render_human_status(config, store))
            return 0
        if args.onboarding_command == "skills":
            action = args.onboarding_skill_command
            if action == "set":
                skill_results = store.set_skill_seeds(parse_skill_seed_csv(args.csv))
            elif action == "add":
                store.add_skill_seed(args.skill)
                skill_results = store.list_skill_seeds()
            elif action == "check":
                _print_json(asdict(store.set_skill_seed_checked(args.skill, checked=True)))
                return 0
            elif action == "uncheck":
                _print_json(asdict(store.set_skill_seed_checked(args.skill, checked=False)))
                return 0
            elif action == "remove":
                store.remove_skill_seed(args.skill)
                skill_results = store.list_skill_seeds()
            else:
                skill_results = store.list_skill_seeds()
            _print_json([asdict(item) for item in skill_results])
            return 0
        roots = list(config.portfolio_roots)
        if args.onboarding_root_command == "add":
            roots.append(args.root)
            roots = list(update_portfolio_roots(args.config, roots))
        elif args.onboarding_root_command == "remove":
            target = args.root.expanduser().absolute()
            if target.is_symlink() or not target.is_dir():
                raise ValueError(f"portfolio root must be an existing directory: {target}")
            resolved = target.resolve(strict=True)
            if resolved not in roots:
                raise ValueError("portfolio root is not configured")
            roots.remove(resolved)
            roots = list(update_portfolio_roots(args.config, roots))
        _print_json([str(root) for root in roots])
        return 0
    if args.command == "settings":
        config = load_config(args.config)
        card = build_settings_card(config, store)
        if args.json:
            _print_json(card.as_dict())
        else:
            print(_render_human_status(config, store))
        return 0
    if args.command == "status":
        config = load_config(args.config)
        counts = {
            "applications": len(store.list_applications()),
            "audit_events": len(store.audit_events()),
            "evidence": len(store.list_evidence()),
            "mail_events": len(store.list_mail_events()),
        }
        if args.json:
            _print_json(counts)
        else:
            print(_render_human_status(config, store))
        return 0
    if args.command == "review":
        readiness = _resume_readiness(load_config(args.config), store)
        if args.json:
            _print_json(readiness)
        else:
            print(_render_resume_readiness(readiness))
        return 0
    if args.command == "tracker" and args.tracker_command is None:
        applications = store.list_applications()
        if not applications:
            print(
                "No applications tracked yet.\n\n"
                "Add one with:\n"
                "  erga applications add --company NAME --role ROLE --source-url URL"
            )
        else:
            print(f"Applications tracked: {len(applications)}")
            for application in applications:
                print(f"  • {application.company} — {application.role} ({application.status})")
        return 0
    if args.command == "tracker" and args.tracker_command == "orbit":
        config = load_config(args.config)
        output_dir = config.data_dir / "orbit"
        output_dir.mkdir(parents=True, exist_ok=True)
        restrict_private_directory(output_dir)
        artifact = create_orbit_artifact(
            applications=store.list_applications(),
            audit_events=store.audit_events(),
            output_dir=output_dir,
            tracker_dir=(
                config.tracker.tracker_dir
                if config.tracker.enabled and config.tracker.tracker_dir is not None
                else None
            ),
            cycle=args.cycle,
        )
        image_path = artifact.image_path
        if args.output is not None:
            image_path = render_orbit_png(artifact.snapshot, args.output)
        restrict_private_file(image_path)
        _print_json(
            {
                "image_path": str(image_path),
                "message": artifact.message,
                "model_api_used": False,
                "snapshot": artifact.snapshot.as_dict(),
            }
        )
        return 0
    if args.command == "notes":
        config = load_config(args.config)
        application = _notes_application(args.query, store.list_applications())
        package_dir = _package_for_application(config.resume.output_root, application)
        print(_render_application_notes(application, package_dir))
        return 0
    if args.command == "research":
        config = load_config(args.config)
        application = _notes_application(args.query, store.list_applications())
        package_dir = _package_for_application(config.resume.output_root, application)
        if package_dir is None:
            package = create_job_package(
                output_root=config.resume.output_root,
                cycle="unsorted",
                application_slug=slug_with_identifier(
                    f"{application.company}-{application.role}",
                    posting_identifier(application.source_url),
                ),
                job_url=application.source_url,
            )
            package_dir = package.package_dir
        result = discover_job_research(application=application, package_dir=package_dir)
        lead_word = "lead" if result.outreach_leads == 1 else "leads"
        print(
            f"Research saved: {result.path}\n"
            f"{result.sources_scraped} sources scraped; {result.outreach_leads} public outreach "
            f"{lead_word}. No messages were sent."
        )
        return 0
    if args.command == "tokens":
        _print_json(store.token_usage_summary(application_id=args.application_id))
        return 0
    if args.command == "git":
        if args.git_command == "projects":
            config = load_config(args.config)
            if args.refresh:
                discover_github_projects(
                    cache_path=config.data_dir / "github-project-catalogue.json"
                )
            catalogue = build_project_catalogue(
                config,
                store,
                page=args.page,
                page_size=args.page_size,
                query=args.query,
            )
            if args.json:
                _print_json(catalogue.as_dict())
            else:
                print(catalogue.card.as_text())
            return 0
        if args.git_command == "skills":
            action = args.git_skill_command
            if action == "show":
                seed_override = (
                    parse_skill_seed_csv(args.seed_csv) if args.seed_csv is not None else ()
                )
                card = build_git_skill_review_card(
                    store,
                    page=args.page,
                    page_size=args.page_size,
                    source_filter=args.source_filter,
                    seed_override=seed_override,
                )
                if args.json:
                    _print_json(card.as_dict())
                else:
                    print(card.as_text())
                return 0
            if action == "approve":
                approved = approve_git_skill_group(store, args.skill)
                _print_json(
                    {
                        "approved_evidence_count": len(approved),
                        "evidence": [asdict(item) for item in approved],
                        "resume_changed": False,
                    }
                )
                return 0
            key = args.skill.strip().casefold()
            if not any(
                group.normalized_skill == key
                for group in reconcile_git_skill_groups(store, include_skipped=True)
            ):
                raise ValueError("git skill group does not exist")
            store.set_git_skill_group_skipped(key, skipped=action == "skip")
            _print_json(
                {
                    "skill": key,
                    "skipped": action == "skip",
                    "evidence_approved": False,
                    "resume_changed": False,
                }
            )
            return 0
        if args.git_command == "candidates":
            _print_json([asdict(candidate) for candidate in store.list_git_candidates()])
            return 0
        if args.git_command == "manual-add":
            _print_json(
                asdict(
                    store.add_manual_git_research_draft(
                        title=args.title, description=args.description
                    )
                )
            )
            return 0
        if args.git_command == "review":
            if args.action == "show" and args.draft_id is not None:
                raise ValueError("git review show does not accept a draft ID")
            if args.action != "show" and not args.draft_id:
                raise ValueError(f"git review {args.action} requires a draft ID")
            if args.action == "edit" and (not args.title or not args.description):
                raise ValueError("git review edit requires --title and --description")
            if args.action != "edit" and (args.title is not None or args.description is not None):
                raise ValueError("--title and --description are only valid with git review edit")
            draft, position, total = store.review_git_research_draft(
                action=args.action,
                draft_id=args.draft_id,
                title=args.title,
                description=args.description,
            )
            _print_json(
                {
                    "draft": asdict(draft),
                    "position": position,
                    "total": total,
                    "evidence_approved": False,
                    "resume_changed": False,
                }
            )
            return 0
        if args.git_command == "research":
            if not args.all:
                _print_json([asdict(draft) for draft in store.list_git_research_drafts()])
                return 0
            if not args.root:
                raise ValueError("git research --all requires at least one --root")
            repositories = discover_worktrees(args.root)
            observations_created = 0
            drafts: list[dict[str, object]] = []
            research_checkpoints: dict[str, str | None] = {}
            for repo in repositories:
                repo_path = str(repo)
                commits, checkpoint = scan_commits(repo, store.git_scan_checkpoint(repo_path))
                candidates = store.list_git_candidates(repo_path=repo_path)
                observations = store.list_git_change_observations(repo_path=repo_path)
                missing = commits_missing_observations(
                    repo, candidates, {item.commit_sha for item in observations}
                )
                for observation in analyze_commits(repo, [*commits, *missing]):
                    observations_created += store.save_git_change_observation(observation)
                summary, bullets = synthesize_diff_research(
                    repo_path,
                    store.list_git_change_observations(repo_path=repo_path),
                    candidates,
                )
                draft = store.save_git_research_draft(
                    repo_path=repo_path,
                    summary=summary,
                    bullet_candidates=bullets,
                    generated_from_git_diffs=True,
                )
                drafts.append({**asdict(draft), "auto_approved": False})
                if checkpoint is not None:
                    store.save_git_scan_checkpoint(repo_path=repo_path, commit_sha=checkpoint)
                research_checkpoints[repo_path] = checkpoint
            _print_json(
                {
                    "repositories_scanned": len(repositories),
                    "observations_created": observations_created,
                    "research_drafts": len(drafts),
                    "drafts": drafts,
                    "checkpoints": research_checkpoints,
                    "auto_approved": False,
                }
            )
            return 0
        if args.git_command == "approve":
            _print_json(asdict(store.approve_git_candidate(args.candidate_id)))
            return 0
        if args.root and not args.all:
            raise ValueError("git scan --root requires --all")
        config = load_config(args.config)
        scan_roots = list(args.root) if args.all else []
        if args.configured_roots:
            scan_roots.extend(config.portfolio_roots)
        repositories = [validate_worktree(args.repo)] if args.repo is not None else []
        if scan_roots:
            repositories.extend(discover_worktrees(scan_roots))
        repositories = list(dict.fromkeys(repositories))
        if not repositories:
            raise ValueError(
                "git scan requires a repository path, --all with --root, or --configured-roots"
            )
        review_seed_override = (
            parse_skill_seed_csv(args.seed_csv) if args.seed_csv is not None else ()
        )
        created = 0
        checkpoints: dict[str, str | None] = {}
        previous_checkpoints: dict[str, str | None] = {}
        research_drafts = []
        for repo in repositories:
            repo_path = str(repo)
            previous_checkpoint = store.git_scan_checkpoint(repo_path)
            previous_checkpoints[repo_path] = previous_checkpoint
            commits, checkpoint = scan_commits(repo, previous_checkpoint)
            for commit in commits:
                commit_range = (
                    f"{commit.parents[0]}..{commit.sha}" if commit.parents else commit.sha
                )
                candidate = store.add_git_candidate(
                    repo_path=repo_path,
                    commit_sha=commit.sha,
                    commit_range=commit_range,
                    text=(
                        f"Git commit: {commit.subject}\n"
                        f"Changed files: {', '.join(commit.files[:10])}"
                    ),
                )
                created += candidate is not None
            summary, bullets = synthesize_project_research(
                repo_path, store.list_git_candidates(repo_path=repo_path)
            )
            research_drafts.append(
                store.save_git_research_draft(
                    repo_path=repo_path, summary=summary, bullet_candidates=bullets
                )
            )
            if checkpoint is not None:
                store.save_git_scan_checkpoint(repo_path=repo_path, commit_sha=checkpoint)
            checkpoints[repo_path] = checkpoint
        payload: dict[str, object] = {
            "checkpoints": checkpoints,
            "created": created,
            "repositories_scanned": len(repositories),
            "research_drafts": len(research_drafts),
            "review_seed_override": list(review_seed_override),
        }
        if len(repositories) == 1:
            repo_path = str(repositories[0])
            payload.update(
                {
                    "checkpoint": checkpoints[repo_path],
                    "previous_checkpoint": previous_checkpoints[repo_path],
                    "repo_path": repo_path,
                }
            )
        _print_json(payload)
        return 0
    if args.command == "evidence" and args.evidence_command == "add":
        evidence = store.add_evidence(
            source_ref=args.source_ref, text=args.text, approved=args.approved
        )
        _print_json(asdict(evidence))
        return 0
    if args.command == "obsidian" and args.obsidian_command == "import":
        config = load_config(args.config)
        if config.vault_path is None:
            raise ValueError("vault_path must be configured before importing Obsidian evidence")
        imported = [
            store.add_evidence(source_ref=item.source_ref, text=item.text, approved=False)
            for item in import_markdown_evidence(config.vault_path, args.note)
        ]
        _print_json([asdict(item) for item in imported])
        return 0
    if args.command == "mail" and args.mail_command == "sync":
        if args.limit < 1 or args.limit > 100:
            raise ValueError("--limit must be between 1 and 100")
        config = load_config(args.config)
        messages = build_mail_provider(config).fetch_inbox_metadata(
            page_size=args.limit,
            max_messages=args.limit,
            include_content=config.mail_provider != "gmail",
        )
        sync_result = sync_metadata(store, messages)
        tracker_updates = 0
        if config.tracker.enabled and config.tracker.tracker_dir is not None:
            tracker_updates = reconcile_application_status_tracker_rows(
                tracker_dir=config.tracker.tracker_dir,
                applications=store.list_applications(),
            )
        contacts_projected = project_recruiter_contacts(
            store.list_recruiter_contacts(), config.contact_outputs
        )
        sync_result_payload = {
            "provider": config.mail_provider,
            "fetched": len(messages),
            "contacts_projected": contacts_projected,
            "tracker_updates": tracker_updates,
            **sync_result,
        }
        if args.notify:
            alerts = sync_result["alerts"]
            assert isinstance(alerts, list)
            notification = format_recruiting_alerts(alerts)
            if notification:
                print(notification)
        else:
            _print_json(sync_result_payload)
        return 0
    if args.command == "mail" and args.mail_command == "history":
        print(render_history_digest(store, days=args.days))
        return 0
    if args.command == "zoho" and args.zoho_command == "sync":
        if args.limit < 1 or args.limit > 100:
            raise ValueError("--limit must be between 1 and 100")
        messages = fetch_inbox_metadata(
            access_token=refresh_access_token(client_id=args.client_id), limit=args.limit
        )
        _print_json({"fetched": len(messages), **sync_metadata(store, messages)})
        return 0
    if args.command == "zoho" and args.zoho_command == "ingest-fixture":
        _print_json({"created": ingest_fixture(store, args.fixture)})
        return 0
    if args.command == "resume" and args.resume_command == "settings":
        if args.resume_settings_command == "show":
            _print_json(resume_settings_as_json(load_config(args.config).resume))
            return 0
        updates = {
            "template_path": args.template_path,
            "editable_sections": args.editable_section,
            "bullet_min_chars": args.bullet_min_chars,
            "bullet_target_chars": args.bullet_target_chars,
            "bullet_max_chars": args.bullet_max_chars,
            "single_line_bullets": args.single_line_bullets,
            "experience_tailoring": args.experience_tailoring,
            "experience_inventory_path": args.experience_inventory_path,
            "max_pages": args.max_pages,
            "experience_min_bullets": args.experience_min_bullets,
            "experience_max_bullets": args.experience_max_bullets,
            "project_min_bullets": args.project_min_bullets,
            "project_max_bullets": args.project_max_bullets,
            "project_count": args.project_count,
            "minimum_page_fill_ratio": args.minimum_page_fill_ratio,
            "require_unique_lead_verbs": args.require_unique_lead_verbs,
            "output_root": args.output_root,
            "output_pdf_name": args.output_pdf_name,
            "latexmk": args.latexmk,
        }
        if (
            args.experience_tailoring is None
            and args.editable_section
            and any(section.casefold() == "experience" for section in args.editable_section)
        ):
            updates["experience_tailoring"] = True
        _print_json(resume_settings_as_json(update_settings(args.config, updates)))
        return 0
    if args.command == "resume" and args.resume_command == "experience":
        config = load_config(args.config)
        inventory_path = config.resume.experience_inventory_path
        if inventory_path is None:
            raise ValueError(
                "experience storage is not configured; run `erga setup` once to create it"
            )
        if args.resume_experience_command == "list":
            experience_records = load_experience_inventory(inventory_path, store.list_evidence())
            _print_json(
                [
                    {
                        "id": item.id,
                        "role": item.title,
                        "company": item.company,
                        "bullet_count": len(item.bullets),
                    }
                    for item in experience_records
                ]
            )
            return 0
        evidence = store.add_evidence(
            source_ref=f"user:experience/{args.company.strip()}/{args.role.strip()}",
            text=" ".join(args.bullet.split()),
            approved=True,
        )
        candidate_id, bullet_count = add_user_experience_bullet(
            inventory_path,
            title=args.role,
            company=args.company,
            text=args.bullet,
            evidence_id=evidence.id,
            tags=args.tag,
        )
        restrict_private_file(inventory_path)
        # Re-read through the strict evidence/metric validator before reporting success.
        load_experience_inventory(inventory_path, store.list_evidence())
        _print_json(
            {
                "role_id": candidate_id,
                "bullet_count": bullet_count,
                "experience_tailoring": config.resume.experience_tailoring,
                "next": (
                    "Experience tailoring is ready."
                    if config.resume.experience_tailoring
                    else "Enable it with `erga resume settings set --experience-tailoring`."
                ),
            }
        )
        return 0
    if args.command == "resume" and args.resume_command == "sources":
        config = load_config(args.config)
        if args.resume_sources_command == "context":
            if config.resume.master_path is None:
                raise ValueError("import a master resume before requesting source context")
            _print_json(
                resume_source_context(
                    master_path=config.resume.master_path,
                    reference_path=config.resume.reference_path,
                    template_path=config.resume.template_path,
                )
            )
            return 0
        original_master_name = args.master.expanduser().name
        master = snapshot_resume_source(
            load_resume_source(args.master),
            data_dir=config.data_dir,
            role="master",
        )
        style_source = (
            snapshot_resume_source(
                load_resume_source(args.style),
                data_dir=config.data_dir,
                role="style",
            )
            if args.style is not None
            else None
        )
        evidence = import_master_resume(
            store,
            master,
            source_name=original_master_name,
        )
        settings = update_settings(
            args.config,
            {
                "master_path": str(master.path),
                "reference_path": str(style_source.path) if style_source is not None else "",
                "template_path": "",
            },
        )
        template_path = ensure_resume_template(args.config)
        _print_json(
            {
                "evidence_id": evidence.id,
                "master_path": str(settings.master_path),
                "style_path": str(settings.reference_path) if settings.reference_path else None,
                "template_path": str(template_path),
            }
        )
        return 0
    if args.command == "resume" and args.resume_command == "master":
        config = load_config(args.config)
        original_master_name = args.source.expanduser().name
        master = snapshot_resume_source(
            load_resume_source(args.source),
            data_dir=config.data_dir,
            role="master",
        )
        evidence = import_master_resume(
            store,
            master,
            source_name=original_master_name,
        )
        updates = {
            "master_path": str(master.path),
            "template_path": "",
        }
        if master.format == "tex":
            # A real LaTeX master owns both facts and presentation. Retaining an
            # older style override here silently routes it through Erga's generic
            # reconstruction and defeats the user's explicit master replacement.
            updates["reference_path"] = ""
        settings = update_settings(args.config, updates)
        template_path = ensure_resume_template(args.config)
        _print_json(
            {
                "evidence_id": evidence.id,
                "master_path": str(settings.master_path),
                "style_path": (str(settings.reference_path) if settings.reference_path else None),
                "template_path": str(template_path),
            }
        )
        return 0
    if args.command == "resume" and args.resume_command == "template":
        if args.resume_template_command == "reset":
            master_path = load_config(args.config).resume.master_path
            template_path = reset_resume_template(args.config)
            _print_json(
                {
                    "master_path": str(master_path),
                    "reset": True,
                    "style_path": None,
                    "template_path": str(template_path),
                }
            )
            return 0
        if args.resume_template_command == "set":
            config = load_config(args.config)
            if config.resume.master_path is None or not config.resume.master_path.is_file():
                raise ValueError("set a master resume before setting a visual template")
            style_source = snapshot_resume_source(
                load_resume_source(args.source),
                data_dir=config.data_dir,
                role="style",
            )
            settings = update_settings(
                args.config,
                {
                    "reference_path": str(style_source.path),
                    "template_path": "",
                },
            )
            template_path = ensure_resume_template(args.config)
            _print_json(
                {
                    "master_path": str(settings.master_path),
                    "style_path": str(settings.reference_path),
                    "template_path": str(template_path),
                }
            )
            return 0
        template_path = ensure_resume_template(args.config)
        _print_json({"generated_or_reused": True, "template_path": str(template_path)})
        return 0
    if args.command == "resume" and args.resume_command == "create-package":
        package = create_job_package(
            output_root=load_config(args.config).resume.output_root,
            cycle=args.cycle,
            application_slug=args.application_slug,
            job_url=args.job_url,
        )
        _print_json(asdict(package))
        return 0
    if args.command == "resume" and args.resume_command == "tailor":
        settings = load_config(args.config).resume
        if settings.template_path is None:
            ensure_resume_template(args.config)
            settings = load_config(args.config).resume
        if settings.template_path is None:
            raise ValueError("resume template could not be generated from the approved master")
        if args.section.casefold() not in {item.casefold() for item in settings.editable_sections}:
            raise ValueError("requested section is not configured as editable")
        proposal = create_section_resume_proposal(
            resume_path=settings.template_path,
            output_dir=args.output_dir,
            section_name=args.section,
            latex_content=args.latex_content,
            evidence=store.approved_evidence(args.evidence_id),
            bullet_min_chars=settings.bullet_min_chars,
            bullet_target_chars=settings.bullet_target_chars,
            bullet_max_chars=settings.bullet_max_chars,
        )
        _print_json(asdict(proposal))
        return 0
    if args.command == "resume" and args.resume_command == "tailor-job":
        _tailor_progress(friendly_tailor, "Reading the job description…")
        config = load_config(args.config)
        settings = config.resume
        if settings.template_path is None and settings.master_path is None:
            raise ValueError(
                "Before Erga can tailor a résumé, add the résumé you want it to use with "
                "`erga setup`. Erga copies it into private local storage and never changes "
                "the original."
            )
        if settings.template_path is None:
            ensure_resume_template(args.config)
            settings = load_config(args.config).resume
        if settings.template_path is None:
            raise ValueError("resume template could not be generated from the approved master")
        snapshot, job_source = _tailor_job_input(args)
        research = analyze_job_snapshot(snapshot, job_url=job_source)
        if args.company or args.role:
            research = replace(
                research,
                company=(args.company or research.company).strip(),
                role=(args.role or research.role).strip(),
            )
        limits = _tailor_limits(args, settings)
        output_dir = args.output_dir or (
            settings.output_root
            / "tailored"
            / slug_with_identifier(
                f"{research.company}-{research.role}-{posting_identifier(job_source)}",
                secrets.token_hex(4),
            )
        )
        _tailor_progress(friendly_tailor, "Matching approved evidence and projects…")
        resume_approved = tuple(item for item in store.list_evidence() if item.approved)
        selected = select_relevant_evidence(
            snapshot,
            resume_approved,
            role_profile=research.role_profile,
        )
        tailoring_context = "\n".join(
            (
                research.company,
                research.role,
                *research.highlights,
                *research.responsibilities,
                *research.qualifications,
                *research.skills,
                official_job_text(snapshot),
            )
        )
        resume_candidates = (
            load_project_inventory(settings.project_inventory_path, resume_approved)
            if settings.project_inventory_path is not None
            else ()
        )
        experience_candidates = (
            load_experience_inventory(settings.experience_inventory_path, resume_approved)
            if cast(bool, limits["experience_tailoring"])
            and settings.experience_inventory_path is not None
            else ()
        )
        _tailor_progress(friendly_tailor, "Building the strongest supported one-page draft…")
        automatic = create_automatic_resume_proposal(
            resume_path=settings.template_path,
            output_dir=output_dir,
            job_description=tailoring_context,
            evidence=list(resume_approved),
            editable_sections=settings.editable_sections,
            bullet_min_chars=settings.bullet_min_chars,
            bullet_target_chars=settings.bullet_target_chars,
            bullet_max_chars=settings.bullet_max_chars,
            project_candidates=resume_candidates,
            project_count=cast(int, limits["project_count"]),
            experience_candidates=experience_candidates,
            experience_tailoring=cast(bool, limits["experience_tailoring"]),
            experience_min_bullets=cast(int, limits["experience_min_bullets"]),
            experience_max_bullets=cast(int, limits["experience_max_bullets"]),
            project_min_bullets=cast(int, limits["project_min_bullets"]),
            project_max_bullets=cast(int, limits["project_max_bullets"]),
            require_unique_lead_verbs=settings.require_unique_lead_verbs,
            minimum_page_fill_ratio=cast(float, limits["minimum_page_fill_ratio"]),
            max_pages=cast(int, limits["max_pages"]),
        )
        validation: dict[str, object] | None = None
        if args.validate:
            _tailor_progress(friendly_tailor, "Checking pages, fill, spacing, and bullet layout…")
            validation = validate_resume_render(
                automatic.proposal.proposed_tex_path,
                latexmk=Path(settings.latexmk),
                output_pdf_name=settings.output_pdf_name,
                max_pages=cast(int, limits["max_pages"]),
                minimum_page_fill_ratio=(
                    cast(float, limits["minimum_page_fill_ratio"])
                    if cast(int, limits["max_pages"]) == 1
                    else 0
                ),
                compiler=validate_latex_proposal,
            ).as_dict()
            quality_path = automatic.proposal.proposed_tex_path.with_name(
                "application-quality.json"
            )
            quality_path.write_text(
                json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            restrict_private_file(quality_path)
        tailoring_result = {
            **asdict(automatic.proposal),
            "company": research.company,
            "role": research.role,
            "job_source": job_source,
            "preset": limits["preset"],
            "resolved_limits": limits,
            "selected_evidence_ids": [item.id for item in selected],
            "meaningful_change": automatic.meaningful_change,
            "changed_sections": list(automatic.changed_sections),
            "experience_selection": automatic.experience_selection,
            "fallback_reason": automatic.fallback_reason,
            "role_profile": (
                research.role_profile.as_dict() if research.role_profile is not None else None
            ),
            "validation": validation,
        }
        if friendly_tailor:
            validation_ok = isinstance(validation, dict) and validation.get("passed") is True
            validation_reason = validation.get("reason") if isinstance(validation, dict) else None
            validation_pdf = validation.get("pdf") if isinstance(validation, dict) else None
            project_titles = automatic.project_selection.get("selected_titles", [])
            selected_projects = (
                ", ".join(str(item) for item in project_titles)
                if isinstance(project_titles, list) and project_titles
                else "master projects retained"
            )
            print(
                "\n".join(
                    (
                        (
                            "Résumé is application-ready."
                            if validation_ok
                            else "Résumé draft needs one more review."
                        ),
                        f"Job: {research.role} at {research.company}",
                        f"Draft: {automatic.proposal.proposed_tex_path}",
                        (
                            f"PDF: {validation_pdf}"
                            if validation_ok
                            else f"Quality check: {validation_reason or 'not run'}"
                        ),
                        f"Changes: {', '.join(automatic.changed_sections) or 'none needed'}",
                        f"Projects: {selected_projects}",
                        f"Preset: {limits['preset']} · max {limits['max_pages']} page(s)",
                        "Nothing was sent or submitted.",
                    )
                )
            )
        else:
            _print_json(tailoring_result)
        return 0
    if args.command == "resume" and args.resume_command == "insights":
        config = load_config(args.config)
        _print_json(
            build_resume_outcome_report(
                applications=store.list_applications(),
                output_root=config.resume.output_root,
            )
        )
        return 0
    if args.command == "resume" and args.resume_command == "propose":
        proposal = create_resume_proposal(
            resume_path=args.resume,
            output_dir=args.output_dir,
            latex_snippet=args.latex_snippet,
            evidence=store.approved_evidence(args.evidence_id),
        )
        _print_json(asdict(proposal))
        return 0
    if args.command == "resume" and args.resume_command == "validate":
        _print_json(asdict(validate_latex_proposal(args.proposal, latexmk=args.latexmk)))
        return 0
    if args.command == "cover-letter":
        if args.cover_letter_command == "settings":
            if args.cover_letter_settings_command == "show":
                _print_json(cover_letter_settings_as_json(load_config(args.config).cover_letter))
                return 0
            _print_json(
                cover_letter_settings_as_json(
                    update_cover_letter_settings(
                        args.config,
                        {
                            "template_path": args.template_path,
                            "writing_sample_path": args.writing_sample_path,
                        },
                    )
                )
            )
            return 0
        cover_letter_settings = load_config(args.config).cover_letter
        if (
            cover_letter_settings.template_path is None
            or cover_letter_settings.writing_sample_path is None
        ):
            raise ValueError(
                "cover_letter template_path and writing_sample_path must be configured"
            )
        if args.cover_letter_command == "context":
            style = load_style_context(cover_letter_settings.writing_sample_path)
            _print_json(
                {
                    "template": cover_letter_settings.template_path.read_text(encoding="utf-8"),
                    "template_path": str(cover_letter_settings.template_path),
                    "writing_sample": style.text,
                    "writing_sample_is_style_only": True,
                    "writing_sample_path": str(style.source_path),
                    "writing_sample_sha256": style.sha256,
                }
            )
            return 0
        _print_json(
            asdict(
                create_cover_letter_proposal(
                    template_path=cover_letter_settings.template_path,
                    writing_sample_path=cover_letter_settings.writing_sample_path,
                    output_dir=args.output_dir,
                    body=(
                        args.body_file.expanduser().read_text(encoding="utf-8")
                        if args.body_file is not None
                        else args.body
                    ),
                    evidence=store.approved_evidence(args.evidence_id),
                )
            )
        )
        return 0
    if args.command == "applications":
        if args.applications_command == "add":
            application = store.create_application(
                company=args.company,
                role=args.role,
                source_url=args.source_url,
                evidence_ids=args.evidence_id,
            )
            if store.list_mail_events():
                reconcile_mail_events(store, store.list_mail_events())
            _print_json(asdict(application))
            return 0
        if args.applications_command == "update-status":
            _print_json(
                asdict(store.update_application_status(args.application_id, status=args.status))
            )
            return 0
        _print_json([asdict(application) for application in store.list_applications()])
        return 0
    if args.command == "export":
        config = load_config(args.config)
        _print_json(
            export_bundle(
                store=store,
                output_root=config.resume.output_root,
                destination=args.output,
                resume_assets={
                    "master"
                    + (
                        config.resume.master_path.suffix if config.resume.master_path else ""
                    ): config.resume.master_path,
                    "project-inventory.json": config.resume.project_inventory_path,
                    "experience-inventory.json": config.resume.experience_inventory_path,
                },
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def _run_console(arguments: Sequence[str] | None = None) -> int:
    """Render expected operator errors without exposing an internal traceback."""
    try:
        return main(arguments)
    except (
        FileNotFoundError,
        NotADirectoryError,
        OSError,
        RuntimeError,
        ValueError,
    ) as error:
        if isinstance(error, FileNotFoundError) and "config.toml" in str(error):
            print(
                "Erga is not set up on this computer yet. Run `erga setup` to add your résumé "
                "and create the private workspace.",
                file=sys.stderr,
            )
        else:
            print(f"Erga could not complete the command: {error}", file=sys.stderr)
        return 1


def console_main() -> int:
    """Installed ``erga`` entry point."""
    return _run_console()


def tokens_main() -> int:
    """Entry point for the ergonomic `erga-tokens` token-report command."""
    return _run_console(["tokens", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(console_main())
