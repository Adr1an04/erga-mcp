from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from .config import DEFAULT_CONFIG, load_config
from .contact_projection import project_recruiter_contacts
from .cover_letter import create_cover_letter_proposal, load_style_context
from .cover_letter_settings import as_json as cover_letter_settings_as_json
from .cover_letter_settings import update_settings as update_cover_letter_settings
from .cron_setup import install_hermes_monitor_scripts
from .discord_bridge import (
    connect_discord_bridge,
    discord_status,
    run_discord_bridge,
    start_discord_bridge,
    stop_discord_bridge,
    store_discord_token,
)
from .discord_setup import (
    collect_optional_discord,
    configure_discord_interactive,
)
from .doctor import check_installation
from .exporting import export_bundle
from .git_evidence import (
    analyze_commits,
    commits_missing_observations,
    discover_worktrees,
    scan_commits,
    synthesize_diff_research,
    synthesize_project_research,
    validate_worktree,
)
from .host_connections import (
    SUPPORTED_HOSTS,
    HostName,
    collect_connection_workspace,
    collect_optional_hosts,
    configure_hosts,
)
from .integrations.mail_provider import build_mail_provider
from .integrations.obsidian import import_markdown_evidence
from .integrations.obsidian_tracker import reconcile_application_status_tracker_rows
from .integrations.zoho import ingest_fixture
from .integrations.zoho_live import (
    fetch_inbox_metadata,
    format_recruiting_alerts,
    sync_metadata,
)
from .job_discovery import discover_job_research
from .mail_settings import as_json as mail_settings_as_json
from .mail_settings import update_settings as update_mail_settings
from .models import Application
from .private_files import restrict_private_file
from .reporting import render_history_digest
from .resume import (
    create_job_package,
    create_resume_proposal,
    create_section_resume_proposal,
    validate_latex_proposal,
)
from .resume_settings import as_json as resume_settings_as_json
from .resume_settings import update_settings
from .resume_sources import (
    import_master_resume,
    load_resume_source,
    resume_source_context,
    snapshot_resume_source,
)
from .resume_template import ensure_resume_template, reset_resume_template
from .setup_wizard import (
    WizardCancelled,
    apply_core_setup,
    collect_core_setup_selections,
    render_core_setup_report,
    write_core_setup_plan,
)
from .store import ErgaStore
from .uninstall import (
    apply_uninstall,
    build_uninstall_plan,
    confirmation_phrase,
    render_uninstall_plan,
)
from .zoho_oauth import (
    connect,
    read_client_secret,
    refresh_access_token,
    store_client_secret,
)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "erga-mcp" / "config.toml"


def _config_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="erga",
        description=(
            "Local-first recruiting workflow tools. No external actions are performed by default."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser(
        "init", help="create a local non-secret configuration and database"
    )
    _config_argument(init)

    setup = subcommands.add_parser(
        "setup",
        help="configure Erga's private career state, resume knowledge, tracking, and MCP core",
    )
    _config_argument(setup)
    setup.add_argument("--vault", type=Path)
    setup.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and review choices without changing local state",
    )

    uninstall = subcommands.add_parser(
        "uninstall",
        help="remove Erga-owned state, credentials, bridges, and recorded MCP connections",
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
        help="optionally connect zero, one, or multiple MCP coding hosts to Erga",
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
        help="optionally configure and run a private Discord bridge",
    )
    discord_commands = discord_bridge.add_subparsers(
        dest="discord_command",
        required=True,
    )
    discord_configure = discord_commands.add_parser(
        "configure",
        help="choose one replaceable coding backend and configure the Discord bridge",
    )
    _config_argument(discord_configure)
    discord_configure.add_argument("--project-dir", type=Path, default=Path.cwd())
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

    status = subcommands.add_parser("status", help="show local pipeline counts")
    _config_argument(status)
    doctor = subcommands.add_parser("doctor", help="check core and optional local capabilities")
    _config_argument(doctor)

    evidence = subcommands.add_parser("evidence", help="manage local evidence records")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_add = evidence_commands.add_parser("add", help="add local career evidence")
    _config_argument(evidence_add)
    evidence_add.add_argument("--source-ref", required=True)
    evidence_add.add_argument("--text", required=True)
    evidence_add.add_argument("--approved", action="store_true")

    git = subcommands.add_parser("git", help="scan local git worktrees for reviewable evidence")
    git_commands = git.add_subparsers(dest="git_command", required=True)
    git_scan = git_commands.add_parser(
        "scan", help="scan bounded new commits into unapproved candidates"
    )
    _config_argument(git_scan)
    git_scan.add_argument("repo", type=Path, nargs="?")
    git_scan.add_argument("--all", action="store_true", help="scan every worktree below --root")
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

    resume = subcommands.add_parser("resume", help="create reviewable local resume proposals")
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
    resume_settings_set.add_argument("--max-pages", type=int)
    resume_settings_set.add_argument("--output-root")
    resume_settings_set.add_argument("--output-pdf-name")
    resume_settings_set.add_argument("--latexmk")
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

    monitor = subcommands.add_parser(
        "monitor", help="prepare deterministic Hermes scheduled-monitor runners"
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
    return 0


def _store_for(config_path: Path) -> ErgaStore:
    config = load_config(config_path)
    store = ErgaStore(config.data_dir / "erga.sqlite3")
    store.initialize()
    return store


def _print_json(value: object) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


def _job_url_identity(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(
        (parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.path, "", "")
    )


def _notes_application(query: str, applications: list[Application]) -> Application:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        raise ValueError("notes query must include a company or role word")
    matches = [
        application
        for application in applications
        if all(term in f"{application.company} {application.role}".casefold() for term in terms)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no tracked application matches: {query}")
    choices = ", ".join(f"{item.company} - {item.role}" for item in matches)
    raise ValueError(f"multiple tracked applications match {query!r}: {choices}")


def _package_for_application(output_root: Path, application: Application) -> Path | None:
    if not output_root.is_dir():
        return None
    identity = _job_url_identity(application.source_url)
    for manifest_path in output_root.glob("*/*/package.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        job_url = manifest.get("job_url") if isinstance(manifest, dict) else None
        if isinstance(job_url, str) and _job_url_identity(job_url) == identity:
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


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
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
        optional_hosts = collect_optional_hosts()
        if optional_hosts:
            try:
                connection_workspace = collect_connection_workspace(default=Path.cwd())
                _print_json(
                    configure_hosts(
                        optional_hosts,
                        project_dir=connection_workspace,
                        config_path=args.config,
                        write=True,
                    )
                )
            except RuntimeError as error:
                print(str(error))
                return 0
            except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as error:
                print(
                    f"Erga's core remains ready, but the optional host connection failed: {error}",
                    file=sys.stderr,
                )
                return 1
        try:
            if collect_optional_discord():
                _print_json(
                    configure_discord_interactive(
                        config_path=args.config,
                        default_project_dir=Path.cwd(),
                    ).as_json()
                )
        except WizardCancelled as error:
            print(str(error))
            return 0
        except (
            FileNotFoundError,
            NotADirectoryError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            print(
                f"Erga's core remains ready, but the optional Discord connection failed: {error}",
                file=sys.stderr,
            )
            return 1
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
                _print_json(
                    configure_discord_interactive(
                        config_path=args.config,
                        default_project_dir=args.project_dir,
                    ).as_json()
                )
                return 0
            if args.discord_command == "run":
                return run_discord_bridge(args.config)
            if args.discord_command == "connect":
                _print_json(connect_discord_bridge(args.config))
            elif args.discord_command == "start":
                _print_json(start_discord_bridge(args.config))
            elif args.discord_command == "stop":
                _print_json(stop_discord_bridge(args.config))
            elif args.discord_command == "set-token":
                token = getpass.getpass(
                    "Discord bot token (stored only in the OS credential store): "
                )
                store_discord_token(args.config, token)
                _print_json({"stored": "OS credential store"})
            else:
                _print_json(discord_status(args.config))
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

    store = _store_for(args.config)
    if args.command == "status":
        _print_json(
            {
                "applications": len(store.list_applications()),
                "audit_events": len(store.audit_events()),
                "evidence": len(store.list_evidence()),
                "mail_events": len(store.list_mail_events()),
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
            raise ValueError(
                "research requires an existing local Erga package for this application"
            )
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
        if bool(args.all) == (args.repo is not None):
            raise ValueError("git scan requires exactly one of a repository path or --all")
        repositories = discover_worktrees(args.root) if args.all else [validate_worktree(args.repo)]
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
            "max_pages": args.max_pages,
            "output_root": args.output_root,
            "output_pdf_name": args.output_pdf_name,
            "latexmk": args.latexmk,
        }
        _print_json(resume_settings_as_json(update_settings(args.config, updates)))
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
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def tokens_main() -> int:
    """Entry point for the ergonomic `erga-tokens` token-report command."""
    return main(["tokens", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
