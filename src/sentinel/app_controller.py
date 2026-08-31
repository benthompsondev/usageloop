"""Qt-independent application coordination and fail-closed state transitions."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Protocol, Collection

from .app_state import (
    AppSettings,
    AppStateStore,
    AutomationDecision,
    ProviderViewState,
    automation_decision,
)
from .providers import CompatibilityResult


class DetectingProvider(Protocol):
    provider_id: str

    def detect(self) -> ProviderViewState: ...


class ApplicationController:
    def __init__(self, providers: Iterable[DetectingProvider], store: AppStateStore):
        self.providers = {provider.provider_id: provider for provider in providers}
        self.store = store
        self.settings = AppSettings()
        self.states: dict[str, ProviderViewState] = {}

    def start(self) -> None:
        self.settings = self.store.load()
        active_provider_ids = set(self.providers)
        self.settings = replace(
            self.settings,
            compatible_runtime_identities={
                key: value
                for key, value in (self.settings.compatible_runtime_identities or {}).items()
                if key in active_provider_ids
            },
            checked_runtime_identities={
                key: value
                for key, value in (self.settings.checked_runtime_identities or {}).items()
                if key in active_provider_ids
            },
        )
        cached = self.store.load_provider_cache()
        checked = self.settings.checked_runtime_identities or {}
        compatible = self.settings.compatible_runtime_identities or {}
        detected: dict[str, ProviderViewState] = {}
        for provider_id, provider in self.providers.items():
            try:
                state = provider.detect()
            except Exception:
                state = ProviderViewState(
                    provider_id,
                    provider_id.title(),
                    False,
                    False,
                    "Needs attention",
                    "Provider detection failed safely.",
                )
            previous = cached.get(provider_id)
            if (
                previous is not None
                and state.installed
                and previous.runtime_identity == state.runtime_identity
            ):
                if (
                    state.usage_checked_at is not None
                    and (
                        previous.usage_checked_at is None
                        or state.usage_checked_at > previous.usage_checked_at
                    )
                ):
                    pass
                elif previous.retry_after_restart:
                    state = replace(
                        state,
                        reset_at=previous.reset_at,
                        last_verified_at=previous.last_verified_at,
                        used_percent=previous.used_percent,
                        usage_checked_at=previous.usage_checked_at,
                        weekly_used_percent=previous.weekly_used_percent,
                        weekly_reset_at=previous.weekly_reset_at,
                    )
                else:
                    state = replace(
                        previous,
                        installed=True,
                        automation_supported=state.automation_supported,
                    )
            if (
                state.runtime_identity is not None
                and checked.get(provider_id) == state.runtime_identity
                and compatible.get(provider_id) != state.runtime_identity
            ):
                state = replace(
                    state,
                    automation_supported=False,
                    status="Needs attention",
                    detail=(
                        previous.detail
                        if previous is not None
                        else "Provider compatibility could not be confirmed, so automation is paused."
                    ),
                )
            detected[provider_id] = state
        self.states = detected
        self._save()

    def set_automation_enabled(self, enabled: bool) -> None:
        self.settings = replace(
            self.settings,
            automation_enabled=bool(enabled),
            first_run_complete=True,
        )
        self._save()

    def set_start_with_windows(self, enabled: bool) -> None:
        self.settings = replace(self.settings, start_with_windows=bool(enabled))
        self._save()

    def decisions(self, *, now: float) -> dict[str, AutomationDecision]:
        compatible = self.settings.compatible_runtime_identities or {}
        checked = self.settings.checked_runtime_identities or {}
        return {
            provider_id: automation_decision(
                self.settings.automation_enabled,
                state,
                now=now,
                compatible_runtime_identity=compatible.get(provider_id),
                checked_runtime_identity=checked.get(provider_id),
            )
            for provider_id, state in self.states.items()
        }

    def refresh_local_states(self, *, exclude: Collection[str] = ()) -> None:
        """Refresh executable identity and local caches without provider traffic."""
        changed = False
        excluded = set(exclude)
        for provider_id, provider in self.providers.items():
            if provider_id in excluded:
                continue
            current = self.states.get(provider_id)
            try:
                detected = provider.detect()
            except Exception:
                continue
            if current is None or current.runtime_identity != detected.runtime_identity:
                self.states[provider_id] = detected
                changed = True
                continue
            if current.status == "Needs attention" and not current.automation_supported:
                continue
            if (
                detected.usage_checked_at is not None
                and (
                    current.usage_checked_at is None
                    or detected.usage_checked_at > current.usage_checked_at
                )
            ):
                self.states[provider_id] = replace(
                    detected,
                    automation_blocked_until=current.automation_blocked_until,
                    last_action=current.last_action,
                )
                changed = True
        if changed:
            self._save()

    def apply_compatibility(
        self, provider_id: str, result: CompatibilityResult
    ) -> None:
        state = self.states[provider_id]
        checked = dict(self.settings.checked_runtime_identities or {})
        checked[provider_id] = result.runtime_identity
        compatible = dict(self.settings.compatible_runtime_identities or {})
        if result.compatible:
            compatible[provider_id] = result.runtime_identity
        elif compatible.get(provider_id) == result.runtime_identity:
            compatible.pop(provider_id, None)
        self.settings = replace(
            self.settings,
            compatible_runtime_identities=compatible,
            checked_runtime_identities=checked,
        )
        self.states[provider_id] = replace(
            state,
            automation_supported=result.compatible,
            status=result.status,
            detail=result.detail,
            runtime_identity=result.runtime_identity,
        )
        self._save()

    def update_provider_state(self, state: ProviderViewState) -> None:
        self.states[state.provider_id] = state
        self._save()

    def _save(self) -> None:
        self.store.save(self.settings, self.states)
