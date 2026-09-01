import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

from sentinel.chain import ChainCoordinator, ChainPolicy
from sentinel.classifier import Classification
from sentinel.history import HistoryIntegrityError, SafeHistory
from sentinel.quota import QuotaSnapshot, QuotaWindow
from sentinel.trigger import TriggerDescription, TriggerRunResult


BOUNDARY = 2_000_000_000


def snapshot(observed_at, reset_at, *, used=0, weekly_used=10):
    return QuotaSnapshot(
        observed_at,
        (
            QuotaWindow("codex", "primary", used, 300, reset_at, None),
            QuotaWindow("codex", "secondary", weekly_used, 10080, BOUNDARY + 500_000, None),
        ),
    )


def anchored(start=BOUNDARY + 20, *, used=1, weekly_used=10):
    reset = start + 17_000
    return [
        snapshot(start + offset, reset, used=used, weekly_used=weekly_used)
        for offset in (0, 10, 20, 30)
    ]


def unanchored(start=BOUNDARY + 60, *, used=0, weekly_used=10):
    return [
        snapshot(start + offset, start + offset + 18_000, used=used, weekly_used=weekly_used)
        for offset in (0, 10, 20, 30)
    ]


def exhausted(start=BOUNDARY + 20, *, weekly_used=10):
    return [
        QuotaSnapshot(
            start + offset,
            (
                QuotaWindow(
                    "codex",
                    "primary",
                    100,
                    300,
                    BOUNDARY,
                    "rate_limit_reached",
                ),
                QuotaWindow(
                    "codex",
                    "secondary",
                    weekly_used,
                    10080,
                    BOUNDARY + 500_000,
                    None,
                ),
            ),
        )
        for offset in (0, 10, 20, 30)
    ]


def unanchored_with_weekly_limit(limit_id):
    return [
        QuotaSnapshot(
            BOUNDARY + 20 + offset,
            (
                QuotaWindow(
                    "codex",
                    "primary",
                    0,
                    300,
                    BOUNDARY + 20 + offset + 18_000,
                    None,
                ),
                QuotaWindow(
                    limit_id,
                    "secondary",
                    10,
                    10080,
                    BOUNDARY + 500_000,
                    None,
                ),
            ),
        )
        for offset in (0, 10, 20, 30)
    ]


class FakeTrigger:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result or TriggerRunResult("turn_completed", True)

    def describe(self):
        return TriggerDescription(
            mechanism="app_server_turn",
            model="gpt-5.6-sol",
            reasoning_effort="low",
            prompt_characters=2,
        )

    def run(self, *, on_request_starting=None):
        self.calls += 1
        if on_request_starting is not None:
            on_request_starting()
        return self.result


class ChainCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.history = SafeHistory(Path(self.directory.name) / "sentinel.jsonl")
        prior = snapshot(BOUNDARY - 30, BOUNDARY, used=12)
        self.history.record_observation(
            prior,
            Classification("ANCHORED", "high", "fixed", {"sample_count": 4}),
            "codex-cli test",
        )

    def tearDown(self):
        self.directory.cleanup()

    def coordinator(self, trigger, policy=None):
        return ChainCoordinator(trigger, self.history, policy or ChainPolicy())

    def test_already_anchored_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(anchored(), lambda: anchored())
        self.assertEqual("ALREADY_ANCHORED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_ordinary_chain_rollover_still_triggers_once(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual(1, trigger.calls)
        self.assertTrue(result.request_possibly_sent)

    def test_transient_exhausted_preflight_never_triggers_then_unanchored_triggers_once(self):
        trigger = FakeTrigger()

        blocked = self.coordinator(trigger).run(exhausted(), lambda: anchored())
        recovered = self.coordinator(trigger).run(
            unanchored(BOUNDARY + 80),
            lambda: anchored(BOUNDARY + 80),
        )

        self.assertEqual("NOT_ELIGIBLE", blocked.status)
        self.assertEqual("ANCHOR_VERIFIED", recovered.status)
        self.assertEqual(1, trigger.calls)

    def test_missed_boundary_after_long_sleep_still_uses_verified_history(self):
        trigger = FakeTrigger()
        much_later = BOUNDARY + (8 * 60 * 60)

        result = self.coordinator(trigger).run(
            unanchored(much_later), lambda: anchored(much_later)
        )

        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual(BOUNDARY, result.boundary_reset_at)
        self.assertEqual(1, trigger.calls)

    def test_crash_before_request_start_keeps_reservation_recoverable(self):
        class CrashBeforeRequest(FakeTrigger):
            def run(self, *, on_request_starting=None):
                self.calls += 1
                raise SystemExit("simulated process death before turn/start")

        crashing = CrashBeforeRequest()
        with self.assertRaises(SystemExit):
            self.coordinator(crashing).run(unanchored(), lambda: anchored())

        self.assertEqual("reserved", self.history.trigger_attempts()[-1].state)

        working = FakeTrigger()
        recovered = self.coordinator(working).run(
            unanchored(BOUNDARY + 180), lambda: anchored(BOUNDARY + 180)
        )
        self.assertEqual("ANCHOR_VERIFIED", recovered.status)
        self.assertEqual(1, working.calls)

    def test_reservation_is_written_inside_exclusive_guard(self):
        class GuardTrackingHistory(SafeHistory):
            guard_active = False

            @contextmanager
            def trigger_reservation_guard(self):
                self.guard_active = True
                try:
                    yield
                finally:
                    self.guard_active = False

            def reserve_trigger(self, **kwargs):
                if not self.guard_active:
                    raise AssertionError("reservation was not guarded")
                return super().reserve_trigger(**kwargs)

        trigger = FakeTrigger()
        guarded_history = GuardTrackingHistory(self.history.path)
        result = ChainCoordinator(trigger, guarded_history, ChainPolicy()).run(
            unanchored(), lambda: anchored()
        )
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual(1, trigger.calls)

    def test_concurrent_process_cannot_recover_reservation_before_launch_state_is_written(self):
        launch_transition_started = threading.Event()
        release_launch_transition = threading.Event()
        second_finished = threading.Event()

        class PausingHistory(SafeHistory):
            def transition_trigger(self, attempt_id, state, **kwargs):
                if state == "launch_attempted":
                    launch_transition_started.set()
                    release_launch_transition.wait(2)
                return super().transition_trigger(attempt_id, state, **kwargs)

        trigger = FakeTrigger()
        results = []

        def first_run():
            coordinator = ChainCoordinator(
                trigger, PausingHistory(self.history.path), ChainPolicy()
            )
            results.append(coordinator.run(unanchored(), lambda: anchored()))

        def second_run():
            try:
                coordinator = ChainCoordinator(
                    trigger, SafeHistory(self.history.path), ChainPolicy()
                )
                results.append(
                    coordinator.run(unanchored(BOUNDARY + 180), lambda: anchored())
                )
            finally:
                second_finished.set()

        first = threading.Thread(target=first_run)
        second = threading.Thread(target=second_run)
        first.start()
        self.assertTrue(launch_transition_started.wait(2))
        second.start()
        try:
            self.assertFalse(second_finished.wait(0.2))
        finally:
            release_launch_transition.set()
            first.join(2)
            second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(1, trigger.calls)
        self.assertEqual(
            ["ANCHOR_VERIFIED", "ATTEMPT_ALREADY_RECORDED"],
            sorted(result.status for result in results),
        )

    def test_weak_unanchored_evidence_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored()[:3], lambda: anchored())
        self.assertEqual("EVIDENCE_TOO_WEAK", result.status)
        self.assertEqual(0, trigger.calls)

    def test_weekly_exhausted_skips_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(weekly_used=100), lambda: anchored())
        self.assertEqual("WEEKLY_EXHAUSTED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_unreadable_history_cannot_create_a_duplicate_trigger_opportunity(self):
        trigger = FakeTrigger()
        with self.history.path.open("ab") as stream:
            stream.write(b"\xff\n")

        with self.assertRaises(HistoryIntegrityError):
            self.coordinator(trigger).run(unanchored(), lambda: anchored())

        self.assertEqual(0, trigger.calls)

    def test_unrelated_weekly_bucket_does_not_satisfy_codex_protection(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(
            unanchored_with_weekly_limit("base_model_inference"),
            lambda: anchored(),
        )
        self.assertEqual("WEEKLY_UNAVAILABLE", result.status)
        self.assertEqual(0, trigger.calls)

    def test_dry_run_performs_zero_triggers_and_reservations(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored(), dry_run=True)
        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(0, trigger.calls)
        self.assertEqual([], self.history.trigger_attempts())

    def test_failed_launch_before_request_does_not_burn_rollover(self):
        failed = FakeTrigger(TriggerRunResult("turn_start_rejected", False))
        first = self.coordinator(failed).run(unanchored(), lambda: anchored())
        self.assertEqual("TRIGGER_NOT_SENT", first.status)
        self.assertFalse(first.request_possibly_sent)

        working = FakeTrigger()
        second = self.coordinator(working).run(unanchored(BOUNDARY + 60), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", second.status)
        self.assertEqual(1, working.calls)

    def test_ambiguous_terminal_outcome_still_verifies_observed_anchor(self):
        trigger = FakeTrigger(TriggerRunResult("turn_stream_unavailable", True))
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("turn_stream_unavailable", result.terminal_outcome)

    def test_intermediate_state_write_failure_still_runs_authoritative_verification(self):
        class FailRequestStateOnce(SafeHistory):
            failed = False

            def transition_trigger(self, attempt_id, state, **kwargs):
                if state == "request_possibly_sent" and not self.failed:
                    self.failed = True
                    raise OSError("simulated state write failure")
                return super().transition_trigger(attempt_id, state, **kwargs)

        verification_called = False

        def verify():
            nonlocal verification_called
            verification_called = True
            return anchored()

        trigger = FakeTrigger()
        history = FailRequestStateOnce(self.history.path)
        result = ChainCoordinator(trigger, history, ChainPolicy()).run(
            unanchored(), verify
        )
        self.assertTrue(verification_called)
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("verified", history.trigger_attempts()[-1].state)

    def test_transient_final_state_write_failure_is_retried_locally(self):
        class FailVerifiedStateOnce(SafeHistory):
            failed = False

            def transition_trigger(self, attempt_id, state, **kwargs):
                if state == "verified" and not self.failed:
                    self.failed = True
                    raise OSError("simulated final state write failure")
                return super().transition_trigger(attempt_id, state, **kwargs)

        trigger = FakeTrigger()
        history = FailVerifiedStateOnce(self.history.path)
        try:
            result = ChainCoordinator(trigger, history, ChainPolicy()).run(
                unanchored(), lambda: anchored()
            )
        except OSError:
            self.fail("a single transient final-state write failure was not retried")
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("verified", history.trigger_attempts()[-1].state)

    def test_possible_request_without_anchor_is_not_retried(self):
        trigger = FakeTrigger(TriggerRunResult("turn_timeout", True))
        first = self.coordinator(trigger).run(unanchored(), lambda: unanchored(BOUNDARY + 60))
        second = self.coordinator(trigger).run(unanchored(BOUNDARY + 100), lambda: anchored())
        self.assertEqual("ANCHOR_NOT_VERIFIED", first.status)
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", second.status)
        self.assertEqual(1, trigger.calls)

    def test_recent_bootstrap_attempt_blocks_rollover_trigger_for_same_window(self):
        bootstrap_trigger = FakeTrigger(TriggerRunResult("turn_timeout", True))
        first = ChainCoordinator(
            bootstrap_trigger, self.history, ChainPolicy()
        ).run_bootstrap(
            unanchored(), lambda: unanchored(BOUNDARY + 60), confirmed=True
        )
        self.assertEqual("ANCHOR_NOT_VERIFIED", first.status)

        rollover_trigger = FakeTrigger()
        second = self.coordinator(rollover_trigger).run(
            unanchored(BOUNDARY + 100), lambda: anchored()
        )
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", second.status)
        self.assertEqual(0, rollover_trigger.calls)

    def test_ambiguous_request_with_verification_failure_is_guarded(self):
        trigger = FakeTrigger(TriggerRunResult("runtime_error", True))

        def unavailable():
            raise RuntimeError("fixture transport unavailable")

        first = self.coordinator(trigger).run(unanchored(), unavailable)
        second = self.coordinator(trigger).run(unanchored(BOUNDARY + 100), lambda: anchored())
        self.assertEqual("VERIFICATION_UNAVAILABLE", first.status)
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", second.status)
        self.assertEqual(1, trigger.calls)

    def test_turn_error_lifecycle_still_defers_to_quota_verification(self):
        trigger = FakeTrigger(TriggerRunResult("turn_error", True))
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("turn_error", result.terminal_outcome)

    def test_turn_timeout_still_defers_to_quota_verification(self):
        trigger = FakeTrigger(TriggerRunResult("turn_timeout", True))
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("turn_timeout", result.terminal_outcome)

    def test_turn_completed_without_anchor_is_not_a_success(self):
        trigger = FakeTrigger(TriggerRunResult("turn_completed", True))
        result = self.coordinator(trigger).run(unanchored(), lambda: unanchored(BOUNDARY + 60))
        self.assertEqual("ANCHOR_NOT_VERIFIED", result.status)
        self.assertFalse(result.anchored)

    def test_unresolved_model_never_starts_a_turn_or_burns_the_boundary(self):
        trigger = FakeTrigger(TriggerRunResult("model_unavailable", False))
        first = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("TRIGGER_NOT_SENT", first.status)
        working = FakeTrigger()
        second = self.coordinator(working).run(unanchored(BOUNDARY + 60), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", second.status)

    def test_restart_during_fresh_reservation_blocks_then_recovers_after_lease(self):
        description = FakeTrigger().describe()
        reservation = self.history.reserve_trigger(
            mode="rollover",
            idempotency_key=f"rollover:{BOUNDARY}",
            boundary_reset_at=BOUNDARY,
            model=description.model,
            reasoning_effort=description.reasoning_effort,
            now=BOUNDARY + 30,
        )
        self.assertEqual("reserved", reservation.state)

        trigger = FakeTrigger()
        result = ChainCoordinator(
            trigger, SafeHistory(self.history.path), ChainPolicy()
        ).run(unanchored(BOUNDARY + 60), lambda: anchored())
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", result.status)
        self.assertEqual(0, trigger.calls)

        recovered = ChainCoordinator(
            trigger, SafeHistory(self.history.path), ChainPolicy()
        ).run(unanchored(BOUNDARY + 180), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", recovered.status)
        self.assertEqual(1, trigger.calls)

    def test_restart_after_launch_attempt_blocks_a_second_request(self):
        reservation = self.history.reserve_trigger(
            mode="rollover",
            idempotency_key=f"rollover:{BOUNDARY}",
            boundary_reset_at=BOUNDARY,
            model="gpt-5.6-sol",
            reasoning_effort="low",
            now=BOUNDARY + 30,
        )
        self.history.transition_trigger(reservation.attempt_id, "launch_attempted", now=BOUNDARY + 31)

        trigger = FakeTrigger()
        result = ChainCoordinator(
            trigger, SafeHistory(self.history.path), ChainPolicy()
        ).run(unanchored(BOUNDARY + 60), lambda: anchored())
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_reset_buffer_blocks_an_early_attempt(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(
            unanchored(BOUNDARY + 29), lambda: anchored()
        )
        self.assertEqual("RESET_BUFFER", result.status)
        self.assertEqual(0, trigger.calls)

    def test_default_buffer_allows_one_attempt_at_sixty_seconds(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(
            unanchored(BOUNDARY + 60), lambda: anchored()
        )
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual(1, trigger.calls)


class BootstrapCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.history = SafeHistory(Path(self.directory.name) / "sentinel.jsonl")

    def tearDown(self):
        self.directory.cleanup()

    def coordinator(self, trigger):
        return ChainCoordinator(trigger, self.history, ChainPolicy())

    def test_bootstrap_eligible_triggers_once_and_verifies(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            unanchored(), lambda: anchored(), confirmed=True
        )
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("bootstrap", result.mode)
        self.assertEqual(1, trigger.calls)

    def test_bootstrap_requires_explicit_confirmation(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            unanchored(), lambda: anchored(), confirmed=False
        )
        self.assertEqual("CONSENT_REQUIRED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_bootstrap_already_anchored_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            anchored(used=0), lambda: anchored(), confirmed=True
        )
        self.assertEqual("ALREADY_ANCHORED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_bootstrap_weak_unknown_evidence_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            unanchored()[:3], lambda: anchored(), confirmed=True
        )
        self.assertEqual("EVIDENCE_TOO_WEAK", result.status)
        self.assertEqual(0, trigger.calls)

    def test_bootstrap_nonzero_five_hour_usage_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            unanchored(used=1), lambda: anchored(), confirmed=True
        )
        self.assertEqual("BOOTSTRAP_USAGE_UNSUITABLE", result.status)
        self.assertEqual(0, trigger.calls)

    def test_bootstrap_weekly_exhausted_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run_bootstrap(
            unanchored(weekly_used=100), lambda: anchored(), confirmed=True
        )
        self.assertEqual("WEEKLY_EXHAUSTED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_bootstrap_cooldown_prevents_duplicate_attempt(self):
        trigger = FakeTrigger()
        first = self.coordinator(trigger).run_bootstrap(
            unanchored(), lambda: unanchored(BOUNDARY + 60), confirmed=True
        )
        second = self.coordinator(trigger).run_bootstrap(
            unanchored(BOUNDARY + 100), lambda: anchored(), confirmed=True
        )
        self.assertEqual("ANCHOR_NOT_VERIFIED", first.status)
        self.assertEqual("BOOTSTRAP_COOLDOWN", second.status)
        self.assertEqual(1, trigger.calls)

    def test_recent_rollover_attempt_blocks_bootstrap_trigger(self):
        prior = snapshot(BOUNDARY - 30, BOUNDARY, used=12)
        self.history.record_observation(
            prior,
            Classification("ANCHORED", "high", "fixed", {"sample_count": 4}),
            "codex-cli test",
        )
        rollover_trigger = FakeTrigger(TriggerRunResult("turn_timeout", True))
        first = self.coordinator(rollover_trigger).run(
            unanchored(), lambda: unanchored(BOUNDARY + 60)
        )
        self.assertEqual("ANCHOR_NOT_VERIFIED", first.status)

        bootstrap_trigger = FakeTrigger()
        second = self.coordinator(bootstrap_trigger).run_bootstrap(
            unanchored(BOUNDARY + 100), lambda: anchored(), confirmed=True
        )
        self.assertEqual("BOOTSTRAP_COOLDOWN", second.status)
        self.assertEqual(0, bootstrap_trigger.calls)

    def test_bootstrap_failed_launch_is_recoverable(self):
        failed = FakeTrigger(TriggerRunResult("turn_start_rejected", False))
        first = self.coordinator(failed).run_bootstrap(
            unanchored(), lambda: anchored(), confirmed=True
        )
        self.assertEqual("TRIGGER_NOT_SENT", first.status)

        working = FakeTrigger()
        second = self.coordinator(working).run_bootstrap(
            unanchored(BOUNDARY + 60), lambda: anchored(), confirmed=True
        )
        self.assertEqual("ANCHOR_VERIFIED", second.status)
        self.assertEqual(1, working.calls)


if __name__ == "__main__":
    unittest.main()
