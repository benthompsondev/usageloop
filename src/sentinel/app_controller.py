"""Qt-independent application coordination and fail-closed state transitions."""

from __future__ import annotations

from dataclasses import replace
import sys
from typing import Iterable, Protocol, Collection

from .app_state import (
    AppSettings,
    AppStateStore,
    AutomationDecision,
    ProviderViewState,
    automation_decision,
    is_valid_daily_start_time,
)
from .providers import CompatibilityResult
from .schedule import SCHEDULE_MODES


RECOVERY_INITIAL_SECONDS = 60
RECOVERY_MAX_SECONDS = 15 * 60


class DetectingProvider(Protocol):
    provider_id: str

    def detect(self) -> ProviderViewState: ...


class ErrorHistory(Protocol):
    def record_error(self, category: str) -> None: ...


class ApplicationController:
    def __init__(
        self,
        providers: Iterable[DetectingProvider],
        store: AppStateStore,
        *,
        error_history: ErrorHistory | None = None,
    ):
        self.providers = {provider.provider_id: provider for provider in providers}
        self.store = store
        self.error_history = error_history
        self.settings = AppSettings()
        self.states: dict[str, ProviderViewState] = {}
        self.persistence_error: str | None = None

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
                        or (
                            state.usage_checked_at == previous.usage_checked_at
                            and (
                                (
                                    state.quota_state == "EXHAUSTED"
                                    and previous.quota_state is None
                                )
                                or (
                                    previous.status == "Needs attention"
                                    and state.status != "Needs attention"
                                )
                            )
                        )
                    )
                ):
                    same_evidence = _provider_evidence(state) == _provider_evidence(previous)
                    state = replace(
                        state,
                        last_action=previous.last_action,
                        automation_blocked_until=previous.automation_blocked_until,
                        recovery_signature=(
                            previous.recovery_signature if same_evidence else None
                        ),
                        recovery_attempts=(
                            previous.recovery_attempts if same_evidence else 0
                        ),
                        recovery_not_before=(
                            previous.recovery_not_before if same_evidence else None
                        ),
                    )
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

    def set_automation_enabled(self, enabled: bool) -> bool:
        candidate = replace(
            self.settings,
            automation_enabled=bool(enabled),
            first_run_complete=True,
        )
        return self._save_settings(candidate)

    def set_start_with_windows(self, enabled: bool) -> bool:
        return self._save_settings(
            replace(self.settings, start_with_windows=bool(enabled))
        )

    def set_schedule_mode(self, mode: str) -> bool:
        if mode not in SCHEDULE_MODES:
            raise ValueError(f"unsupported schedule mode: {mode}")
        return self._save_settings(replace(self.settings, schedule_mode=mode))

    def set_daily_start_time(self, hour: int, minute: int) -> bool:
        if not is_valid_daily_start_time(hour, minute):
            raise ValueError("daily start time requires integer hour and minute values")
        return self._save_settings(
            replace(
                self.settings,
                daily_start_hour=hour,
                daily_start_minute=minute,
            )
        )

    def decisions(self, *, now: float) -> dict[str, AutomationDecision]:
        if self.persistence_error is not None:
            return {
                provider_id: AutomationDecision(
                    "WAIT", "Local state could not be saved safely."
                )
                for provider_id in self.states
            }
        compatible = self.settings.compatible_runtime_identities or {}
        checked = self.settings.checked_runtime_identities or {}
        return {
            provider_id: automation_decision(
                self.settings.automation_enabled,
                state,
                now=now,
                compatible_runtime_identity=compatible.get(provider_id),
                checked_runtime_identity=checked.get(provider_id),
                schedule_mode=self.settings.schedule_mode,
                daily_hour=self.settings.daily_start_hour,
                daily_minute=self.settings.daily_start_minute,
            )
            for provider_id, state in self.states.items()
        }

    def refresh_local_states(self, *, exclude: Collection[str] = ()) -> None:
        """Refresh executable identity and local caches without provider traffic."""
        previous_states = dict(self.states)
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
                    or (
                        detected.usage_checked_at == current.usage_checked_at
                        and (
                            (
                                detected.quota_state == "EXHAUSTED"
                                and current.quota_state is None
                            )
                            or (
                                current.status == "Needs attention"
                                and detected.status != "Needs attention"
                            )
                        )
                    )
                )
            ):
                same_evidence = _provider_evidence(detected) == _provider_evidence(current)
                self.states[provider_id] = replace(
                    detected,
                    automation_blocked_until=current.automation_blocked_until,
                    last_action=current.last_action,
                    recovery_signature=(
                        current.recovery_signature if same_evidence else None
                    ),
                    recovery_attempts=(
                        current.recovery_attempts if same_evidence else 0
                    ),
                    recovery_not_before=(
                        current.recovery_not_before if same_evidence else None
                    ),
                )
                changed = True
        if changed:
            if not self._save():
                self.states = previous_states

    def apply_compatibility(
        self, provider_id: str, result: CompatibilityResult
    ) -> bool:
        previous_settings = self.settings
        previous_state = self.states[provider_id]
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
        if self._save():
            return True
        self.settings = previous_settings
        self.states[provider_id] = previous_state
        return False

    def update_provider_state(self, state: ProviderViewState) -> bool:
        previous = self.states.get(state.provider_id)
        self.states[state.provider_id] = state
        if self._save():
            return True
        if previous is None:
            self.states.pop(state.provider_id, None)
        else:
            self.states[state.provider_id] = previous
        return False

    def apply_operation_result(
        self,
        outcome: str,
        state: ProviderViewState,
        *,
        now: float,
    ) -> ProviderViewState:
        """Persist a chain result with a bounded read-only recovery cadence."""
        from .provider_runtime import chain_outcome_policy

        policy = chain_outcome_policy(outcome)
        if not policy.read_only_recovery:
            applied = replace(
                state,
                recovery_signature=None,
                recovery_attempts=0,
                recovery_not_before=None,
            )
        else:
            signature = _recovery_signature(outcome, state)
            previous = self.states.get(state.provider_id)
            attempts = (
                previous.recovery_attempts + 1
                if previous is not None
                and previous.recovery_signature == signature
                else 1
            )
            delay = min(
                RECOVERY_MAX_SECONDS,
                RECOVERY_INITIAL_SECONDS * (2 ** (attempts - 1)),
            )
            applied = replace(
                state,
                recovery_signature=signature,
                recovery_attempts=attempts,
                recovery_not_before=float(now) + delay,
            )
        if self.update_provider_state(applied):
            return applied
        return self.states.get(state.provider_id, applied)

    def _save_settings(self, candidate: AppSettings) -> bool:
        previous = self.settings
        self.settings = candidate
        if self._save():
            return True
        self.settings = previous
        return False

    def _save(self) -> bool:
        try:
            self.store.save(self.settings, self.states)
        # OSError covers the filesystem path. RuntimeError covers an explicit
        # storage-backend failure without hiding programming errors such as
        # AttributeError, AssertionError, or NameError.
        except (OSError, RuntimeError):
            self.persistence_error = "state_write_failed"
            self._record_persistence_error()
            return False
        self.persistence_error = None
        return True

    def _record_persistence_error(self) -> None:
        if self.error_history is not None:
            try:
                self.error_history.record_error("state_write_failed")
                return
            except Exception:
                pass
        try:
            if sys.stderr is not None:
                print("UsageLoop: state_write_failed", file=sys.stderr)
        except Exception:
            pass


def _recovery_signature(outcome: str, state: ProviderViewState) -> str:
    values = (
        outcome,
        state.quota_state,
        state.reset_at,
        state.used_percent,
        state.weekly_used_percent,
        state.weekly_reset_at,
    )
    return "|".join("" if value is None else str(value) for value in values)


def _provider_evidence(state: ProviderViewState) -> tuple[object, ...]:
    return (
        state.quota_state,
        state.reset_at,
        state.used_percent,
        state.weekly_used_percent,
        state.weekly_reset_at,
    )
