from __future__ import annotations

from erga_mcp.config import ErgaConfig
from erga_mcp.portfolio.roots import detected_portfolio_root
from erga_mcp.store import ErgaStore
from erga_mcp.tracking.cards import CardAction, CardField, CardView


def build_onboarding_card(config: ErgaConfig, store: ErgaStore) -> CardView:
    seeds = store.list_skill_seeds()
    approved_evidence = sum(item.approved for item in store.list_evidence())
    checked = sum(item.checked for item in seeds)
    master = config.resume.master_path
    resume_state = f"Configured: {master.name}" if master is not None else "Not configured"
    obsidian_state = "Configured" if config.vault_path is not None else "Not configured"
    tracker_state = "Available" if config.tracker.enabled else "Not configured"
    skills_state = (
        f"{len(seeds)} configured - {checked} checked, {len(seeds) - checked} unchecked"
        if seeds
        else "Needs setup - import approved skills or add a list manually below."
    )
    detected_root = detected_portfolio_root() if not config.portfolio_roots else None
    roots_state = (
        "\n".join(str(root) for root in config.portfolio_roots)
        if config.portfolio_roots
        else (
            "Needs setup - a conventional projects folder was detected."
            if detected_root is not None
            else "Needs setup - choose the projects folder on the computer running Erga."
        )
    )
    configured = sum(
        (
            master is not None,
            config.vault_path is not None,
            bool(seeds),
            bool(config.portfolio_roots),
            config.tracker.enabled,
        )
    )
    actions: list[CardAction] = []
    if not seeds and approved_evidence:
        actions.append(
            CardAction(
                "onboarding.skills.import",
                "Import approved skills",
                "One tap: seed explicit audited skills already present in approved evidence.",
                style="primary",
            )
        )
    actions.append(
        CardAction(
            "onboarding.skills.help",
            "Set skills manually",
            "Open a mobile-friendly guide with a copyable command.",
        )
    )
    if not config.portfolio_roots and detected_root is not None:
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
    if config.portfolio_roots:
        actions.append(
            CardAction(
                "git.scan",
                "Scan Git projects",
                "Scan configured roots and open the shared Git review UI.",
            )
        )
    actions.append(CardAction("settings.show", "Open settings", "Review setup progress."))
    return CardView(
        title="Erga onboarding",
        summary=f"{configured} of 5 optional/local stages configured. Review any answer safely.",
        fields=(
            CardField("Resume source", resume_state),
            CardField("Obsidian", obsidian_state),
            CardField("Skill inventory", skills_state),
            CardField("Git roots", roots_state),
            CardField("Application tracker", tracker_state),
            CardField(
                "Trust boundary",
                "Seed skills are self-reported discovery hints, not approved résumé evidence.",
            ),
        ),
        actions=tuple(actions),
    )
