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
            settings = AppSettings(
                True,
                True,
                True,
                {"codex": "native:123"},
                schedule_mode="daily",
                daily_start_hour=6,
                daily_start_minute=30,
            )
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

    def test_invalid_schedule_settings_fall_back_to_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app-state.json"
            path.write_text(
                json.dumps(
                    {
                        "settings": {
                            "automation_enabled": True,
                            "schedule_mode": "surprise",
                            "daily_start_hour": 99,
                            "daily_start_minute": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
            settings = AppStateStore(path).load()
            self.assertEqual("continuous", settings.schedule_mode)
            self.assertEqual(4, settings.daily_start_hour)
            self.assertEqual(0, settings.daily_start_minute)

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

    def test_daily_mode_waits_when_reset_expired_before_selected_time(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo("America/Toronto")
        reset = datetime(2026, 8, 31, 3, 32, tzinfo=timezone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(int(reset), verified_at=reset - 100)

        decision = automation_decision(
            True, state,
            now=datetime(2026, 8, 31, 3, 50, tzinfo=timezone).timestamp(),
            compatible_runtime_identity="native:same",
            schedule_mode="daily", daily_hour=4, daily_minute=0,
            timezone=timezone,
        )

        self.assertEqual("WAIT", decision.action)

    def test_daily_mode_catches_up_once_after_sleeping_past_due_time(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo("America/Toronto")
        reset = datetime(2026, 8, 31, 3, 32, tzinfo=timezone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(int(reset), verified_at=reset - 100)

        decision = automation_decision(
            True, state,
            now=datetime(2026, 8, 31, 7, 0, tzinfo=timezone).timestamp(),
            compatible_runtime_identity="native:same",
            schedule_mode="daily", daily_hour=4, daily_minute=0,
            timezone=timezone,
        )

        self.assertEqual("ROLLOVER", decision.action)

    def test_daily_mode_does_not_start_after_active_window_missed_time(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo("America/Toronto")
        reset = datetime(2026, 8, 31, 5, 0, tzinfo=timezone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(int(reset), verified_at=reset - 100)

        decision = automation_decision(
            True, state,
            now=datetime(2026, 8, 31, 7, 0, tzinfo=timezone).timestamp(),
            compatible_runtime_identity="native:same",
            schedule_mode="daily", daily_hour=4, daily_minute=0,
            timezone=timezone,
        )

        self.assertEqual("WAIT", decision.action)

    def test_switching_daily_to_continuous_makes_an_expired_boundary_due(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo("America/Toronto")
        reset = datetime(2026, 8, 31, 3, 32, tzinfo=timezone).timestamp()
        now = datetime(2026, 8, 31, 3, 40, tzinfo=timezone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(int(reset), verified_at=reset - 100)

        daily = automation_decision(
            True,
            state,
            now=now,
            compatible_runtime_identity="native:same",
            schedule_mode="daily",
            daily_hour=4,
            daily_minute=0,
            timezone=timezone,
        )
        continuous = automation_decision(
            True,
            state,
            now=now,
            compatible_runtime_identity="native:same",
            schedule_mode="continuous",
        )

        self.assertEqual("WAIT", daily.action)
        self.assertEqual("ROLLOVER", continuous.action)

    def test_automation_off_then_on_recovers_one_missed_boundary(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(1_000, verified_at=900)

        off = automation_decision(
            False,
            state,
            now=1_100,
            compatible_runtime_identity="native:same",
        )
        on = automation_decision(
            True,
            state,
            now=1_100,
            compatible_runtime_identity="native:same",
        )

        self.assertEqual("NONE", off.action)
        self.assertEqual("ROLLOVER", on.action)

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

    def test_expired_exhausted_window_is_rechecked_after_reset_buffer(self):
        state = ProviderViewState(
            provider_id="codex",
            display_name="Codex",
            installed=True,
            automation_supported=True,
            status="Waiting",
            detail="Codex reports that the five-hour window is exhausted.",
            runtime_identity="native:same",
            reset_at=1_000,
            quota_state="EXHAUSTED",
        )

        before = automation_decision(
            True,
            state,
            now=1_014,
            compatible_runtime_identity="native:same",
        )
        after = automation_decision(
            True,
            state,
            now=1_015,
            compatible_runtime_identity="native:same",
        )

        self.assertEqual("WAIT", before.action)
        self.assertEqual("ROLLOVER", after.action)

    def test_daily_mode_fails_closed_on_an_unusable_cached_reset_timestamp(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="native:same"
        ).with_reset(10**20, verified_at=100)

        decision = automation_decision(
            True,
            state,
            now=200,
            compatible_runtime_identity="native:same",
            schedule_mode="daily",
            daily_hour=4,
            daily_minute=0,
        )

        self.assertEqual("NONE", decision.action)


if __name__ == "__main__":
    unittest.main()
