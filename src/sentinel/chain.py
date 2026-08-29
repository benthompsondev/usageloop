"""One bounded Codex rollover trigger followed by Phase 1 verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .classifier import Classification, classify
from .history import SafeHistory
from .quota import QuotaSnapshot, QuotaWindow
from .trigger import Trigger, TriggerDescription


@dataclass(frozen=True)
class ChainPolicy:
    reset_buffer_seconds: float = 15.0
    weekly_protection_percent: int = 99
    max_attempts_per_boundary: int = 1


@dataclass(frozen=True)
class ChainResult:
    status: str
    reason: str
    classification: Classification
    trigger_attempted: bool
    boundary_reset_at: int | None
    trigger: TriggerDescription

    @property
    def anchored(self) -> bool:
        return self.classification.state == "ANCHORED"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "anchored": self.anchored,
            "trigger_attempted": self.trigger_attempted,
            "boundary_reset_at": self.boundary_reset_at,
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
        preflight = classify(observations)
        description = self.trigger.describe()
        if preflight.state == "ANCHORED":
            return self._result(
                "ALREADY_ANCHORED",
                "The five-hour window is already anchored; no request was sent.",
                preflight,
                False,
                None,
                description,
            )
        if preflight.state != "UNANCHORED":
            return self._result(
                "NOT_ELIGIBLE",
                "A trigger requires conservative UNANCHORED evidence.",
                preflight,
                False,
                None,
                description,
            )

        current = max(observations, key=lambda item: item.observed_at)
        weekly = _select_weekly(current)
        if weekly is None:
            return self._result(
                "WEEKLY_UNAVAILABLE",
                "The weekly Codex window is absent or ambiguous, so Sentinel will not consume quota.",
                preflight,
                False,
                None,
                description,
            )
        if weekly.used_percent >= self.policy.weekly_protection_percent or weekly.blocked_reason:
            return self._result(
                "WEEKLY_EXHAUSTED",
                "Weekly protection blocked the trigger.",
                preflight,
                False,
                None,
                description,
            )

        boundary = self.history.latest_anchored_reset_before(current.observed_at)
        if boundary is None:
            return self._result(
                "ROLLOVER_BOUNDARY_UNKNOWN",
                "No recent anchored reset proves that a genuine rollover occurred.",
                preflight,
                False,
                None,
                description,
            )
        if current.observed_at < boundary + self.policy.reset_buffer_seconds:
            return self._result(
                "RESET_BUFFER",
                "The known rollover has not cleared the configured reset buffer.",
                preflight,
                False,
                boundary,
                description,
            )

        attempts = self.history.trigger_attempt_count(boundary)
        if attempts >= self.policy.max_attempts_per_boundary:
            return self._result(
                "ATTEMPT_ALREADY_RECORDED",
                "A trigger was already reserved for this rollover; Sentinel will not send another.",
                preflight,
                False,
                boundary,
                description,
            )
        if dry_run:
            return self._result(
                "DRY_RUN",
                "Eligible rollover detected; dry-run sent no request.",
                preflight,
                False,
                boundary,
                description,
            )

        self.history.record_trigger_attempt(
            boundary,
            description.model,
            description.reasoning_effort,
        )
        trigger_result = self.trigger.run()
        if not trigger_result.succeeded:
            self.history.record_trigger_result(boundary, trigger_result.category, preflight.state)
            return self._result(
                "TRIGGER_FAILED",
                "The bounded interactive Codex trigger process failed.",
                preflight,
                True,
                boundary,
                description,
            )

        verified = classify(collect_verification())
        if verified.state == "ANCHORED":
            self.history.record_trigger_result(boundary, "anchor_verified", verified.state)
            return self._result(
                "ANCHOR_VERIFIED",
                "Post-trigger observations prove that the reset timestamp is fixed.",
                verified,
                True,
                boundary,
                description,
            )
        self.history.record_trigger_result(boundary, "anchor_not_verified", verified.state)
        return self._result(
            "ANCHOR_NOT_VERIFIED",
            "The request completed, but post-trigger observations did not prove anchoring.",
            verified,
            True,
            boundary,
            description,
        )

    @staticmethod
    def _result(
        status: str,
        reason: str,
        classification: Classification,
        attempted: bool,
        boundary: int | None,
        description: TriggerDescription,
    ) -> ChainResult:
        return ChainResult(status, reason, classification, attempted, boundary, description)


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
