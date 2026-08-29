"""Bounded Codex rollover and explicit first-window bootstrap coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
import uuid

from .classifier import Classification, classify
from .history import SafeHistory, TriggerAttempt
from .quota import QuotaSnapshot, QuotaWindow, select_five_hour
from .trigger import Trigger, TriggerDescription, TriggerRunResult


@dataclass(frozen=True)
class ChainPolicy:
    reset_buffer_seconds: float = 15.0
    weekly_protection_percent: int = 99
    bootstrap_cooldown_seconds: float = 18_000.0
    reservation_recovery_seconds: float = 120.0


@dataclass(frozen=True)
class ChainResult:
    status: str
    reason: str
    classification: Classification
    mode: str
    request_possibly_sent: bool
    boundary_reset_at: int | None
    trigger: TriggerDescription
    attempt_state: str | None = None
    terminal_outcome: str | None = None

    @property
    def anchored(self) -> bool:
        return self.classification.state == "ANCHORED"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "mode": self.mode,
            "anchored": self.anchored,
            "request_possibly_sent": self.request_possibly_sent,
            "boundary_reset_at": self.boundary_reset_at,
            "attempt_state": self.attempt_state,
            "terminal_outcome": self.terminal_outcome,
            "classification": {
                "state": self.classification.state,
                "confidence": self.classification.confidence,
                "reason": self.classification.reason,
                "evidence": self.classification.evidence,
            },
            "trigger": {
                "mechanism": self.trigger.mechanism,
                "model": self.trigger.model,
                "reasoning_effort": self.trigger.reasoning_effort,
                "prompt_characters": self.trigger.prompt_characters,
            },
        }


class ChainCoordinator:
    def __init__(self, trigger: Trigger, history: SafeHistory, policy: ChainPolicy):
        self.trigger = trigger
        self.history = history
        self.policy = policy

    def run(
        self,
        observations: Sequence[QuotaSnapshot],
        collect_verification: Callable[[], Sequence[QuotaSnapshot]],
        *,
        dry_run: bool = False,
    ) -> ChainResult:
        return self._run(
            "rollover",
            observations,
            collect_verification,
            confirmed=True,
            dry_run=dry_run,
        )

    def run_bootstrap(
        self,
        observations: Sequence[QuotaSnapshot],
        collect_verification: Callable[[], Sequence[QuotaSnapshot]],
        *,
        confirmed: bool,
        dry_run: bool = False,
    ) -> ChainResult:
        return self._run(
            "bootstrap",
            observations,
            collect_verification,
            confirmed=confirmed,
            dry_run=dry_run,
        )

    def _run(
        self,
        mode: str,
        observations: Sequence[QuotaSnapshot],
        collect_verification: Callable[[], Sequence[QuotaSnapshot]],
        *,
        confirmed: bool,
        dry_run: bool,
    ) -> ChainResult:
        preflight = classify(observations)
        description = self.trigger.describe()
        if preflight.state == "ANCHORED":
            return self._result(
                "ALREADY_ANCHORED",
                "The five-hour window is already anchored; no request was sent.",
                preflight,
                mode,
                False,
                None,
                description,
            )
        if preflight.state != "UNANCHORED":
            return self._result(
                "NOT_ELIGIBLE",
                "A trigger requires conservative UNANCHORED evidence.",
                preflight,
                mode,
                False,
                None,
                description,
            )
        if not _strong_evidence(preflight):
            return self._result(
                "EVIDENCE_TOO_WEAK",
                "Quota-consuming actions require four observations spanning at least 30 seconds.",
                preflight,
                mode,
                False,
                None,
                description,
            )
        if mode == "bootstrap" and not confirmed and not dry_run:
            return self._result(
                "CONSENT_REQUIRED",
                "Starting the first window requires explicit confirmation.",
                preflight,
                mode,
                False,
                None,
                description,
            )

        current = max(observations, key=lambda item: item.observed_at)
        if mode == "bootstrap" and not _bootstrap_usage_suitable(observations):
            return self._result(
                "BOOTSTRAP_USAGE_UNSUITABLE",
                "Bootstrap requires every selected five-hour observation to report zero percent used.",
                preflight,
                mode,
                False,
                None,
                description,
            )

        weekly = _select_weekly(current)
        if weekly is None:
            return self._result(
                "WEEKLY_UNAVAILABLE",
                "The weekly Codex window is absent or ambiguous, so Sentinel will not consume quota.",
                preflight,
                mode,
                False,
                None,
                description,
            )
        if weekly.used_percent >= self.policy.weekly_protection_percent or weekly.blocked_reason:
            return self._result(
                "WEEKLY_EXHAUSTED",
                "Weekly protection blocked the trigger.",
                preflight,
                mode,
                False,
                None,
                description,
            )

        if mode == "rollover":
            boundary = self.history.latest_anchored_reset_before(current.observed_at)
            if boundary is None:
                return self._result(
                    "ROLLOVER_BOUNDARY_UNKNOWN",
                    "No recent anchored reset proves that a genuine rollover occurred.",
                    preflight,
                    mode,
                    False,
                    None,
                    description,
                )
            if current.observed_at < boundary + self.policy.reset_buffer_seconds:
                return self._result(
                    "RESET_BUFFER",
                    "The known rollover has not cleared the configured reset buffer.",
                    preflight,
                    mode,
                    False,
                    boundary,
                    description,
                )
            idempotency_key = f"rollover:{boundary}"
            blocking = self._blocking_rollover_attempt(idempotency_key, boundary, current.observed_at)
            if blocking is not None:
                return self._result(
                    "ATTEMPT_ALREADY_RECORDED",
                    "A request may already have been sent for this rollover; Sentinel will not send another.",
                    preflight,
                    mode,
                    False,
                    boundary,
                    description,
                    attempt_state=blocking.state,
                )
        else:
            boundary = None
            blocking = self._blocking_bootstrap_attempt(current.observed_at)
            if blocking is not None:
                return self._result(
                    "BOOTSTRAP_COOLDOWN",
                    "A bootstrap request may have been sent within one full five-hour window.",
                    preflight,
                    mode,
                    False,
                    None,
                    description,
                    attempt_state=blocking.state,
                )
            idempotency_key = f"bootstrap:{uuid.uuid4().hex}"

        if dry_run:
            return self._result(
                "DRY_RUN",
                f"Eligible {mode} detected; dry-run created no reservation and sent no request.",
                preflight,
                mode,
                False,
                boundary,
                description,
            )

        attempt = self.history.reserve_trigger(
            mode=mode,
            idempotency_key=idempotency_key,
            boundary_reset_at=boundary,
            model=description.model,
            reasoning_effort=description.reasoning_effort,
            now=current.observed_at,
        )
        self.history.transition_trigger(
            attempt.attempt_id, "launch_attempted", now=current.observed_at
        )
        try:
            trigger_result = self.trigger.run()
        except Exception:
            trigger_result = TriggerRunResult("runtime_error", True)

        if not trigger_result.request_possibly_sent:
            self.history.transition_trigger(
                attempt.attempt_id,
                "failed_recoverable",
                outcome=trigger_result.terminal_outcome,
                observed_state=preflight.state,
                now=current.observed_at,
            )
            return self._result(
                "TRIGGER_NOT_SENT",
                "Codex did not launch, so this opportunity remains recoverable.",
                preflight,
                mode,
                False,
                boundary,
                description,
                attempt_state="failed_recoverable",
                terminal_outcome=trigger_result.terminal_outcome,
            )

        self.history.transition_trigger(
            attempt.attempt_id,
            "request_possibly_sent",
            outcome=trigger_result.terminal_outcome,
            observed_state=preflight.state,
            now=current.observed_at,
        )
        try:
            verification = list(collect_verification())
            verified = classify(verification)
            verified_at = (
                max(verification, key=lambda item: item.observed_at).observed_at
                if verification
                else current.observed_at
            )
        except Exception:
            self.history.transition_trigger(
                attempt.attempt_id,
                "failed_guarded",
                outcome="verification_unavailable",
                observed_state="UNKNOWN",
                now=current.observed_at,
            )
            return self._result(
                "VERIFICATION_UNAVAILABLE",
                "A request may have been sent, but read-only verification was unavailable; no retry is allowed.",
                preflight,
                mode,
                True,
                boundary,
                description,
                attempt_state="failed_guarded",
                terminal_outcome=trigger_result.terminal_outcome,
            )

        if verified.state == "ANCHORED" and _strong_evidence(verified):
            self.history.transition_trigger(
                attempt.attempt_id,
                "verified",
                outcome="anchor_verified",
                observed_state=verified.state,
                now=verified_at,
            )
            return self._result(
                "ANCHOR_VERIFIED",
                "Post-trigger observations prove that the reset timestamp is fixed.",
                verified,
                mode,
                True,
                boundary,
                description,
                attempt_state="verified",
                terminal_outcome=trigger_result.terminal_outcome,
            )

        self.history.transition_trigger(
            attempt.attempt_id,
            "failed_guarded",
            outcome="anchor_not_verified",
            observed_state=verified.state,
            now=verified_at,
        )
        return self._result(
            "ANCHOR_NOT_VERIFIED",
            "Codex may have received the request, but observations did not prove anchoring; no retry is allowed.",
            verified,
            mode,
            True,
            boundary,
            description,
            attempt_state="failed_guarded",
            terminal_outcome=trigger_result.terminal_outcome,
        )

    def _blocking_rollover_attempt(
        self, idempotency_key: str, boundary: int, now: float
    ) -> TriggerAttempt | None:
        attempts = [
            item
            for item in self.history.trigger_attempts()
            if item.mode == "rollover" and item.idempotency_key == idempotency_key
        ]
        blocking = self._recover_bare_reservations(attempts, now)
        if blocking:
            return blocking[-1]
        if self.history.trigger_attempt_count(boundary):
            return TriggerAttempt(
                "legacy",
                "rollover",
                idempotency_key,
                boundary,
                "failed_guarded",
                now,
                now,
            )
        return None

    def _blocking_bootstrap_attempt(self, now: float) -> TriggerAttempt | None:
        cutoff = now - self.policy.bootstrap_cooldown_seconds
        attempts = [
            item
            for item in self.history.trigger_attempts()
            if item.mode == "bootstrap" and item.created_at >= cutoff
        ]
        blocking = self._recover_bare_reservations(attempts, now)
        return blocking[-1] if blocking else None

    def _recover_bare_reservations(
        self, attempts: Sequence[TriggerAttempt], now: float
    ) -> list[TriggerAttempt]:
        blocking: list[TriggerAttempt] = []
        for attempt in attempts:
            if attempt.state == "reserved":
                if now - attempt.updated_at >= self.policy.reservation_recovery_seconds:
                    self.history.transition_trigger(
                        attempt.attempt_id,
                        "failed_recoverable",
                        outcome="reservation_recovered",
                        observed_state="UNKNOWN",
                        now=now,
                    )
                else:
                    blocking.append(attempt)
            elif attempt.state != "failed_recoverable":
                blocking.append(attempt)
        return blocking

    @staticmethod
    def _result(
        status: str,
        reason: str,
        classification: Classification,
        mode: str,
        request_possibly_sent: bool,
        boundary: int | None,
        description: TriggerDescription,
        *,
        attempt_state: str | None = None,
        terminal_outcome: str | None = None,
    ) -> ChainResult:
        return ChainResult(
            status,
            reason,
            classification,
            mode,
            request_possibly_sent,
            boundary,
            description,
            attempt_state,
            terminal_outcome,
        )


def _strong_evidence(classification: Classification) -> bool:
    evidence = classification.evidence
    return (
        classification.confidence == "high"
        and evidence.get("sample_count", 0) >= 4
        and evidence.get("elapsed_seconds", 0) >= 30
    )


def _bootstrap_usage_suitable(observations: Sequence[QuotaSnapshot]) -> bool:
    selections = [select_five_hour(snapshot) for snapshot in observations]
    return all(
        selection.status == "selected"
        and selection.window is not None
        and selection.window.used_percent == 0
        for selection in selections
    )


def _select_weekly(snapshot: QuotaSnapshot) -> QuotaWindow | None:
    candidates = [
        window
        for window in snapshot.windows
        if window.duration_minutes is not None and 9000 <= window.duration_minutes <= 11100
    ]
    if len(candidates) == 1:
        return candidates[0]
    official = [window for window in candidates if window.limit_id == "codex"]
    return official[0] if len(official) == 1 else None
