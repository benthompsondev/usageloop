from pathlib import Path
import tempfile
import unittest

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore, ProviderViewState, automation_decision
from sentinel.provider_runtime import CodexOperationRunner, chain_outcome_policy


class ChainOutcomePolicyTests(unittest.TestCase):
    CASES = {
        "EVIDENCE_TOO_WEAK": ("RECOVERABLE", "Waiting", True, True, False),
        "WEEKLY_UNAVAILABLE": ("PROTECTED", "Protected", True, True, False),
        "WEEKLY_EXHAUSTED": ("PROTECTED", "Protected", True, True, False),
        "ROLLOVER_BOUNDARY_UNKNOWN": ("RECOVERABLE", "Waiting", True, True, False),
        "ATTEMPT_ALREADY_RECORDED": ("GUARDED", "Guarded", True, False, False),
        "TRIGGER_NOT_SENT": ("RECOVERABLE", "Waiting", True, True, False),
        "VERIFICATION_UNAVAILABLE": ("GUARDED", "Guarded", True, False, False),
        "ANCHOR_NOT_VERIFIED": ("GUARDED", "Guarded", True, False, False),
        "RESET_BUFFER": ("RECOVERABLE", "Waiting", True, True, False),
        "NOT_ELIGIBLE": ("RECOVERABLE", "Waiting", True, True, False),
        "ALREADY_ANCHORED": ("READY", "Ready", False, False, False),
        "ANCHOR_VERIFIED": ("READY", "Ready", False, False, False),
    }

    def test_every_relevant_outcome_has_an_intentional_policy(self):
        for outcome, expected in self.CASES.items():
            with self.subTest(outcome=outcome):
                policy = chain_outcome_policy(outcome)
                self.assertEqual(expected[0], policy.category)
                self.assertEqual(expected[1], policy.view_status)
                self.assertEqual(expected[2], policy.read_only_recovery)
                self.assertEqual(expected[3], policy.future_trigger_permitted)
                self.assertEqual(expected[4], policy.user_attention)

    def test_every_policy_reaches_the_provider_view_state_seam(self):
        for outcome, expected in self.CASES.items():
            with self.subTest(outcome=outcome):
                state = CodexOperationRunner._state_from_result(
                    outcome,
                    "bounded test outcome",
                    "UNANCHORED",
                    [],
                    "runtime:1",
                )
                self.assertEqual(expected[1], state.status)
                self.assertEqual(expected[0], state.outcome_category)

    def test_unknown_outcome_fails_closed_for_user_attention(self):
        policy = chain_outcome_policy("UNRECOGNIZED")
        self.assertEqual("USER_ATTENTION", policy.category)
        self.assertEqual("Needs attention", policy.view_status)
        self.assertFalse(policy.read_only_recovery)
        self.assertFalse(policy.future_trigger_permitted)
        self.assertTrue(policy.user_attention)


class RecoveryBackoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def state(self, *, outcome="TRIGGER_NOT_SENT", checked_at=1_000.0):
        policy = chain_outcome_policy(outcome)
        return ProviderViewState(
            "codex",
            "Codex",
            True,
            True,
            policy.view_status,
            outcome,
            runtime_identity="runtime:1",
            reset_at=900,
            usage_checked_at=checked_at,
            weekly_used_percent=10,
            weekly_reset_at=99_000,
            quota_state="UNANCHORED",
            outcome_category=policy.category,
        )

    def controller(self):
        class Provider:
            provider_id = "codex"

            def detect(inner_self):
                return self.state()

        controller = ApplicationController(
            [Provider()], AppStateStore(Path(self.directory.name) / "state.json")
        )
        controller.settings = AppSettings(
            automation_enabled=True,
            compatible_runtime_identities={"codex": "runtime:1"},
            checked_runtime_identities={"codex": "runtime:1"},
        )
        controller.states = {"codex": self.state()}
        return controller

    def test_recoverable_result_uses_bounded_exponential_backoff(self):
        controller = self.controller()
        delays = []
        now = 2_000.0
        for _ in range(6):
            applied = controller.apply_operation_result(
                "TRIGGER_NOT_SENT", self.state(), now=now
            )
            delays.append(applied.recovery_not_before - now)
            now = applied.recovery_not_before
        self.assertEqual([60, 120, 240, 480, 900, 900], delays)

    def test_materially_new_evidence_resets_backoff(self):
        controller = self.controller()
        first = controller.apply_operation_result(
            "NOT_ELIGIBLE", self.state(outcome="NOT_ELIGIBLE"), now=2_000
        )
        controller.apply_operation_result(
            "NOT_ELIGIBLE", self.state(outcome="NOT_ELIGIBLE"), now=first.recovery_not_before
        )
        changed = self.state(outcome="NOT_ELIGIBLE", checked_at=1_100)
        changed = ProviderViewState(**{**changed.__dict__, "used_percent": 1})
        reset = controller.apply_operation_result(
            "NOT_ELIGIBLE", changed, now=3_000
        )
        self.assertEqual(1, reset.recovery_attempts)
        self.assertEqual(3_060, reset.recovery_not_before)

    def test_next_tick_waits_until_backoff_expires(self):
        controller = self.controller()
        state = controller.apply_operation_result(
            "TRIGGER_NOT_SENT", self.state(), now=2_000
        )
        before = automation_decision(
            True,
            state,
            now=2_059,
            compatible_runtime_identity="runtime:1",
        )
        after = automation_decision(
            True,
            state,
            now=2_060,
            compatible_runtime_identity="runtime:1",
        )
        self.assertEqual("WAIT", before.action)
        self.assertEqual("ROLLOVER", after.action)

    def test_guarded_result_allows_read_only_recovery_but_never_a_second_turn(self):
        policy = chain_outcome_policy("ANCHOR_NOT_VERIFIED")
        self.assertTrue(policy.read_only_recovery)
        self.assertFalse(policy.future_trigger_permitted)


if __name__ == "__main__":
    unittest.main()
