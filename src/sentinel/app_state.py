"""Presentation-safe state and local automation decisions for the desktop app."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
from typing import Any

from .product import PRODUCT


@dataclass(frozen=True)
class AppSettings:
    automation_enabled: bool = False
    start_with_windows: bool = False
    first_run_complete: bool = False
    compatible_runtime_identities: dict[str, str] | None = None
    checked_runtime_identities: dict[str, str] | None = None

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
    used_percent: int | None = None
    usage_checked_at: float | None = None

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
) -> AutomationDecision:
    if not enabled:
        return AutomationDecision("NONE", "Automation is off.")
    if not state.installed or not state.automation_supported:
        return AutomationDecision("NONE", "Provider automation is unavailable.")
    if state.status == "Needs attention":
        return AutomationDecision("NONE", "Provider needs explicit attention.")
    if state.runtime_identity != compatible_runtime_identity:
        if state.runtime_identity == checked_runtime_identity:
            return AutomationDecision("NONE", "Provider compatibility needs attention.")
        return AutomationDecision("PROBE", "Provider runtime capabilities must be checked.")
    if state.reset_at is None:
        return AutomationDecision("BOOTSTRAP", "No verified window is known yet.")
    if now >= state.reset_at + 15:
        return AutomationDecision("ROLLOVER", "The verified reset boundary has passed.")
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
        return AppSettings(
            automation_enabled=settings.get("automation_enabled") is True,
            start_with_windows=settings.get("start_with_windows") is True,
            first_run_complete=settings.get("first_run_complete") is True,
            compatible_runtime_identities=safe_identities,
            checked_runtime_identities=safe_checked,
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
                state = ProviderViewState(**value)
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
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


def default_app_state_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return root / PRODUCT.app_data_folder / "app-state.json"
