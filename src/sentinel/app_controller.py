"""Qt-independent application coordination and fail-closed state transitions."""

from __future__ import annotations

from dataclasses import replace
import sys
import time
import uuid
from typing import Iterable, Protocol, Collection, Sequence

from .app_state import (
    AppSettings,
    AppStateStore,
    AutomationDecision,
    ProviderViewState,
    automation_decision,
    is_valid_daily_start_time,
)
from .providers import CompatibilityResult
from .schedule import SCHEDULE_MODES, WEEKLY, normalize_weekly_times
from .schedule import schedule_summary


RECOVERY_INITIAL_SECONDS = 60
RECOVERY_MAX_SECONDS = 15 * 60
COMPATIBILITY_RETRY_DELAYS = (60, 120, 300, 600, 900)
COMPATIBILITY_EPISODE_SECONDS = 60 * 60
TRANSIENT_COMPATIBILITY_FAILURES = frozenset({"app_server_unavailable", "app_server_timeout"})


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
                        compatibility_incident_id=previous.compatibility_incident_id,
                        compatibility_failure_category=previous.compatibility_failure_category,
                        compatibility_attempts=previous.compatibility_attempts,
                        compatibility_started_at=previous.compatibility_started_at,
                        compatibility_next_retry_at=previous.compatibility_next_retry_at,
                        compatibility_notified=previous.compatibility_notified,
                        compatibility_blocked_notified=previous.compatibility_blocked_notified,
                        compatibility_terminal_notified=previous.compatibility_terminal_notified,
                        compatibility_blocked_opportunity=previous.compatibility_blocked_opportunity,
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
                        automation_supported=(
                            previous.automation_supported
                            if previous.compatibility_incident_id else state.automation_supported
                        ),
                    )
            if (
                state.runtime_identity is not None
                and checked.get(provider_id) == state.runtime_identity
                and compatible.get(provider_id) != state.runtime_identity
            ):
                state = replace(
                    state,
                    automation_supported=False,
                    status=(previous.status if previous is not None and previous.compatibility_incident_id
                            else "Needs attention"),
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
            automation_paused_until=(self.settings.automation_paused_until if enabled else None),
        )
        previous_states = self.states
        if not enabled:
            self._stop_compatibility_retries()
        if self._save_settings(candidate):
            return True
        self.states = previous_states
        return False

    def pause_until_tomorrow(self, *, now: float) -> bool:
        if not self.settings.automation_enabled or self.settings.pause_active(now):
            return False
        target = self.settings.tomorrow_first_start(now)
        if target is None:
            return False
        previous_states = self.states
        self._stop_compatibility_retries()
        if self._save_settings(replace(self.settings, automation_paused_until=target)):
            return True
        self.states = previous_states
        return False

    def _stop_compatibility_retries(self) -> None:
        self.states = {
            provider_id: (replace(state, status="Needs attention",
                                  detail="Read-only recovery stopped. Recheck Codex when ready.",
                                  compatibility_next_retry_at=None)
                          if state.compatibility_next_retry_at is not None else state)
            for provider_id, state in self.states.items()
        }

    def resume_automation(self) -> bool:
        # Removing a pause never turns the main automation switch on.
        return self._save_settings(replace(self.settings, automation_paused_until=None))

    def set_start_with_windows(self, enabled: bool) -> bool:
        return self._save_settings(
            replace(self.settings, start_with_windows=bool(enabled))
        )

    def set_schedule_mode(self, mode: str) -> bool:
        if mode not in SCHEDULE_MODES:
            raise ValueError(f"unsupported schedule mode: {mode}")
        weekly_times = self.settings.weekly_start_times
        if mode == WEEKLY and weekly_times is None:
            weekly_times = (
                (self.settings.daily_start_hour, self.settings.daily_start_minute),
            ) * 7
        return self._save_settings(
            replace(
                self.settings,
                schedule_mode=mode,
                weekly_start_times=weekly_times,
            )
        )

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

    def set_weekly_start_times(
        self, values: Sequence[tuple[int, int]]
    ) -> bool:
        weekly_times = normalize_weekly_times(values)
        return self._save_settings(
            replace(self.settings, weekly_start_times=weekly_times)
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
                weekly_times=self.settings.weekly_start_times,
                paused_until=self.settings.automation_paused_until,
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
            if current.compatibility_incident_id and current.runtime_identity == detected.runtime_identity:
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
        self, provider_id: str, result: CompatibilityResult, *,
        now: float | None = None, explicit: bool = False,
    ) -> bool:
        current_time = time.time() if now is None else now
        previous_settings = self.settings
        previous_state = self.states[provider_id]
        state = self.states[provider_id]
        if result.runtime_identity != state.runtime_identity:
            return False
        if not explicit and state.compatibility_attempts >= 6 and not result.compatible:
            return False
        incident_id = None if explicit else state.compatibility_incident_id
        if not result.compatible and incident_id is None:
            incident_id = uuid.uuid4().hex
        attempts = (state.compatibility_attempts + 1
                    if incident_id == state.compatibility_incident_id else 1)
        started = (state.compatibility_started_at
                   if incident_id == state.compatibility_incident_id and state.compatibility_started_at is not None
                   else current_time)
        category = result.failure_category or "capability_unavailable"
        retryable = category in TRANSIENT_COMPATIBILITY_FAILURES
        next_retry = None
        if (not result.compatible and retryable and self.settings.automation_enabled
                and not self.settings.pause_active(current_time)
                and attempts <= len(COMPATIBILITY_RETRY_DELAYS)
                and current_time < started + COMPATIBILITY_EPISODE_SECONDS):
            candidate = current_time + COMPATIBILITY_RETRY_DELAYS[attempts - 1]
            if candidate < started + COMPATIBILITY_EPISODE_SECONDS:
                next_retry = candidate
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
            status=(result.status if result.compatible else
                    "Reconnecting" if next_retry is not None else "Needs attention"),
            detail=(result.detail if result.compatible or not retryable else
                    "Codex is temporarily unavailable. UsageLoop is retrying read-only checks."
                    if next_retry is not None else
                    "Codex did not reconnect in the retry window. Recheck it when ready."),
            runtime_identity=result.runtime_identity,
            compatibility_incident_id=None if result.compatible else incident_id,
            compatibility_failure_category=None if result.compatible else category,
            compatibility_attempts=0 if result.compatible else attempts,
            compatibility_started_at=None if result.compatible else started,
            compatibility_next_retry_at=next_retry,
            compatibility_notified=(False if result.compatible or incident_id != state.compatibility_incident_id
                                    else state.compatibility_notified),
            compatibility_blocked_notified=(False if result.compatible or incident_id != state.compatibility_incident_id
                                            else state.compatibility_blocked_notified),
            compatibility_terminal_notified=(False if result.compatible or incident_id != state.compatibility_incident_id
                                             else state.compatibility_terminal_notified),
            compatibility_blocked_opportunity=(None if result.compatible or incident_id != state.compatibility_incident_id
                                               else state.compatibility_blocked_opportunity),
        )
        if self._save():
            event = ("recovered" if result.compatible and state.compatibility_incident_id else
                     "retry" if not result.compatible and attempts > 1 else
                     "failed" if not result.compatible else None)
            if event is not None:
                if not self._record_compatibility_event(
                    state.compatibility_incident_id if event == "recovered" else incident_id,
                    result.runtime_identity,
                    state.compatibility_failure_category if event == "recovered" else category,
                    event, current_time, attempts, next_retry,
                ):
                    return False
            return True
        self.settings = previous_settings
        self.states[provider_id] = previous_state
        return False

    def expire_compatibility(self, *, now: float) -> list[str]:
        if not self.settings.automation_enabled or self.settings.pause_active(now):
            return []
        exhausted: list[str] = []
        for provider_id, state in list(self.states.items()):
            if (state.compatibility_next_retry_at is None or state.compatibility_started_at is None
                    or now < state.compatibility_started_at + COMPATIBILITY_EPISODE_SECONDS):
                continue
            applied = replace(state, status="Needs attention",
                              detail="Codex did not reconnect in the retry window. Recheck it when ready.",
                              compatibility_next_retry_at=None)
            if self.update_provider_state(applied) and self._record_compatibility_event(
                state.compatibility_incident_id, state.runtime_identity,
                state.compatibility_failure_category, "exhausted", now,
                state.compatibility_attempts, None,
            ):
                exhausted.append(provider_id)
        return exhausted

    def record_blocked_opportunity(self, provider_id: str, *, now: float) -> bool:
        state = self.states[provider_id]
        if (not self.settings.automation_enabled or self.settings.pause_active(now)
                or state.compatibility_incident_id is None or state.reset_at is None):
            return False
        try:
            summary = schedule_summary(
                self.settings.schedule_mode, boundary_reset_at=state.reset_at, now=now,
                hour=self.settings.daily_start_hour, minute=self.settings.daily_start_minute,
                weekly_times=self.settings.weekly_start_times,
            )
        except (OSError, OverflowError, ValueError):
            return False
        if not summary.due or summary.next_action_at is None:
            return False
        key = f"{self.settings.schedule_mode}:{int(summary.next_action_at)}"
        if key == state.compatibility_blocked_opportunity:
            return False
        if not self._record_compatibility_event(
            state.compatibility_incident_id, state.runtime_identity,
            state.compatibility_failure_category, "blocked_start", now,
            state.compatibility_attempts, state.compatibility_next_retry_at,
            opportunity_at=summary.next_action_at,
        ):
            return False
        return self.update_provider_state(replace(state, compatibility_blocked_opportunity=key))

    def mark_compatibility_notified(self, provider_id: str, *, kind: str) -> bool:
        state = self.states[provider_id]
        if kind not in {"blocked", "terminal"}:
            raise ValueError("Unsupported compatibility notification kind.")
        if (state.compatibility_incident_id is None
                or (kind == "blocked" and state.compatibility_blocked_notified)
                or (kind == "terminal" and state.compatibility_terminal_notified)):
            return False
        return self.update_provider_state(replace(
            state, compatibility_notified=True,
            compatibility_blocked_notified=(kind == "blocked" or state.compatibility_blocked_notified),
            compatibility_terminal_notified=(kind == "terminal" or state.compatibility_terminal_notified),
        ))

    def _record_compatibility_event(self, incident_id, runtime_identity, category,
                                    event, now, attempts, next_retry, opportunity_at=None) -> bool:
        if self.error_history is None or not hasattr(self.error_history, "record_compatibility_event"):
            return True
        try:
            self.error_history.record_compatibility_event(
                incident_id=incident_id, runtime_identity=runtime_identity,
                category=category, event=event, now=now, attempts=attempts,
                next_retry_at=next_retry, opportunity_at=opportunity_at,
            )
        except (OSError, RuntimeError):
            self.persistence_error = "history_write_failed"
            return False
        return True

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

    def apply_sync_result(self, state: ProviderViewState) -> bool:
        current = self.states.get(state.provider_id)
        if current is None or current.runtime_identity != state.runtime_identity:
            return False
        if current.compatibility_incident_id is not None:
            state = replace(
                current,
                reset_at=state.reset_at,
                last_verified_at=state.last_verified_at,
                used_percent=state.used_percent,
                usage_checked_at=state.usage_checked_at,
                weekly_used_percent=state.weekly_used_percent,
                weekly_reset_at=state.weekly_reset_at,
                quota_state=state.quota_state,
                quota_evidence=state.quota_evidence,
            )
        return self.update_provider_state(state)

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
