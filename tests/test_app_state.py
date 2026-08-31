import json
from pathlib import Path
import tempfile
import unittest

from sentinel.app_state import (
    AppSettings,
    AppStateStore,
    ProviderViewState,
    automation_decision,
    format_countdown,
)


class AppStateTests(unittest.TestCase):
    def test_missing_or_corrupt_settings_fail_safe_with_automation_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app-state.json"
            store = AppStateStore(path)
            self.assertFalse(store.load().automation_enabled)
            path.write_text("{broken", encoding="utf-8")
            self.assertFalse(store.load().automation_enabled)

    def test_safe_settings_and_provider_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app-state.json"
            store = AppStateStore(path)
            settings = AppSettings(True, True, True, {"codex": "native:123"})
            state = ProviderViewState(
                provider_id="codex",
                display_name="Codex",
                installed=True,
                automation_supported=True,
                status="Ready",
                detail="Window is ready.",
                runtime_identity="native:123",
                reset_at=2_000_000_000,
                last_verified_at=1_999_990_000,
                last_action="Verified window",
                used_percent=4,
                usage_checked_at=1_999_990_000,
            )
            store.save(settings, {"codex": state})
            loaded = store.load()
            self.assertEqual(settings, loaded)
            self.assertEqual(state, store.load_provider_cache()["codex"])
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("prompt", json.dumps(raw).lower())
            self.assertNotIn("token", json.dumps(raw).lower())

    def test_countdown_is_derived_locally_from_cached_reset(self):
        self.assertEqual("1h 01m", format_countdown(10_000, 6_340))
        self.assertEqual("Reset reached", format_countdown(10_000, 10_001))
        self.assertEqual("Not verified yet", format_countdown(None, 10_001))

    def test_automation_off_never_requests_provider_work(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=True)
        self.assertEqual("NONE", automation_decision(False, state, now=100).action)

    def test_changed_runtime_runs_probe_only_when_automation_is_enabled(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:new"
        )
        self.assertEqual(
            "PROBE",
            automation_decision(
                True,
                state,
                now=100,
                compatible_runtime_identity="native:old",
            ).action,
        )

    def test_verified_future_reset_waits_without_provider_traffic(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(1_000, verified_at=10)
        decision = automation_decision(
            True,
            state,
            now=900,
            compatible_runtime_identity="native:same",
        )
        self.assertEqual("WAIT", decision.action)

    def test_missed_boundary_after_sleep_is_evaluated_as_rollover(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(1_000, verified_at=900)

        decision = automation_decision(
            True,
            state,
            now=1_600,
            compatible_runtime_identity="native:same",
        )

        self.assertEqual("ROLLOVER", decision.action)

    def test_needs_attention_state_never_retries_automatically(self):
        state = ProviderViewState(
            provider_id="codex",
            display_name="Codex",
            installed=True,
            automation_supported=True,
            status="Needs attention",
            detail="The request outcome was ambiguous.",
            runtime_identity="native:same",
            reset_at=1_000,
        )
        decision = automation_decision(
            True,
            state,
            now=1_100,
            compatible_runtime_identity="native:same",
        )

        self.assertEqual("NONE", decision.action)


if __name__ == "__main__":
    unittest.main()
