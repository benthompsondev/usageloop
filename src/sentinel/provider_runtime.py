"""In-process Codex operations for the windowed desktop application."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Protocol

from .app_state import ProviderViewState
from .chain import ChainCoordinator, ChainPolicy
from .classifier import classify
from .history import SafeHistory
from .models import select_trigger_model
from .providers import CompatibilityResult
from .quota import QuotaSnapshot, normalize_rate_limits, select_five_hour, select_weekly
from .trigger import AppServerTrigger, TriggerConfig, dedicated_trigger_workspace


class RuntimeSession(Protocol):
    codex_version: str
    client: object

    def close(self) -> None: ...


@dataclass(frozen=True)
class ProviderOperationResult:
    outcome: str
    state: ProviderViewState
    request_possibly_sent: bool


class CodexOperationRunner:
    def __init__(
        self,
        history: SafeHistory,
        *,
        session_factory: Callable[[], RuntimeSession] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.history = history
        if session_factory is None:
            from .cli import connect

            session_factory = connect
        self._session_factory = session_factory
        self._clock = clock
        self._sleep = sleep

    def probe(self, runtime_identity: str) -> CompatibilityResult:
        session = None
        try:
            session = self._session_factory()
            observations: list[QuotaSnapshot] = []
            for index in range(4):
                observed_at = self._clock()
                raw = session.client.read_rate_limits()
                session.client.drain_rate_limit_notifications()
                observations.append(normalize_rate_limits(raw, observed_at))
                if index < 3:
                    self._sleep(10.0)
            models = session.client.list_models()
            choice = select_trigger_model(models)
            classification = classify(observations)
            for snapshot in observations:
                self.history.record_observation(
                    snapshot, classification, session.codex_version
                )
            return CompatibilityResult.from_capabilities(
                runtime_identity=runtime_identity,
                initialized=True,
                rate_limits_available=any(snapshot.windows for snapshot in observations),
                model_catalog_available=bool(models),
                suitable_model_available=choice is not None,
            )
        except Exception:
            return CompatibilityResult(
                False,
                "Needs attention",
                "The Codex compatibility check failed safely. No automatic request was sent.",
                runtime_identity,
            )
        finally:
            if session is not None:
                session.close()

    def sync(self, runtime_identity: str) -> ProviderOperationResult:
        """Refresh quota evidence without discovering models or starting a turn."""
        session = self._session_factory()
        try:
            observations: list[QuotaSnapshot] = []
            for index in range(4):
                observed_at = self._clock()
                raw = session.client.read_rate_limits()
                session.client.drain_rate_limit_notifications()
                observations.append(normalize_rate_limits(raw, observed_at))
                if index < 3:
                    self._sleep(10.0)
            classification = classify(observations)
            for snapshot in observations:
                self.history.record_observation(
                    snapshot, classification, session.codex_version
                )
            latest = observations[-1]
            selected = select_five_hour(latest).window
            weekly = select_weekly(latest)
            conclusive = classification.state in {
                "ANCHORED",
                "UNANCHORED",
                "EXHAUSTED",
            }
            status = {
                "ANCHORED": "Ready",
                "UNANCHORED": "Waiting",
                "EXHAUSTED": "Waiting",
            }.get(classification.state, "Needs attention")
            detail = {
                "ANCHORED": "Codex usage was updated from a fixed reset clock.",
                "UNANCHORED": "Codex usage was updated; no fixed reset clock is active.",
                "EXHAUSTED": "Codex reports that the five-hour window is exhausted.",
            }.get(classification.state, "Codex usage could not be confirmed safely.")
            state = ProviderViewState(
                provider_id="codex",
                display_name="Codex",
                installed=True,
                automation_supported=True,
                status=status,
                detail=detail,
                runtime_identity=runtime_identity,
                reset_at=selected.resets_at if selected else None,
                last_verified_at=(
                    latest.observed_at if classification.state == "ANCHORED" else None
                ),
                used_percent=selected.used_percent if selected else None,
                usage_checked_at=latest.observed_at,
                weekly_used_percent=weekly.used_percent if weekly else None,
                weekly_reset_at=weekly.resets_at if weekly else None,
                quota_state=classification.state,
            )
            return ProviderOperationResult(
                "SYNC_UPDATED" if conclusive else "SYNC_INCONCLUSIVE",
                state,
                False,
            )
        finally:
            session.close()

    def run(self, mode: str, *, runtime_identity: str) -> ProviderOperationResult:
        if mode not in {"bootstrap", "rollover"}:
            raise ValueError("Unsupported Codex operation mode.")
        session = self._session_factory()
        all_observations: list[QuotaSnapshot] = []
        try:
            trigger = AppServerTrigger(
                session.client,
                dedicated_trigger_workspace(self.history.path),
                TriggerConfig(),
            )

            def collect() -> list[QuotaSnapshot]:
                observations: list[QuotaSnapshot] = []
                for index in range(4):
                    observed_at = self._clock()
                    raw = session.client.read_rate_limits()
                    session.client.drain_rate_limit_notifications()
                    observations.append(normalize_rate_limits(raw, observed_at))
                    if index < 3:
                        self._sleep(10.0)
                classification = classify(observations)
                for snapshot in observations:
                    self.history.record_observation(
                        snapshot, classification, session.codex_version
                    )
                all_observations.extend(observations)
                return observations

            preflight = collect()
            coordinator = ChainCoordinator(trigger, self.history, ChainPolicy())
            if mode == "bootstrap":
                result = coordinator.run_bootstrap(
                    preflight, collect, confirmed=True, dry_run=False
                )
            else:
                result = coordinator.run(preflight, collect, dry_run=False)
            state = self._state_from_result(
                result.status,
                result.reason,
                result.classification.state,
                all_observations,
                runtime_identity,
            )
            return ProviderOperationResult(
                result.status, state, result.request_possibly_sent
            )
        finally:
            session.close()

    @staticmethod
    def _state_from_result(
        outcome: str,
        reason: str,
        classification_state: str,
        observations: list[QuotaSnapshot],
        runtime_identity: str,
    ) -> ProviderViewState:
        latest = observations[-1] if observations else None
        selected = select_five_hour(latest).window if latest is not None else None
        weekly = select_weekly(latest) if latest is not None else None
        ready = outcome in {"ALREADY_ANCHORED", "ANCHOR_VERIFIED"}
        waiting = outcome in {"RESET_BUFFER", "NOT_ELIGIBLE"} and classification_state != "UNKNOWN"
        return ProviderViewState(
            provider_id="codex",
            display_name="Codex",
            installed=True,
            automation_supported=True,
            status="Ready" if ready else ("Waiting" if waiting else "Needs attention"),
            detail=reason,
            runtime_identity=runtime_identity,
            runtime_version=None,
            reset_at=selected.resets_at if selected else None,
            last_verified_at=(latest.observed_at if ready and latest else None),
            last_action=outcome.replace("_", " ").title(),
            used_percent=selected.used_percent if selected else None,
            usage_checked_at=latest.observed_at if latest else None,
            weekly_used_percent=weekly.used_percent if weekly else None,
            weekly_reset_at=weekly.resets_at if weekly else None,
            quota_state=classification_state,
        )
