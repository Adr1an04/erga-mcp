from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, cast

from mcp.server.mcpserver import Context
from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitResult, InputRequiredResult
from pydantic import Field, StrictInt

from erga_mcp.config import ErgaConfig, load_config
from erga_mcp.integrations.mail.provider import build_mail_provider
from erga_mcp.integrations.mail.zoho_live import sync_metadata
from erga_mcp.integrations.obsidian.tracker import (
    import_confirmed_application_tracker_rows,
    reconcile_confirmed_application_tracker_rows,
)
from erga_mcp.mcp.profiles import (
    LOCAL_IDEMPOTENT_WRITE,
    LOCAL_WRITE,
    NETWORK_READ_AND_WRITE,
    READ_ONLY,
    profile_visible_evidence,
)
from erga_mcp.mcp.registry import ToolRegistry
from erga_mcp.portfolio.catalogue import build_project_catalogue
from erga_mcp.portfolio.github import discover_github_projects
from erga_mcp.portfolio.metrics import propose_git_project_metrics
from erga_mcp.portfolio.roots import detected_portfolio_root, update_portfolio_roots
from erga_mcp.portfolio.skill_inventory import parse_skill_seed_csv
from erga_mcp.portfolio.skills import (
    approve_git_skill_group,
    explicit_skills_in_texts,
    reconcile_git_skill_groups,
)
from erga_mcp.resumes.sources import resume_source_context as build_resume_source_context
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.contact_projection import project_recruiter_contacts
from erga_mcp.tracking.onboarding import build_onboarding_card

JsonValue = Callable[[object], object]
GitResearchReport = Callable[[ErgaStore, list[str]], dict[str, object]]


def register_workspace_tools(
    registry: ToolRegistry,
    *,
    config: ErgaConfig,
    config_path: Path,
    store: ErgaStore,
    selected_tool_profile: str,
    json_value: JsonValue,
    git_research_report: GitResearchReport,
) -> None:
    """Register project, Git, evidence, mail, and usage tools."""

    @registry.tool(
        "update_application_status",
        title="Update one local application status",
        description=(
            "Set the status of one existing application in Erga's private local database. "
            "Allowed statuses are draft, applied, oa, assessment, interview, interview-2, "
            "interview-3, final-interview, offer, accepted, rejected, and withdrawn. This "
            "records a local audit event when the value changes; it never "
            "contacts an employer, submits an application, or mutates a remote service."
        ),
        annotations=LOCAL_IDEMPOTENT_WRITE,
    )
    def update_application_status(application_id: str, status: str) -> dict[str, object]:
        """Set an existing application's canonical local workflow status."""
        return cast(
            dict[str, object],
            json_value(asdict(store.update_application_status(application_id, status=status))),
        )

    @registry.tool(
        "refresh_project_catalogue",
        title="Refresh and browse the private GitHub project catalogue",
        description=(
            "Use the already-authorized GitHub CLI to refresh owned/collaborator repository "
            "metadata in Erga's private local cache, then return the shared project catalogue. "
            "This never approves evidence or changes a resume."
        ),
        annotations=NETWORK_READ_AND_WRITE,
    )
    def refresh_project_catalogue(
        page: Annotated[StrictInt, Field(ge=1)] = 1,
        page_size: Annotated[StrictInt, Field(ge=1, le=10)] = 6,
        query: str = "",
    ) -> dict[str, object]:
        """Refresh bounded GitHub metadata and reopen the catalogue without evidence writes."""
        current_config = load_config(config_path)
        discovered = discover_github_projects(
            cache_path=current_config.data_dir / "github-project-catalogue.json"
        )
        payload = build_project_catalogue(
            current_config,
            store,
            page=page,
            page_size=page_size,
            query=query,
        ).as_dict()
        payload["github_projects_refreshed"] = len(discovered)
        payload["evidence_created"] = False
        payload["resume_changed"] = False
        return payload

    @registry.tool("update_skill_inventory", annotations=LOCAL_IDEMPOTENT_WRITE)
    def update_skill_inventory(
        operation: str,
        skill: str = "",
        skill_csv: str = "",
    ) -> dict[str, object]:
        """Explicitly manage self-reported review hints without creating résumé evidence."""
        if operation == "set":
            if skill or not skill_csv.strip():
                raise ValueError("set requires skill_csv and does not accept skill")
            store.set_skill_seeds(parse_skill_seed_csv(skill_csv))
        elif operation == "import_approved":
            if skill or skill_csv:
                raise ValueError("import_approved does not accept skill or skill_csv")
            imported = explicit_skills_in_texts(
                item.text for item in store.list_evidence() if item.approved
            )
            if not imported:
                raise ValueError("approved evidence contains no explicit audited skill names")
            for imported_skill in imported:
                store.add_skill_seed(imported_skill, source="approved_evidence")
        elif operation == "add":
            if not skill or skill_csv:
                raise ValueError("add requires skill and does not accept skill_csv")
            store.add_skill_seed(skill)
        elif operation in {"check", "uncheck"}:
            if not skill or skill_csv:
                raise ValueError(f"{operation} requires skill and does not accept skill_csv")
            store.set_skill_seed_checked(skill, checked=operation == "check")
        elif operation == "remove":
            if not skill or skill_csv:
                raise ValueError("remove requires skill and does not accept skill_csv")
            store.remove_skill_seed(skill)
        elif operation != "list" or skill or skill_csv:
            raise ValueError(
                "operation must be set, add, list, check, uncheck, remove, or import_approved"
            )
        return {
            "skills": [asdict(item) for item in store.list_skill_seeds()],
            "evidence_created": False,
            "card": build_onboarding_card(load_config(config_path), store).as_dict(),
        }

    @registry.tool("manage_portfolio_roots", annotations=LOCAL_IDEMPOTENT_WRITE)
    def manage_portfolio_roots(
        operation: str,
        root: str = "",
        roots: list[str] | None = None,
    ) -> dict[str, object]:
        """Manage only explicit existing local roots; never crawl a home directory by default."""
        current = list(load_config(config_path).portfolio_roots)
        if operation == "add":
            if not root or roots is not None:
                raise ValueError("add requires root and does not accept roots")
            current.append(Path(root))
            current = list(update_portfolio_roots(config_path, current))
        elif operation == "add_detected":
            if root or roots is not None:
                raise ValueError("add_detected does not accept root or roots")
            detected = detected_portfolio_root()
            if detected is None:
                raise ValueError(
                    "no conventional local projects folder with Git repositories found"
                )
            current.append(detected)
            current = list(update_portfolio_roots(config_path, current))
        elif operation == "remove":
            if not root or roots is not None:
                raise ValueError("remove requires root and does not accept roots")
            target = Path(root).expanduser().absolute()
            if target.is_symlink() or not target.is_dir():
                raise ValueError(f"portfolio root must be an existing directory: {target}")
            resolved = target.resolve(strict=True)
            if resolved not in current:
                raise ValueError("portfolio root is not configured")
            current.remove(resolved)
            current = list(update_portfolio_roots(config_path, current))
        elif operation == "set":
            if root or roots is None:
                raise ValueError("set requires roots and does not accept root")
            current = list(update_portfolio_roots(config_path, [Path(item) for item in roots]))
        elif operation != "list" or root or roots is not None:
            raise ValueError("operation must be set, add, add_detected, list, or remove")
        return {
            "roots": [str(item) for item in current],
            "card": build_onboarding_card(load_config(config_path), store).as_dict(),
        }

    @registry.tool("review_git_skill_group", annotations=LOCAL_WRITE)
    def review_git_skill_group(operation: str, skill: str) -> dict[str, object]:
        """Inspect, skip, restore, or explicitly approve one Git skill group."""
        matches = [
            item
            for item in reconcile_git_skill_groups(store, include_skipped=True)
            if item.normalized_skill == skill.strip().casefold()
        ]
        if operation == "inspect":
            if not matches:
                raise ValueError("git skill group does not exist")
            return {
                "group": asdict(matches[0]),
                "approved_evidence_count": 0,
                "resume_changed": False,
            }
        if operation in {"skip", "restore"}:
            if not matches:
                raise ValueError("git skill group does not exist")
            store.set_git_skill_group_skipped(skill, skipped=operation == "skip")
            return {
                "group": asdict(matches[0]),
                "skipped": operation == "skip",
                "approved_evidence_count": 0,
                "resume_changed": False,
            }
        if operation != "approve":
            raise ValueError("operation must be inspect, approve, skip, or restore")
        approved = approve_git_skill_group(store, skill)
        return {
            "group": asdict(
                next(
                    item
                    for item in reconcile_git_skill_groups(store)
                    if item.normalized_skill == skill.strip().casefold()
                )
            ),
            "approved_evidence_count": len(approved),
            "evidence": [asdict(item) for item in approved],
            "resume_changed": False,
        }

    @registry.tool("list_evidence", annotations=READ_ONLY)
    def list_evidence() -> list[dict[str, object]]:
        """List evidence records while withholding master-resume text from non-private profiles."""
        evidence_records = profile_visible_evidence(selected_tool_profile, store.list_evidence())
        return [
            cast(dict[str, object], json_value(asdict(evidence))) for evidence in evidence_records
        ]

    @registry.tool("resume_source_context", annotations=READ_ONLY)
    def resume_source_context() -> dict[str, object]:
        """Return approved master knowledge and style-only layout metadata."""
        if config.resume.master_path is None:
            raise ValueError("import a master resume before requesting source context")
        return build_resume_source_context(
            master_path=config.resume.master_path,
            reference_path=config.resume.reference_path,
            template_path=config.resume.template_path,
        )

    @registry.tool("list_mail_events", annotations=READ_ONLY)
    def list_mail_events() -> list[dict[str, object]]:
        """List normalized local mail events; previews and message bodies are not retained."""
        return [
            cast(dict[str, object], json_value(asdict(event))) for event in store.list_mail_events()
        ]

    @registry.tool("token_usage", annotations=READ_ONLY)
    def token_usage(application_id: str = "") -> dict[str, object]:
        """Show recorded input, output, and total model tokens; no cost estimate is made."""
        normalized = application_id.strip()
        return cast(
            dict[str, object],
            store.token_usage_summary(application_id=normalized or None),
        )

    @registry.tool(
        "propose_project_metrics",
        title="Analyze attributable Git-backed project scope",
        description=(
            "Inspect a single explicit local Git worktree and return review-only, "
            "author-attributed engineering context plus deterministic test-case, HTTP-route, "
            "and CLI-command scope from recognized source and test files. Generated assets, "
            "dependencies, locks, snapshots, docs, and data are excluded. Commit, file, language, "
            "and line counts remain internal review facts and are never promoted into resume "
            "metrics or mistaken for product impact."
        ),
        annotations=READ_ONLY,
    )
    def propose_project_metrics(
        repo_path: str, author_email: str, commit_limit: int = 200
    ) -> dict[str, object]:
        """Return confirmation-required Git scope for one repository, not resume claims."""
        proposal = propose_git_project_metrics(
            Path(repo_path), author_email=author_email, commit_limit=commit_limit
        )
        return cast(dict[str, object], json_value(asdict(proposal)))

    @registry.tool(
        "research_git_worktrees",
        title="Research explicit local Git worktrees from diffs",
        description=(
            "Run the unified candidate scan and diff-research pipeline below explicit existing "
            "roots, or below roots saved during onboarding when the list is empty. This tool "
            "never defaults to home-directory scanning, uses no network, returns only "
            "review-required provenance, and never auto-approves evidence or edits a resume."
        ),
        annotations=LOCAL_WRITE,
    )
    def research_git_worktrees(roots: list[str]) -> dict[str, object]:
        """Scan Git candidates and create unapproved diff drafts below configured roots."""
        selected_roots = roots or [str(root) for root in load_config(config_path).portfolio_roots]
        if not selected_roots:
            return {
                "repositories_scanned": 0,
                "candidates_created": 0,
                "observations_created": 0,
                "research_drafts": 0,
                "drafts": [],
                "auto_approved": False,
                "scan_started": False,
                "setup_required": True,
                "card": build_onboarding_card(load_config(config_path), store).as_dict(),
            }
        return git_research_report(store, selected_roots)

    @registry.tool(
        "review_git_drafts",
        title="Review one persisted Git or manual project draft",
        description=(
            "Display or explicitly navigate, save, skip, edit, or add a local review draft. "
            "Saving never approves evidence or changes a resume; Git provenance remains local."
        ),
        annotations=LOCAL_WRITE,
    )
    def review_git_drafts(
        action: str = "show",
        draft_id: str | None = None,
        title: str = "",
        description: str = "",
    ) -> dict[str, object]:
        """Operate one persisted review draft at a time without an evidence-approval route."""
        if action == "add":
            if draft_id is not None:
                raise ValueError("adding a manual project draft does not accept a draft ID")
            store.add_manual_git_research_draft(title=title, description=description)
            draft, position, total = store.review_git_research_draft(action="show", draft_id=None)
        else:
            if action == "edit" and (not title or not description):
                raise ValueError("editing a review draft requires title and description")
            if action != "edit" and (title or description):
                raise ValueError("title and description are only valid when adding or editing")
            draft, position, total = store.review_git_research_draft(
                action=action,
                draft_id=draft_id,
                title=title or None,
                description=description or None,
            )
        return {
            "draft": {
                "id": draft.id,
                "title": draft.title,
                "description": draft.description,
                "source": draft.source,
                "review_status": draft.review_status,
                "needs_review": draft.needs_review,
            },
            "position": position,
            "total": total,
            "evidence_approved": False,
            "resume_changed": False,
        }

    @registry.tool(
        "review_git_draft_prompt",
        title="Prompt for an explicit Git-project review decision",
        description=(
            "On MCP 2026-07-28 clients, display one local draft and ask for an explicit Save or "
            "Skip decision. Save or Skip changes only the local draft review status; neither "
            "approves evidence nor changes a resume. Older clients receive the draft without a "
            "prompt and must use review_git_drafts explicitly."
        ),
        annotations=LOCAL_WRITE,
    )
    async def review_git_draft_prompt(
        draft_id: str,
        ctx: Context,
    ) -> dict[str, object] | InputRequiredResult:
        """Use a sealed MCP multi-round-trip request for one explicit review decision."""
        shown = review_git_drafts(action="show", draft_id=draft_id)
        if ctx.protocol_version != "2026-07-28":
            return shown

        response = (ctx.input_responses or {}).get("review_decision")
        if response is not None:
            if not isinstance(response, ElicitResult) or response.action != "accept":
                return shown
            decision = (response.content or {}).get("decision")
            if decision not in {"save", "skip"}:
                return shown
            if ctx.request_state != draft_id:
                raise ValueError("review decision does not match the requested draft")
            return review_git_drafts(action=decision, draft_id=draft_id)

        draft_data = cast(dict[str, object], shown["draft"])
        title = cast(str, draft_data["title"])
        return InputRequiredResult(
            input_requests={
                "review_decision": ElicitRequest(
                    params=ElicitRequestFormParams(
                        message=(
                            f"Review local project draft: {title}. Save keeps it as a reviewable "
                            "draft; Skip marks it skipped. Neither action approves evidence or "
                            "changes a resume."
                        ),
                        requested_schema={
                            "type": "object",
                            "properties": {
                                "decision": {
                                    "type": "string",
                                    "enum": ["save", "skip"],
                                    "description": "Choose Save only after reviewing the draft.",
                                }
                            },
                            "required": ["decision"],
                            "additionalProperties": False,
                        },
                    )
                )
            },
            request_state=draft_id,
        )

    @registry.tool("record_token_usage", annotations=LOCAL_WRITE)
    def record_token_usage(
        application_id: str,
        operation: str,
        input_tokens: StrictInt,
        output_tokens: StrictInt,
        model: str = "",
    ) -> dict[str, object]:
        """Record host-reported tokens against one application without a cost estimate."""
        usage = store.record_token_usage(
            application_id=application_id,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model or None,
        )
        return {
            "usage": cast(dict[str, object], json_value(asdict(usage))),
            "summary": store.token_usage_summary(application_id=application_id),
        }

    @registry.tool("sync_recruiting_mail", annotations=NETWORK_READ_AND_WRITE)
    def sync_recruiting_mail() -> dict[str, object]:
        """Read configured mail page by page, persist local events, and summarize safely."""
        known_message_ids = {event.message_id for event in store.list_mail_events()}
        messages = build_mail_provider(config).fetch_inbox_metadata(
            page_size=100,
            max_messages=1000,
            include_content=config.mail_provider != "gmail",
            known_message_ids=known_message_ids,
        )
        new_messages = [
            message for message in messages if message.message_id not in known_message_ids
        ]
        sync_result = sync_metadata(store, new_messages)
        tracker_updates = 0
        tracker_imports = 0
        if config.tracker.enabled and config.tracker.tracker_dir is not None:
            tracker_updates = reconcile_confirmed_application_tracker_rows(
                tracker_dir=config.tracker.tracker_dir,
                events=store.list_mail_events(),
            )
            tracker_imports = import_confirmed_application_tracker_rows(
                tracker_dir=config.tracker.tracker_dir,
                active_cycles=config.tracker.active_cycles,
                events=store.list_mail_events(),
            )
        tracker_rows_updated = tracker_updates + tracker_imports
        contacts_projected = project_recruiter_contacts(
            store.list_recruiter_contacts(), config.contact_outputs
        )
        created = cast(int, sync_result["created"])
        recruiting_events = cast(int, sync_result["application"]) + cast(int, sync_result["job"])
        message = (
            "📬 **Erga mail sync complete**\n\n"
            f"{config.mail_provider.title()} {config.mail_folder} checked: "
            f"{len(messages)} messages scanned · {created} new events · "
            f"{recruiting_events} recruiting updates · "
            f"{tracker_rows_updated} tracker rows updated · "
            f"{contacts_projected} contacts projected."
        )
        return {
            "provider": config.mail_provider,
            "fetched": len(messages),
            "created": created,
            "recruiting_events": recruiting_events,
            "tracker_updates": tracker_rows_updated,
            "tracker_imports": tracker_imports,
            "contacts_projected": contacts_projected,
            "message": message,
        }
