"""Presentation-safe state and local automation decisions for the desktop app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from typing import Any

from .product import PRODUCT
from .schedule import (
    CONTINUOUS,
    DAILY,
    RESET_BUFFER_SECONDS,
    SCHEDULE_MODES,
    WEEKLY,
    normalize_weekly_times,
    schedule_summary,
)


@dataclass(frozen=True)
class AppSettings:
    automation_enabled: bool = False
    start_with_windows: bool = False
    first_run_complete: bool = False
    compatible_runtime_identities: dict[str, str] | None = None
    checked_runtime_identities: dict[str, str] | None = None
    schedule_mode: str = CONTINUOUS
    daily_start_hour: int = 4
    daily_start_minute: int = 0
    weekly_start_times: tuple[tuple[int, int], ...] | None = None

    def __post_init__(self) -> None:
        if self.compatible_runtime_identities is None:
            object.__setattr__(self, "compatible_runtime_identities", {})
        if self.checked_runtime_identities is None:
            object.__setattr__(self, "checked_runtime_identities", {})


@dataclass(frozen=True)
class ProviderViewState:
    provider_id: str
    display_name: str
    installed: bool
    automation_supported: bool
    status: str
    detail: str
    runtime_identity: str | None = None
    runtime_version: str | None = None
    reset_at: int | None = None
    last_verified_at: float | None = None
    last_action: str | None = None
    used_percent: float | None = None
    usage_checked_at: float | None = None
    weekly_used_percent: float | None = None
    weekly_reset_at: int | None = None
    automation_blocked_until: float | None = None
    retry_after_restart: bool = False
    quota_state: str | None = None
    outcome_category: str | None = None
    recovery_signature: str | None = None
    recovery_attempts: int = 0
    recovery_not_before: float | None = None

    @classmethod
    def waiting(
        cls,
        provider_id: str,
        display_name: str,
        *,
        installed: bool,
        runtime_identity: str | None = None,
    ) -> "ProviderViewState":
        return cls(
            provider_id,
            display_name,
            installed,
            True,
            "Waiting" if installed else "Needs attention",
            (
                "Detected. Waiting for a verified window."
                if installed
                else f"{display_name} was not found on this PC."
            ),
            runtime_identity,
        )

    def with_reset(self, reset_at: int, *, verified_at: float) -> "ProviderViewState":
        return replace(
            self,
            status="Ready" if reset_at > verified_at else "Waiting",
            detail="The last verified five-hour window is ready.",
            reset_at=int(reset_at),
            last_verified_at=float(verified_at),
        )


@dataclass(frozen=True)
class AutomationDecision:
    action: str
    reason: str


def automation_decision(
    enabled: bool,
    state: ProviderViewState,
    *,
    now: float,
    compatible_runtime_identity: str | None = None,
    checked_runtime_identity: str | None = None,
    schedule_mode: str = CONTINUOUS,
    daily_hour: int = 4,
    daily_minute: int = 0,
    weekly_times: tuple[tuple[int, int], ...] | None = None,
    timezone: Any = None,
) -> AutomationDecision:
    if not enabled:
        return AutomationDecision("NONE", "Automation is off.")
    if not state.installed or not state.automation_supported:
        return AutomationDecision("NONE", "Provider automation is unavailable.")
    if state.status == "Needs attention":
        return AutomationDecision("NONE", "Provider needs explicit attention.")
    if state.recovery_not_before is not None and now < state.recovery_not_before:
        return AutomationDecision("WAIT", "Read-only recovery is backing off.")
    if state.automation_blocked_until is not None and now < state.automation_blocked_until:
        return AutomationDecision("WAIT", "A guarded provider action is already recorded.")
    if state.runtime_identity != compatible_runtime_identity:
        if state.runtime_identity == checked_runtime_identity:
            return AutomationDecision("NONE", "Provider compatibility needs attention.")
        return AutomationDecision("PROBE", "Provider runtime capabilities must be checked.")
    if state.reset_at is None:
        return AutomationDecision("BOOTSTRAP", "No verified window is known yet.")
    try:
        schedule = schedule_summary(
            schedule_mode,
            boundary_reset_at=state.reset_at,
            now=now,
            hour=daily_hour,
            minute=daily_minute,
            weekly_times=weekly_times,
            timezone=timezone,
        )
    except (OSError, OverflowError, ValueError):
        return AutomationDecision("NONE", "The saved schedule evidence is unusable.")
    if schedule.due:
        if schedule_mode == DAILY:
            return AutomationDecision(
                "ROLLOVER", "The selected daily start is due after the verified reset."
            )
        if schedule_mode == WEEKLY:
            if schedule.phase == "scheduled_first_start":
                return AutomationDecision(
                    "ROLLOVER",
                    "The scheduled weekly first start is due after the verified reset.",
                )
            return AutomationDecision(
                "ROLLOVER", "The daytime reset boundary has passed."
            )
        return AutomationDecision("ROLLOVER", "The verified reset boundary has passed.")
    if schedule_mode == DAILY and now >= state.reset_at + RESET_BUFFER_SECONDS:
        return AutomationDecision("WAIT", "Waiting for the selected daily start time.")
    if schedule_mode == WEEKLY and schedule.phase == "overnight_pause":
        return AutomationDecision(
            "WAIT", "Waiting through the overnight pause for the next scheduled start."
        )
    return AutomationDecision("WAIT", "The countdown is maintained locally.")


def format_countdown(reset_at: int | None, now: float) -> str:
    if reset_at is None:
        return "Not verified yet"
    remaining = int(reset_at - now)
    if remaining <= 0:
        return "Reset reached"
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


_TEXT_FIELDS = ("provider_id", "display_name", "status", "detail")
_OPTIONAL_TEXT_FIELDS = (
    "runtime_identity",
    "runtime_version",
    "last_action",
    "quota_state",
    "outcome_category",
    "recovery_signature",
)
_BOOL_FIELDS = ("installed", "automation_supported", "retry_after_restart")
_INT_FIELDS = ("reset_at", "weekly_reset_at")
_FLOAT_FIELDS = (
    "last_verified_at",
    "used_percent",
    "usage_checked_at",
    "weekly_used_percent",
    "automation_blocked_until",
    "recovery_not_before",
)


def _coerce_provider_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize a cached provider record so bad types cannot reach the UI.

    A dataclass does not enforce its annotations, so a hand-edited or partially
    written state file could previously load a string where a timestamp was
    expected and raise a TypeError inside the one-second clock tick. Unusable
    fields are dropped back to their defaults instead.
    """
    cleaned: dict[str, Any] = {}
    for key, raw in value.items():
        if key in _TEXT_FIELDS:
            if not isinstance(raw, str):
                raise ValueError(f"{key} must be text")
            cleaned[key] = raw
        elif key in _OPTIONAL_TEXT_FIELDS:
            cleaned[key] = raw if isinstance(raw, str) else None
        elif key in _BOOL_FIELDS:
            cleaned[key] = raw is True
        elif key in _INT_FIELDS:
            cleaned[key] = int(raw) if _is_finite_number(raw) else None
        elif key in _FLOAT_FIELDS:
            cleaned[key] = float(raw) if _is_finite_number(raw) else None
        elif key == "recovery_attempts":
            cleaned[key] = (
                int(raw)
                if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0
                else 0
            )
        else:
            raise ValueError(f"unknown provider field {key}")
    return cleaned


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def is_valid_daily_start_time(hour: Any, minute: Any) -> bool:
    return (
        isinstance(hour, int)
        and not isinstance(hour, bool)
        and 0 <= hour <= 23
        and isinstance(minute, int)
        and not isinstance(minute, bool)
        and 0 <= minute <= 59
    )


class AppStateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_app_state_path()

    def load(self) -> AppSettings:
        payload = self._read()
        settings = payload.get("settings") if isinstance(payload, dict) else None
        if not isinstance(settings, dict):
            return AppSettings()
        identities = settings.get("compatible_runtime_identities")
        safe_identities = (
            {str(key): str(value) for key, value in identities.items()}
            if isinstance(identities, dict)
            else {}
        )
        checked = settings.get("checked_runtime_identities")
        safe_checked = (
            {str(key): str(value) for key, value in checked.items()}
            if isinstance(checked, dict)
            else {}
        )
        schedule_mode = settings.get("schedule_mode")
        if schedule_mode not in SCHEDULE_MODES:
            schedule_mode = CONTINUOUS
        daily_hour = settings.get("daily_start_hour")
        daily_minute = settings.get("daily_start_minute")
        if not is_valid_daily_start_time(daily_hour, daily_minute):
            daily_hour, daily_minute = 4, 0
        try:
            weekly_times = normalize_weekly_times(
                settings.get("weekly_start_times")
            )
        except ValueError:
            weekly_times = None
        return AppSettings(
            automation_enabled=settings.get("automation_enabled") is True,
            start_with_windows=settings.get("start_with_windows") is True,
            first_run_complete=settings.get("first_run_complete") is True,
            compatible_runtime_identities=safe_identities,
            checked_runtime_identities=safe_checked,
            schedule_mode=schedule_mode,
            daily_start_hour=daily_hour,
            daily_start_minute=daily_minute,
            weekly_start_times=weekly_times,
        )

    def load_provider_cache(self) -> dict[str, ProviderViewState]:
        payload = self._read()
        providers = payload.get("providers") if isinstance(payload, dict) else None
        if not isinstance(providers, dict):
            return {}
        result: dict[str, ProviderViewState] = {}
        for provider_id, value in providers.items():
            if not isinstance(provider_id, str) or not isinstance(value, dict):
                continue
            try:
                state = ProviderViewState(**_coerce_provider_fields(value))
            except (TypeError, ValueError):
                continue
            if state.provider_id == provider_id:
                result[provider_id] = state
        return result

    def save(
        self,
        settings: AppSettings,
        providers: dict[str, ProviderViewState],
    ) -> None:
        payload = {
            "schema_version": 1,
            "settings": asdict(settings),
            "providers": {key: asdict(value) for key, value in providers.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


def app_data_root() -> Path:
    """Return the local state directory, migrating the pre-rebrand folder once.

    The folder holds the one-shot provider guards. Abandoning it during a rename
    would silently reset those guards, so a legacy folder is moved when the new
    name is still free, and is used in place if the move cannot happen.
    """
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    current = root / PRODUCT.app_data_folder
    legacy = root / PRODUCT.legacy_app_data_folder
    if current.exists() or legacy == current or not legacy.is_dir():
        return current
    try:
        legacy.rename(current)
    except OSError:
        # Renaming can fail while another copy holds a file open. Keeping the
        # legacy folder preserves the guards; the next start retries.
        return legacy
    return current


def default_app_state_path() -> Path:
    return app_data_root() / "app-state.json"
