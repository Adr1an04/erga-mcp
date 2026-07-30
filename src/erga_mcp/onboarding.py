"""One-command, client-neutral first-run onboarding."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .client_adapters import ClientName, client_adapter
from .client_config import (
    DEFAULT_SERVER_NAME,
    DEFAULT_TOOL_PROFILE,
    ensure_client_configuration,
    render_client_configuration,
    resolve_server_command,
)
from .config import DEFAULT_CONFIG, load_config
from .doctor import check_installation
from .store import ErgaStore


@dataclass(frozen=True)
class OnboardingReport:
    status: str
    core_ready: bool
    client: ClientName
    client_command_found: bool
    config_created: bool
    config_path: str
    data_dir: str
    mcp_config_path: str
    mcp_config_written: bool
    mcp_already_configured: bool
    model_api_required: bool
    tool_profile: str
    checks: dict[str, str]
    warnings: dict[str, str]
    next_steps: list[str]

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def _initialize_or_reuse(config_path: Path) -> tuple[bool, Path]:
    config_path = config_path.expanduser().absolute()
    created = not config_path.exists()
    if created:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(config_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    ErgaStore(config.data_dir / "erga.sqlite3").initialize()
    return created, config.data_dir


def onboard(
    client: ClientName,
    *,
    config_path: Path,
    project_dir: Path,
    server_command: Path | None = None,
    client_command: Path | None = None,
) -> OnboardingReport:
    """Initialize private state, configure one client, check health, and explain first use."""
    config_path = config_path.expanduser().absolute()
    project_dir = project_dir.expanduser().absolute()
    if not project_dir.is_dir():
        raise NotADirectoryError(f"Project directory does not exist: {project_dir}")

    config_created, data_dir = _initialize_or_reuse(config_path)
    configuration = render_client_configuration(
        client,
        project_dir=project_dir,
        config_path=config_path,
        server_command=resolve_server_command(server_command),
        server_name=DEFAULT_SERVER_NAME,
        tool_profile=DEFAULT_TOOL_PROFILE,
    )
    configured = ensure_client_configuration(configuration)
    doctor = check_installation(config_path)
    adapter = client_adapter(client)
    client_command_found = (
        client_command.is_file()
        if client_command is not None
        else adapter.executable is None or shutil.which(adapter.executable) is not None
    )
    warnings = dict(doctor.warnings)
    if not client_command_found:
        warnings["client_command"] = (
            f"{adapter.executable} was not found on PATH; install or launch "
            f"{adapter.label} before verification"
        )
    if adapter.executable is None:
        warnings["generic_client"] = (
            "Erga generated portable .mcp.json configuration, but only the selected CLI's "
            "documentation can confirm that it discovers that file."
        )

    return OnboardingReport(
        status="ready" if doctor.core_ready else "needs_attention",
        core_ready=doctor.core_ready,
        client=client,
        client_command_found=client_command_found,
        config_created=config_created,
        config_path=str(config_path.expanduser().absolute()),
        data_dir=str(data_dir),
        mcp_config_path=str(configuration.target_path),
        mcp_config_written=bool(configured["written"]),
        mcp_already_configured=bool(configured["already_configured"]),
        model_api_required=False,
        tool_profile=DEFAULT_TOOL_PROFILE,
        checks=doctor.checks,
        warnings=warnings,
        next_steps=[
            adapter.restart_step,
            'Ask the client: "Show my Erga pipeline status."',
            "Paste a public job-posting URL and ask Erga to intake it.",
            "Configure a resume template when you are ready to generate tailored PDF proposals.",
        ],
    )


def render_onboarding_report(report: OnboardingReport) -> str:
    """Render a short human-first completion message."""
    config_action = "created" if report.config_created else "reused"
    mcp_action = "already configured" if report.mcp_already_configured else "configured"
    warning_lines = (
        ["", "Optional setup still available:"]
        + [f"  - {name}: {message}" for name, message in sorted(report.warnings.items())]
        if report.warnings
        else []
    )
    lines = [
        (
            f"Erga is ready for {client_adapter(report.client).label}."
            if report.core_ready
            else (f"Erga needs attention before {client_adapter(report.client).label} can use it.")
        ),
        "",
        f"  [ok] Private configuration {config_action}: {report.config_path}",
        f"  [ok] Local database ready: {report.data_dir}",
        f"  [ok] MCP project entry {mcp_action}: {report.mcp_config_path}",
        f"  [ok] Tool profile: {report.tool_profile}",
        "  [ok] Separate model API key required: no",
        *warning_lines,
        "",
        "Next:",
        *[f"  {index}. {step}" for index, step in enumerate(report.next_steps, start=1)],
    ]
    return "\n".join(lines)
