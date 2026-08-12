from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated, Protocol, cast

from pydantic import Field, StrictBool, StrictInt

from erga_mcp.applications.identity import job_identity
from erga_mcp.applications.navigator import build_research_navigator, research_stage_for_status
from erga_mcp.config import ErgaConfig, load_config
from erga_mcp.integrations.keryx import keryx_status
from erga_mcp.integrations.keryx import search_keryx_jobs as search_cached_keryx_jobs
from erga_mcp.mcp.profiles import (
    HERMES_TOOL_NAMES,
    LOCAL_IDEMPOTENT_WRITE,
    LOCAL_WRITE_TOOL_NAMES,
    NETWORK_READ_TOOL_NAMES,
    READ_ONLY,
)
from erga_mcp.mcp.registry import ToolRegistry
from erga_mcp.operations.private_files import restrict_private_directory, restrict_private_file
from erga_mcp.portfolio.catalogue import build_project_catalogue
from erga_mcp.portfolio.roots import detected_portfolio_root
from erga_mcp.portfolio.skill_inventory import parse_skill_seed_csv
from erga_mcp.portfolio.skills import build_git_skill_review_card
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.onboarding import build_onboarding_card
from erga_mcp.tracking.orbit import create_orbit_artifact
from erga_mcp.tracking.orbit_preferences import update_orbit_preferences
from erga_mcp.tracking.settings import build_settings_card
from erga_mcp.tracking.tracker import (
    build_tracker_card,
    filter_application_tracker,
    paginate_application_tracker,
    read_application_tracker,
    render_tracker_message,
)
from erga_mcp.versioning import capabilities


class ResearchPackageResolver(Protocol):
    def __call__(self, *, output_root: Path, job_url: str) -> Path | None: ...


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def register_read_tools(
    registry: ToolRegistry,
    *,
    config: ErgaConfig,
    config_path: Path,
    store: ErgaStore,
    selected_tool_profile: str,
    research_package_by_identity: ResearchPackageResolver,
    combine_token_summaries: Callable[[Iterable[Mapping[str, int]]], dict[str, int]],
) -> None:
    """Register the local read and dashboard tool surface."""
    enabled_tool_names = registry.enabled_names

    @registry.tool("erga_capabilities", annotations=READ_ONLY)
    def erga_capabilities() -> dict[str, object]:
        """Return the compact, versioned local MCP compatibility contract."""
        capability_classes = ["local-read"]
        if enabled_tool_names & NETWORK_READ_TOOL_NAMES:
            capability_classes.append("network-read")
        if enabled_tool_names & LOCAL_WRITE_TOOL_NAMES:
            capability_classes.append("local-write")
        if enabled_tool_names & HERMES_TOOL_NAMES:
            capability_classes.append("hermes-integration")
        if "intake_job_url" in enabled_tool_names:
            capability_classes.append("network-write")
        result = capabilities(
            tool_profile=selected_tool_profile,
            capability_classes=capability_classes,
        )
        result.update({"model_api_required": False, "reasoning_host": "mcp-client"})
        keryx = keryx_status(config)
        result["optional_integrations"] = {
            "keryx": {"enabled": keryx.enabled, "cache_ready": keryx.cache_ready}
        }
        return result

    @registry.tool("pipeline_status", annotations=READ_ONLY)
    def pipeline_status() -> dict[str, int]:
        """Return counts for local-only recruiting records."""
        return {
            "applications": len(store.list_applications()),
            "evidence": len(store.list_evidence()),
            "mail_events": len(store.list_mail_events()),
            "audit_events": len(store.audit_events()),
        }

    @registry.tool("list_applications", annotations=READ_ONLY)
    def list_applications() -> list[dict[str, object]]:
        """List local application records; no external system is queried."""
        return [
            cast(dict[str, object], _json_value(asdict(application)))
            for application in store.list_applications()
        ]

    @registry.tool(
        "search_keryx_jobs",
        title="Search the optional local Keryx opportunity cache",
        description=(
            "Search a user-enabled local cache of Keryx's public US internships and new-graduate "
            "roles. This tool performs no network request, sends no query or Erga private data, "
            "and creates no application record. To act on one returned posting, the user must "
            "separately request the ordinary intake_job_url workflow with its URL."
        ),
        annotations=READ_ONLY,
    )
    def search_keryx_jobs(
        query: str = "",
        program: str = "",
        cycle: str = "",
        location: str = "",
        limit: int = 20,
    ) -> dict[str, object]:
        """Return bounded public listings from the explicitly enabled local Keryx cache."""
        return search_cached_keryx_jobs(
            config,
            query=query,
            program=program,
            cycle=cycle,
            location=location,
            limit=limit,
        )

    @registry.tool("application_tracker", annotations=READ_ONLY)
    def application_tracker(
        query: str = "",
        page: Annotated[StrictInt, Field(ge=1)] = 1,
        page_size: Annotated[StrictInt, Field(ge=1, le=10)] = 6,
    ) -> dict[str, object]:
        """Render or search every local tracker cycle with stable pagination."""
        if not config.tracker.enabled or config.tracker.tracker_dir is None:
            return {
                "enabled": False,
                "entries": [],
                "summary": {},
                "message": (
                    "### Erga application tracker\n\n"
                    "Obsidian application tracking is not configured for this Erga workspace."
                ),
            }
        normalized_query = "" if query.strip().casefold() in {"all", "*"} else query.strip()
        snapshot = filter_application_tracker(
            read_application_tracker(config.tracker.tracker_dir), normalized_query
        )
        pagination = paginate_application_tracker(snapshot, page=page, page_size=page_size)
        applications = store.list_applications()
        summaries_by_identity: dict[str, list[dict[str, int]]] = {}
        for application in applications:
            summaries_by_identity.setdefault(job_identity(application.source_url), []).append(
                store.token_usage_summary(application_id=application.id)
            )
        token_usage_by_source_url = {
            entry.source_url: combine_token_summaries(
                summaries_by_identity.get(job_identity(entry.source_url), [])
            )
            for entry in pagination.entries
            if entry.source_url
        }
        entries = []
        for entry in pagination.entries:
            serialized = asdict(entry)
            research_stage = research_stage_for_status(entry.status)
            serialized["research"] = {
                "eligible": research_stage is not None and bool(entry.source_url),
                "stage": research_stage,
            }
            entries.append(serialized)
        return {
            "enabled": True,
            "entries": entries,
            "summary": snapshot.summary,
            "total_entries": pagination.total,
            "page": pagination.page,
            "page_count": pagination.page_count,
            "page_size": pagination.page_size,
            "has_previous": pagination.page > 1,
            "has_next": pagination.page < pagination.page_count,
            "cycles": sorted({entry.cycle for entry in snapshot.entries}),
            "local_application_records": len(applications),
            "token_usage": store.token_usage_summary(),
            "message": render_tracker_message(
                snapshot,
                page=pagination.page,
                page_size=pagination.page_size,
                query=normalized_query,
                token_usage_by_source_url=token_usage_by_source_url,
                local_application_count=len(applications),
            ),
            "card": build_tracker_card(
                snapshot,
                page=pagination.page,
                page_size=pagination.page_size,
                query=normalized_query,
            ).as_dict(),
        }

    @registry.tool("application_orbit", annotations=LOCAL_IDEMPOTENT_WRITE)
    def application_orbit(
        cycle: Annotated[str, Field(max_length=80)] = "",
    ) -> dict[str, object]:
        """Render the aggregate Applied-to-outcome funnel without setup states or model calls."""
        current_config = load_config(config_path)
        output_dir = current_config.data_dir / "orbit"
        output_dir.mkdir(parents=True, exist_ok=True)
        restrict_private_directory(output_dir)
        artifact = create_orbit_artifact(
            applications=store.list_applications(),
            audit_events=store.audit_events(),
            output_dir=output_dir,
            tracker_dir=(
                current_config.tracker.tracker_dir
                if current_config.tracker.enabled and current_config.tracker.tracker_dir is not None
                else None
            ),
            cycle=cycle,
        )
        restrict_private_file(artifact.image_path)
        return {
            "image_path": str(artifact.image_path),
            "mime_type": "image/png",
            "message": artifact.message,
            "snapshot": artifact.snapshot.as_dict(),
            "model_api_used": False,
            "retain_generated_images": current_config.orbit.retain_generated_images,
        }

    @registry.tool("update_orbit_preferences", annotations=LOCAL_IDEMPOTENT_WRITE)
    def update_orbit_preferences_tool(
        retain_generated_images: StrictBool,
    ) -> dict[str, object]:
        """Choose whether uploaded Orbit PNGs remain in Erga's private local state."""
        settings = update_orbit_preferences(
            config_path,
            retain_generated_images=retain_generated_images,
        )
        return {
            "retain_generated_images": settings.retain_generated_images,
            "message": (
                "Orbit images will be saved locally after Discord upload."
                if settings.retain_generated_images
                else "Orbit images will be deleted locally after Discord upload."
            ),
        }

    @registry.tool(
        "research_navigator",
        title="Open stage-aware role research",
        description=(
            "Open a read-only navigator for a tracked role once it reaches an OA, interview, or "
            "offer. It lists the official posting, saved local research artifacts, bounded public "
            "links, résumé availability, and stage-specific preparation guidance. Community and "
            "secondary sources remain explicitly unverified."
        ),
        annotations=READ_ONLY,
    )
    def research_navigator(job_url: str) -> dict[str, object]:
        """Return a mobile-safe research card for one exact eligible tracker row."""
        if not config.tracker.enabled or config.tracker.tracker_dir is None:
            raise ValueError("application tracking must be configured to open role research")
        identity = job_identity(job_url)
        matches = [
            entry
            for entry in read_application_tracker(config.tracker.tracker_dir).entries
            if entry.source_url and job_identity(entry.source_url) == identity
        ]
        if not matches:
            raise ValueError("no tracker row matches this job URL")
        eligible = [entry for entry in matches if research_stage_for_status(entry.status)]
        if not eligible:
            raise ValueError("research navigation becomes available when the role reaches OA")
        entry = eligible[0]
        application_matches = [
            application
            for application in store.list_applications()
            if job_identity(application.source_url) == identity
        ]
        package_dir = research_package_by_identity(
            output_root=config.resume.output_root,
            job_url=entry.source_url,
        )
        navigator = build_research_navigator(entry=entry, package_dir=package_dir)
        available_actions = tuple(
            action
            for action in navigator.card.actions
            if action.action_id == "research.back"
            or (
                action.action_id == "research.refresh"
                and "discover_job_research" in enabled_tool_names
                and package_dir is not None
                and bool(application_matches)
            )
            or (
                action.action_id == "research.brief"
                and "create_research_brief" in enabled_tool_names
                and package_dir is not None
            )
        )
        card = replace(navigator.card, actions=available_actions)
        return {
            "company": entry.company,
            "role": entry.role,
            "job_url": entry.source_url,
            "stage": navigator.stage,
            "research_query": f"{entry.company} {entry.role}",
            "package_available": navigator.package_available,
            "resume_available": navigator.resume_available,
            "source_warning": navigator.source_warning,
            "saved_artifact_count": navigator.saved_artifact_count,
            "artifacts": [artifact.as_dict() for artifact in navigator.artifacts],
            "links": [link.as_dict() for link in navigator.links],
            "card": card.as_dict(),
        }

    @registry.tool("onboarding_status", annotations=READ_ONLY)
    def onboarding_status() -> dict[str, object]:
        """Return the shared truthful onboarding card without mutating local state."""
        return build_onboarding_card(load_config(config_path), store).as_dict()

    @registry.tool("erga_settings_card", annotations=READ_ONLY)
    def erga_settings_card(host_integration: str = "") -> dict[str, object]:
        """Return a redacted shared settings card; credentials and secret paths are omitted."""
        if host_integration not in {"", "hermes"}:
            raise ValueError("host_integration is not supported")
        return build_settings_card(
            load_config(config_path), store, host_integration=host_integration
        ).as_dict()

    @registry.tool("git_skill_review_card", annotations=READ_ONLY)
    def git_skill_review_card(
        page: Annotated[StrictInt, Field(ge=1)] = 1,
        page_size: Annotated[StrictInt, Field(ge=1, le=10)] = 5,
        source_filter: str = "",
        seed_csv: str = "",
    ) -> dict[str, object]:
        """Render paginated Git/seed skill groups; rendering never approves evidence."""
        seed_override = parse_skill_seed_csv(seed_csv) if seed_csv.strip() else ()
        current_config = load_config(config_path)
        if current_config.portfolio_roots:
            primary_action = (
                "git.scan",
                "Scan Git projects",
                "Scan configured roots and refresh this review.",
            )
        elif detected_portfolio_root() is not None:
            primary_action = (
                "onboarding.roots.use_detected",
                "Use detected folder and scan",
                "One tap: configure the detected projects folder, then continue this scan.",
            )
        else:
            primary_action = (
                "onboarding.roots.help",
                "Set up Git projects",
                "Choose the projects folder on the computer running Erga before scanning.",
            )
        return build_git_skill_review_card(
            store,
            page=page,
            page_size=page_size,
            source_filter=source_filter or None,
            seed_override=seed_override,
            primary_action_id=primary_action[0],
            primary_action_label=primary_action[1],
            primary_action_instruction=primary_action[2],
        ).as_dict()

    @registry.tool("project_catalogue", annotations=READ_ONLY)
    def project_catalogue(
        page: Annotated[StrictInt, Field(ge=1)] = 1,
        page_size: Annotated[StrictInt, Field(ge=1, le=10)] = 6,
        query: str = "",
    ) -> dict[str, object]:
        """Browse cached GitHub discovery plus the approved project inventory without writes."""
        return build_project_catalogue(
            load_config(config_path),
            store,
            page=page,
            page_size=page_size,
            query=query,
        ).as_dict()
