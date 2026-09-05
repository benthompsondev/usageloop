"""Turn internal state into a health summary a non-technical user can read.

Pure functions only, so the wording and the status decisions can be tested
without a running Qt application. The Settings page renders these rows as badges
and keeps the raw technical text behind an expander.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .app_state import AppSettings, ProviderViewState
from .host import is_windows, platform_label
from .product import PRODUCT


#: Local evidence older than this is reported as stale rather than current.
STALE_AFTER_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class HealthRow:
    label: str
    status: str
    tone: str
    detail: str


def provider_health(
    state: ProviderViewState,
    *,
    automation_enabled: bool,
    compatible_identity: str | None,
    now: float,
) -> HealthRow:
    """Describe one provider in the words a normal user would use."""
    if not state.installed:
        return HealthRow(
            state.display_name,
            "Not found",
            "neutral",
            (
                f"{state.display_name} is not installed on this PC, so it is being skipped."
                if is_windows()
                else f"{state.display_name} was not found in $CODEX_HOME, in a Codex "
                "desktop installation, or on PATH, so it is being skipped."
            ),
        )
    if state.status == "Needs attention":
        return HealthRow(
            state.display_name,
            "Needs attention",
            "error",
            "A check stopped early. Nothing was retried. Technical details has the reason.",
        )
    if not state.automation_supported:
        return HealthRow(
            state.display_name,
            "Paused",
            "warning",
            "This version could not be confirmed as compatible, so automation is paused for it.",
        )
    if state.status == "Starting":
        return HealthRow(
            state.display_name, "Checking", "info", "A safe check is running right now."
        )
    if state.reset_at is not None and state.reset_at > now:
        if (
            state.last_verified_at is not None
            and now - state.last_verified_at > STALE_AFTER_SECONDS
        ):
            return HealthRow(
                state.display_name,
                "Stale",
                "warning",
                "The countdown is running from older information than usual.",
            )
        return HealthRow(
            state.display_name,
            "Ready",
            "success",
            "A five-hour window is open and counting down.",
        )
    if not automation_enabled:
        return HealthRow(
            state.display_name,
            "Detected",
            "neutral",
            "Installed and detected. Turn the main switch on to have windows kept ready.",
        )
    if state.usage_checked_at is None:
        return HealthRow(
            state.display_name,
            "Not checked",
            "info",
            "No five-hour window reading has been captured yet.",
        )
    if compatible_identity is not None and compatible_identity == state.runtime_identity:
        return HealthRow(
            state.display_name,
            "Waiting",
            "info",
            "Detected and compatible. Waiting for a safe moment to start a window.",
        )
    return HealthRow(
        state.display_name,
        "Waiting",
        "info",
        "Detected. Compatibility is checked before anything is sent.",
    )


def automation_health(settings: AppSettings) -> HealthRow:
    if settings.automation_enabled:
        return HealthRow(
            "Automation",
            "On",
            "success",
            "The Codex reset clock is maintained using one minimal guarded request after rollover.",
        )
    return HealthRow(
        "Automation",
        "Off",
        "neutral",
        "No Codex request is sent while this is off.",
    )


def codex_installation_health(state: ProviderViewState) -> HealthRow:
    if not state.installed:
        return HealthRow("Codex installed", "Not found", "error", "Install and sign in to Codex before enabling automation.")
    if state.status == "Needs attention" or not state.automation_supported:
        return HealthRow("Codex installed", "Check needed", "error", "The current Codex capabilities could not be confirmed safely.")
    return HealthRow("Codex installed", "Detected", "success", "The local Codex executable is available.")


def five_hour_health(state: ProviderViewState, *, now: float) -> HealthRow:
    if state.reset_at is None:
        return HealthRow("5-hour window", "Not verified", "neutral", "No fixed five-hour reset has been verified yet.")
    if state.last_verified_at is not None and now - state.last_verified_at > STALE_AFTER_SECONDS:
        return HealthRow("5-hour window", "Stale", "warning", "The cached reset evidence is older than usual.")
    if state.reset_at > now and state.status == "Ready":
        return HealthRow("5-hour window", "Clock running", "success", f"Last-known usage is {state.used_percent:g}% used." if state.used_percent is not None else "A fixed reset is counting down locally.")
    return HealthRow("5-hour window", "Waiting", "info", "The previous reset boundary has passed or is not currently anchored.")


def weekly_health(state: ProviderViewState) -> HealthRow:
    if state.weekly_used_percent is None:
        return HealthRow("Weekly allowance", "Not checked", "neutral", "No unique official weekly Codex window is cached. Automatic starts still fail closed without one.")
    if state.weekly_used_percent >= 99:
        return HealthRow("Weekly allowance", "Protected", "error", f"{state.weekly_used_percent:g}% used. UsageLoop will not start a window.")
    return HealthRow("Weekly allowance", "Safe", "success", f"Last-known usage is {state.weekly_used_percent:g}% used. This is checked before a start.")


def local_state_health(
    *,
    state_file_exists: bool,
    newest_observation_at: float | None,
    now: float,
    persistence_error: bool = False,
) -> HealthRow:
    if persistence_error:
        return HealthRow(
            "Local state",
            "Needs attention",
            "error",
            "A setting could not be saved. Automatic starts are paused until local state can be written again.",
        )
    if not state_file_exists:
        return HealthRow(
            "Local state",
            "Not saved yet",
            "neutral",
            "Nothing has been saved on this PC yet. That is normal on a first run.",
        )
    if newest_observation_at is None:
        return HealthRow(
            "Local state",
            "Healthy",
            "success",
            "Saved on this PC and readable. No provider readings stored yet.",
        )
    age = now - newest_observation_at
    if age > STALE_AFTER_SECONDS:
        return HealthRow(
            "Local state",
            "Stale",
            "warning",
            "Saved and readable, but the newest reading is more than six hours old.",
        )
    return HealthRow(
        "Local state", "Healthy", "success", "Saved on this PC and up to date."
    )


def startup_health(enabled: bool) -> HealthRow:
    label = f"{platform_label()} startup"
    if enabled:
        return HealthRow(
            label,
            "Enabled",
            "success",
            f"{PRODUCT.display_name} opens in the tray when you sign in.",
        )
    return HealthRow(
        label,
        "Disabled",
        "neutral",
        f"{PRODUCT.display_name} only runs when you open it.",
    )


def build_health_rows(
    states: Mapping[str, ProviderViewState],
    settings: AppSettings,
    *,
    startup_enabled: bool,
    state_file_exists: bool,
    now: float,
    persistence_error: bool = False,
) -> tuple[HealthRow, ...]:
    state = states.get("codex") or ProviderViewState.waiting("codex", "Codex", installed=False)
    rows = [codex_installation_health(state), five_hour_health(state, now=now), weekly_health(state)]
    observations = [
        state.usage_checked_at
        for state in states.values()
        if state.usage_checked_at is not None
    ]
    rows.append(automation_health(settings))
    rows.append(
        local_state_health(
            state_file_exists=state_file_exists,
            newest_observation_at=max(observations) if observations else None,
            now=now,
            persistence_error=persistence_error,
        )
    )
    rows.append(startup_health(startup_enabled))
    return tuple(rows)


def overall_summary(
    rows: tuple[HealthRow, ...], *, automation_enabled: bool = False
) -> HealthRow:
    """One line for the top of the card, so the page answers itself at a glance.

    "All good" is reserved for a state that actually earned it. An app that is
    merely installed correctly says so instead, because claiming success before
    anything has been verified is the kind of thing people stop trusting.
    """
    if any(row.tone == "error" for row in rows):
        return HealthRow(
            "Overall",
            "Needs attention",
            "error",
            "Something needs a look. The rows below say which part.",
        )
    if any(row.tone == "warning" for row in rows):
        return HealthRow(
            "Overall",
            "Mostly fine",
            "warning",
            "Everything is running, but one thing is worth checking.",
        )
    if not automation_enabled:
        return HealthRow(
            "Overall",
            "Setup OK",
            "info",
            f"{PRODUCT.display_name} is installed correctly. Turn automation on when you are ready.",
        )
    if not any(row.status == "Clock running" for row in rows):
        return HealthRow(
            "Overall",
            "Setup OK",
            "info",
            "Automation is on. Nothing has been verified yet, so there is nothing to report.",
        )
    return HealthRow(
        "Overall", "All good", "success", "The Codex reset clock is running. Nothing needs you."
    )


def technical_summary(
    states: Mapping[str, ProviderViewState],
    settings: AppSettings,
    *,
    app_version: str = PRODUCT.version,
    persistence_error: bool = False,
) -> str:
    """The raw detail, kept for troubleshooting and for the copy button.

    Deliberately excludes prompts, responses, credentials, and account identity,
    because this text is meant to be pasted into a bug report.
    """
    compatible = settings.compatible_runtime_identities or {}
    local_data = (
        rf"%LOCALAPPDATA%\{PRODUCT.app_data_folder}"
        if is_windows()
        else "${XDG_STATE_HOME:-~/.local/state}/" + PRODUCT.app_data_folder.lower()
    )
    lines = [
        f"{PRODUCT.display_name} {app_version} on {platform_label()}",
        f"Local data: {local_data}",
        f"Local state: {'Needs attention' if persistence_error else 'Healthy'}",
        "",
        "Codex support",
        f"  Discovery: {_discovery_order()}",
        "  Observation: local app-server account/rateLimits/read",
        "  Start: ephemeral thread/start + turn/start with read-only sandbox",
        "  Verification: repeated fixed-reset observations are authoritative",
    ]
    for provider_id, state in states.items():
        version = state.runtime_version or "version unavailable"
        if not state.automation_supported:
            compatibility = "not applicable"
        elif compatible.get(provider_id) == state.runtime_identity:
            compatibility = "passed"
        else:
            compatibility = "not confirmed"
        lines.extend(
            [
                "",
                state.display_name,
                f"  Installed: {'yes' if state.installed else 'no'}",
                f"  Version: {version}",
                f"  Compatibility: {compatibility}",
                f"  Raw state: {state.status}",
                f"  Outcome category: {state.outcome_category or 'not recorded'}",
                f"  Detail: {state.detail}",
            ]
        )
    lines.extend(
        [
            "",
            "Automation",
            f"  Global control: {'enabled' if settings.automation_enabled else 'off'}",
            "  Codex-triggering activity while off: none",
        ]
    )
    return "\n".join(lines)


def _discovery_order() -> str:
    """Name where UsageLoop looks for Codex, symbolically and in order.

    This is a fixed description of the search, not a report of what was found,
    so it adds no state and expands no path from the user's home directory.
    """
    if is_windows():
        return "installed Codex app binary, then PATH"
    return "$CODEX_HOME/plugins, then a Codex desktop install, then PATH"
