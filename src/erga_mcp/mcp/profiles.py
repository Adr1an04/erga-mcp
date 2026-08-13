from __future__ import annotations

from collections.abc import Mapping

from mcp.types import ToolAnnotations

from erga_mcp.config import ErgaConfig
from erga_mcp.models import Evidence

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
NETWORK_READ = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
DESTRUCTIVE_LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False
)
LOCAL_IDEMPOTENT_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
NETWORK_READ_AND_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
JOB_INTAKE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)
LOCAL_EXEC = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

READ_TOOL_NAMES = frozenset(
    {
        "erga_capabilities",
        "pipeline_status",
        "list_applications",
        "application_tracker",
        "research_navigator",
        "onboarding_status",
        "git_skill_review_card",
        "project_catalogue",
        "erga_settings_card",
        "list_evidence",
        "list_mail_events",
        "list_mail_reconciliation_reviews",
        "token_usage",
        "search_keryx_jobs",
    }
)
LOCAL_ANALYSIS_TOOL_NAMES = frozenset({"propose_project_metrics"})
NETWORK_READ_TOOL_NAMES = frozenset({"scrape_public_page", "extract_public_page"})
NETWORK_WRITE_TOOL_NAMES = frozenset(
    {"discover_job_research", "refresh_project_catalogue", "create_tailoring_plan"}
)
LOCAL_WRITE_TOOL_NAMES = frozenset(
    {
        "application_orbit",
        "update_orbit_preferences",
        "record_token_usage",
        "update_application_status",
        "confirm_application_submission",
        "export_data",
        "record_secondary_research",
        "create_research_brief",
        "record_deep_research",
        "create_tailored_resume",
        "create_cover_letter",
        "cover_letter_style_context",
        "validate_tailored_resume",
        "research_git_worktrees",
        "review_git_drafts",
        "review_git_draft_prompt",
        "update_skill_inventory",
        "manage_portfolio_roots",
        "review_git_skill_group",
        "update_tailoring_plan",
        "retry_mail_reconciliation",
        "resolve_mail_reconciliation",
    }
)
HERMES_TOOL_NAMES = frozenset(
    {
        "application_orbit",
        "update_orbit_preferences",
        "confirm_application_submission",
        "sync_recruiting_mail",
        "list_mail_reconciliation_reviews",
        "retry_mail_reconciliation",
        "resolve_mail_reconciliation",
        "install_mail_monitor_scripts",
        "install_update_monitor_script",
        "discover_job_research",
        "create_research_brief",
        "research_git_worktrees",
        "update_skill_inventory",
        "manage_portfolio_roots",
        "review_git_skill_group",
        "refresh_project_catalogue",
        "create_tailoring_plan",
        "update_tailoring_plan",
        "execute_tailoring_plan",
    }
)
CAREER_TOOL_NAMES = frozenset(
    {
        "erga_capabilities",
        "pipeline_status",
        "list_applications",
        "application_tracker",
        "application_orbit",
        "update_orbit_preferences",
        "research_navigator",
        "onboarding_status",
        "git_skill_review_card",
        "project_catalogue",
        "erga_settings_card",
        "list_evidence",
        "update_application_status",
        "confirm_application_submission",
        "scrape_public_page",
        "extract_public_page",
        "intake_job_url",
        "prepare_job_workspace",
        "record_secondary_research",
        "discover_job_research",
        "create_research_brief",
        "record_deep_research",
        "create_tailored_resume",
        "validate_tailored_resume",
        "create_cover_letter",
        "propose_project_metrics",
        "update_skill_inventory",
        "manage_portfolio_roots",
        "review_git_skill_group",
        "refresh_project_catalogue",
        "create_tailoring_plan",
        "update_tailoring_plan",
        "execute_tailoring_plan",
        "search_keryx_jobs",
        "install_update_monitor_script",
        "retry_mail_reconciliation",
        "resolve_mail_reconciliation",
    }
)
CAREER_PRIVATE_TOOL_NAMES = CAREER_TOOL_NAMES | frozenset(
    {"resume_source_context", "cover_letter_style_context", "export_data"}
)
ALL_TOOL_NAMES = frozenset(
    {
        *READ_TOOL_NAMES,
        *LOCAL_ANALYSIS_TOOL_NAMES,
        *NETWORK_READ_TOOL_NAMES,
        *NETWORK_WRITE_TOOL_NAMES,
        *LOCAL_WRITE_TOOL_NAMES,
        *HERMES_TOOL_NAMES,
        "resume_source_context",
        "intake_job_url",
        "execute_tailoring_plan",
        "prepare_job_workspace",
    }
)
TOOL_PROFILES = {
    "career": CAREER_TOOL_NAMES,
    "career-private": CAREER_PRIVATE_TOOL_NAMES,
    "default": ALL_TOOL_NAMES,
    "read": READ_TOOL_NAMES,
    "research": READ_TOOL_NAMES | NETWORK_READ_TOOL_NAMES,
    "write": READ_TOOL_NAMES | LOCAL_WRITE_TOOL_NAMES,
    "hermes": READ_TOOL_NAMES | HERMES_TOOL_NAMES,
}


def selected_tool_profile(config: ErgaConfig, environment: Mapping[str, str]) -> str:
    """Resolve a non-secret MCP capability profile, with environment taking precedence."""
    profile = environment.get("ERGA_MCP_TOOL_PROFILE", config.mcp.tool_profile).strip().casefold()
    if profile not in TOOL_PROFILES:
        raise ValueError(
            "ERGA_MCP_TOOL_PROFILE must be career, career-private, default, read, research, "
            "write, or hermes"
        )
    return profile


def enabled_tool_names(config: ErgaConfig, environment: Mapping[str, str]) -> frozenset[str]:
    """Return the names enabled by the selected non-secret capability profile."""
    return TOOL_PROFILES[selected_tool_profile(config, environment)]


def profile_visible_evidence(profile: str, evidence_records: list[Evidence]) -> list[Evidence]:
    """Withhold managed master-resume records unless a profile explicitly permits them."""
    if profile in {"career-private", "default"}:
        return evidence_records
    return [
        evidence
        for evidence in evidence_records
        if not str(getattr(evidence, "source_ref", "")).startswith("master-resume:")
    ]
