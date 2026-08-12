from __future__ import annotations

from erga_mcp.config import ErgaConfig
from erga_mcp.integrations.discord.settings import settings_path as discord_settings_path
from erga_mcp.portfolio.roots import detected_portfolio_root
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.cards import CardAction, CardField, CardView


def build_settings_card(
    config: ErgaConfig,
    store: ErgaStore,
    *,
    host_integration: str = "",
) -> CardView:
    seeds = store.list_skill_seeds()
    evidence = store.list_evidence()
    approved = sum(item.approved for item in evidence)
    resume = (
        "Configured"
        if config.resume.master_path is not None
        else "Needs setup - use **Resume setup help** below."
    )
    tracker = (
        "Configured" if config.tracker.enabled else "Needs setup - run Erga setup on the host."
    )
    standalone_discord = discord_settings_path(config.config_path).is_file()
    if host_integration == "hermes":
        discord = "Connected through Hermes - the standalone Erga bridge is optional."
    else:
        discord = (
            "Standalone bridge configured"
            if standalone_discord
            else "Standalone bridge not configured - optional when using Hermes."
        )
    mail = (
        f"{config.mail_provider} - Configured"
        if config.mail_client_id
        else f"{config.mail_provider} - Needs setup on the Erga host"
    )
    detected_root = detected_portfolio_root() if not config.portfolio_roots else None
    actions: list[CardAction] = []
    if not seeds:
        if approved:
            actions.append(
                CardAction(
                    "onboarding.skills.import",
                    "Import approved skills",
                    "One tap: seed only explicit audited skill names from approved evidence.",
                    style="primary",
                )
            )
        actions.append(
            CardAction(
                "onboarding.skills.help",
                "Set skills manually",
                "Open a short mobile-friendly guide with a copyable command.",
            )
        )
    if not config.portfolio_roots:
        if detected_root is not None:
            actions.append(
                CardAction(
                    "onboarding.roots.use_detected",
                    "Use detected projects folder",
                    "One tap: add the conventional local projects folder Erga detected.",
                    style="primary",
                )
            )
        actions.append(
            CardAction(
                "onboarding.roots.help",
                "Choose Git projects folder",
                "Explain Git roots and show a copyable mobile setup command.",
            )
        )
    if config.resume.master_path is None:
        actions.append(
            CardAction(
                "onboarding.resume.help",
                "Resume setup help",
                "Show the exact host command for importing a master resume.",
            )
        )
    if config.orbit.retain_generated_images:
        actions.append(
            CardAction(
                "orbit.retention.temporary",
                "❌ Make Orbit images temporary",
                "Delete each local PNG after Discord has uploaded its attachment.",
            )
        )
    else:
        actions.append(
            CardAction(
                "orbit.retention.save",
                "✅ Save Orbit images locally",
                "Keep generated Orbit PNGs in Erga's private local state.",
                style="success",
            )
        )
    actions.append(CardAction("onboarding.status", "Full setup", "Review every setup stage."))
    if config.portfolio_roots:
        actions.append(CardAction("git.review", "Git review", "Scan or review Git projects."))
    actions.append(CardAction("tracker.show", "Tracker", "Open the application tracker."))
    return CardView(
        title="Erga settings",
        summary=(
            "Tap a setup action for anything marked Needs setup. Credentials and private paths "
            "are never shown."
        ),
        fields=(
            CardField("Resume", resume),
            CardField("Evidence", f"{approved} approved of {len(evidence)} records"),
            CardField(
                "Skills",
                f"{len(seeds)} configured"
                if seeds
                else "Needs setup - import approved skills or add a list manually below.",
            ),
            CardField(
                "Git roots",
                f"{len(config.portfolio_roots)} configured"
                if config.portfolio_roots
                else (
                    "Needs setup - a projects folder was detected; use the button below."
                    if detected_root is not None
                    else "Needs setup - choose the projects folder on the computer running Erga."
                ),
            ),
            CardField("Obsidian tracker", tracker),
            CardField(
                "Orbit images",
                (
                    "Saved locally after Discord upload"
                    if config.orbit.retain_generated_images
                    else "Temporary - deleted locally after Discord upload"
                ),
            ),
            CardField("Discord", discord),
            CardField("Mail", mail),
            CardField("MCP profile", config.mcp.tool_profile),
        ),
        actions=tuple(actions),
    )
