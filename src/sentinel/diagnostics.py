"""Turn internal state into a health summary a non-technical user can read.

Pure functions only, so the wording and the status decisions can be tested
without a running Qt application. The Settings page renders these rows as badges
and keeps the raw technical text behind an expander.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .app_state import AppSettings, ProviderViewState
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
            f"{state.display_name} is not installed on this PC, so it is being skipped.",
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
            "Windows are kept ready using the smallest request, after every safety check.",
        )
    return HealthRow(
        "Automation",
        "Off",
        "neutral",
        "Nothing is sent to any provider while this is off.",
    )


def local_state_health(
    *, state_file_exists: bool, newest_observation_at: float | None, now: float
) -> HealthRow:
    if not state_file_exists:
        return HealthRow(
            "Local data",
            "Not saved yet",
            "neutral",
            "Nothing has been saved on this PC yet. That is normal on a first run.",
        )
    if newest_observation_at is None:
        return HealthRow(
            "Local data",
            "Healthy",
            "success",
            "Saved on this PC and readable. No provider readings stored yet.",
        )
    age = now - newest_observation_at
    if age > STALE_AFTER_SECONDS:
        return HealthRow(
            "Local data",
            "Stale",
            "warning",
            "Saved and readable, but the newest reading is more than six hours old.",
        )
    return HealthRow(
        "Local data", "Healthy", "success", "Saved on this PC and up to date."
    )


def startup_health(enabled: bool) -> HealthRow:
    if enabled:
        return HealthRow(
            "Windows startup",
            "Enabled",
            "success",
            f"{PRODUCT.display_name} opens in the tray when you sign in.",
        )
    return HealthRow(
        "Windows startup",
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
) -> tuple[HealthRow, ...]:
    compatible = settings.compatible_runtime_identities or {}
    rows = [
        provider_health(
            state,
            automation_enabled=settings.automation_enabled,
            compatible_identity=compatible.get(provider_id),
            now=now,
        )
        for provider_id, state in states.items()
    ]
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
        )
    )
    rows.append(startup_health(startup_enabled))
    return tuple(rows)


def overall_summary(rows: tuple[HealthRow, ...]) -> HealthRow:
    """One line for the top of the card, so the page answers itself at a glance."""
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
    return HealthRow(
        "Overall", "All good", "success", "Nothing needs your attention right now."
    )


def technical_summary(
    states: Mapping[str, ProviderViewState],
    settings: AppSettings,
    *,
    app_version: str = PRODUCT.version,
) -> str:
    """The raw detail, kept for troubleshooting and for the copy button.

    Deliberately excludes prompts, responses, credentials, and account identity,
    because this text is meant to be pasted into a bug report.
    """
    compatible = settings.compatible_runtime_identities or {}
    lines = [f"{PRODUCT.display_name} {app_version}"]
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
                f"  Detail: {state.detail}",
            ]
        )
    lines.extend(
        [
            "",
            "Automation",
            f"  Global control: {'enabled' if settings.automation_enabled else 'off'}",
            "  Provider-triggering activity while off: none",
        ]
    )
    return "\n".join(lines)
