from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .contact_projection import project_recruiter_contacts
from .cover_letter import create_cover_letter_proposal, load_style_context
from .cron_setup import install_hermes_monitor_scripts
from .doctor import check_installation
from .exporting import export_bundle
from .integrations.gmail_live import fetch_inbox_metadata_with_gws
from .integrations.obsidian import import_markdown_evidence
from .integrations.zoho import ingest_fixture
from .integrations.zoho_live import (
    fetch_inbox_metadata,
    format_recruiting_alerts,
    sync_metadata,
)
from .mail_settings import as_json as mail_settings_as_json
from .mail_settings import update_settings as update_mail_settings
from .reporting import render_history_digest
from .resume import (
    create_job_package,
    create_resume_proposal,
    create_section_resume_proposal,
    validate_latex_proposal,
)
from .resume_settings import as_json as resume_settings_as_json
from .resume_settings import update_settings
from .store import ErgaStore
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
    cover_letter_propose.add_argument("--body", required=True)
    cover_letter_propose.add_argument("--evidence-id", action="append", default=[])

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


def _initialize(config_path: Path) -> int:
    config_path = config_path.expanduser()
    if config_path.exists():
        print(f"Config already exists: {config_path}")
        return 2
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(config_path)
    ErgaStore(config.data_dir / "erga.sqlite3").initialize()
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


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    if args.command == "init":
        return _initialize(args.config)
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
    if args.command == "tokens":
        _print_json(store.token_usage_summary(application_id=args.application_id))
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
        if config.mail_provider == "gmail":
            messages = fetch_inbox_metadata_with_gws(
                gws_command=config.gws_command, limit=args.limit
            )
        else:
            if not config.mail_client_id:
                raise ValueError("mail client_id must be configured before scheduled Zoho sync")
            messages = fetch_inbox_metadata(
                access_token=refresh_access_token(
                    client_id=config.mail_client_id,
                    accounts_url=config.mail_accounts_url,
                ),
                limit=args.limit,
                folder=config.mail_folder,
            )
        sync_result = sync_metadata(store, messages)
        contacts_projected = project_recruiter_contacts(
            store.list_recruiter_contacts(), config.contact_outputs
        )
        result = {
            "provider": config.mail_provider,
            "fetched": len(messages),
            "contacts_projected": contacts_projected,
            **sync_result,
        }
        if args.notify:
            alerts = sync_result["alerts"]
            assert isinstance(alerts, list)
            notification = format_recruiting_alerts(alerts)
            if notification:
                print(notification)
        else:
            _print_json(result)
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
            raise ValueError("resume template_path must be configured before tailoring")
        if args.section.casefold() not in {item.casefold() for item in settings.editable_sections}:
            raise ValueError("requested section is not configured as editable")
        proposal = create_section_resume_proposal(
            resume_path=settings.template_path,
            output_dir=args.output_dir,
            section_name=args.section,
            latex_content=args.latex_content,
            evidence=store.approved_evidence(args.evidence_id),
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
                    body=args.body,
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
