"""Qt-independent application coordination and fail-closed state transitions."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Protocol

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
        cached = self.store.load_provider_cache()
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
                state = replace(
                    previous,
                    installed=True,
                    automation_supported=state.automation_supported,
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
