import tempfile
import unittest
from pathlib import Path

from sentinel.chain import ChainCoordinator, ChainPolicy
from sentinel.classifier import Classification
from sentinel.history import SafeHistory
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
    return [snapshot(start + offset, reset, used=used, weekly_used=weekly_used) for offset in (0, 5, 10, 15)]


def unanchored(start=BOUNDARY + 20, *, weekly_used=10):
    return [
        snapshot(start + offset, start + offset + 18_000, weekly_used=weekly_used)
        for offset in (0, 5, 10, 15)
    ]


class FakeTrigger:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result or TriggerRunResult(True, "turn_completed")

    def describe(self):
        return TriggerDescription(
            mechanism="interactive_codex_tui_conpty",
            model="gpt-5.4-mini",
            reasoning_effort="low",
            prompt_characters=2,
        )

    def run(self):
        self.calls += 1
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

    def coordinator(self, trigger):
        return ChainCoordinator(trigger, self.history, ChainPolicy())

    def test_already_anchored_does_not_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(anchored(), lambda: anchored())
        self.assertEqual("ALREADY_ANCHORED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_rollover_unanchored_triggers_once(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual(1, trigger.calls)

    def test_weekly_exhausted_skips_trigger(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(weekly_used=100), lambda: anchored())
        self.assertEqual("WEEKLY_EXHAUSTED", result.status)
        self.assertEqual(0, trigger.calls)

    def test_dry_run_performs_zero_triggers(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored(), dry_run=True)
        self.assertEqual("DRY_RUN", result.status)
        self.assertEqual(0, trigger.calls)
        self.assertEqual(0, self.history.trigger_attempt_count(BOUNDARY))

    def test_trigger_process_failure_is_bounded(self):
        trigger = FakeTrigger(TriggerRunResult(False, "trigger_process_failed"))
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("TRIGGER_FAILED", result.status)
        self.assertEqual(1, trigger.calls)
        again = self.coordinator(trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", again.status)
        self.assertEqual(1, trigger.calls)

    def test_successful_process_without_anchor_fails_verification(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: unanchored(BOUNDARY + 60))
        self.assertEqual("ANCHOR_NOT_VERIFIED", result.status)
        self.assertEqual("UNANCHORED", result.classification.state)
        self.assertEqual(1, trigger.calls)

    def test_successful_trigger_requires_fixed_reset_evidence(self):
        trigger = FakeTrigger()
        result = self.coordinator(trigger).run(unanchored(), lambda: anchored(BOUNDARY + 60))
        self.assertEqual("ANCHOR_VERIFIED", result.status)
        self.assertEqual("ANCHORED", result.classification.state)

    def test_repeated_polling_does_not_double_trigger(self):
        trigger = FakeTrigger()
        first = self.coordinator(trigger).run(unanchored(), lambda: unanchored(BOUNDARY + 60))
        second = self.coordinator(trigger).run(unanchored(BOUNDARY + 100), lambda: anchored())
        self.assertEqual("ANCHOR_NOT_VERIFIED", first.status)
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", second.status)
        self.assertEqual(1, trigger.calls)

    def test_restart_recovers_around_rollover_boundary(self):
        first_trigger = FakeTrigger(TriggerRunResult(False, "trigger_process_failed"))
        first = self.coordinator(first_trigger).run(unanchored(), lambda: anchored())
        self.assertEqual("TRIGGER_FAILED", first.status)

        restarted_trigger = FakeTrigger()
        restarted = ChainCoordinator(
            restarted_trigger, SafeHistory(self.history.path), ChainPolicy()
        ).run(unanchored(BOUNDARY + 40), lambda: anchored())
        self.assertEqual("ATTEMPT_ALREADY_RECORDED", restarted.status)
        self.assertEqual(0, restarted_trigger.calls)

        recovered = ChainCoordinator(
            restarted_trigger, SafeHistory(self.history.path), ChainPolicy()
        ).run(anchored(BOUNDARY + 80), lambda: anchored())
        self.assertEqual("ALREADY_ANCHORED", recovered.status)

    def test_reset_buffer_blocks_an_early_attempt(self):
        trigger = FakeTrigger()
        policy = ChainPolicy(reset_buffer_seconds=60)
        result = ChainCoordinator(trigger, self.history, policy).run(unanchored(), lambda: anchored())
        self.assertEqual("RESET_BUFFER", result.status)
        self.assertEqual(0, trigger.calls)


if __name__ == "__main__":
    unittest.main()
