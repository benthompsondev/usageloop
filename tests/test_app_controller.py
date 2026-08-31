from pathlib import Path
import tempfile
import unittest

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore, ProviderViewState
from sentinel.providers import CompatibilityResult


class FakeProvider:
    def __init__(self, state):
        self.provider_id = state.provider_id
        self.state = state
        self.detect_calls = 0
        self.operation_calls = 0

    def detect(self):
        self.detect_calls += 1
        return self.state


class ApplicationControllerTests(unittest.TestCase):
    def test_schedule_choice_survives_restart_without_provider_work(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            state = ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:1"
            )
            provider = FakeProvider(state)
            controller = ApplicationController([provider], store)
            controller.start()
            controller.set_schedule_mode("daily")
            controller.set_daily_start_time(6, 45)

            restarted = ApplicationController([provider], store)
            restarted.start()

            self.assertEqual("daily", restarted.settings.schedule_mode)
            self.assertEqual(6, restarted.settings.daily_start_hour)
            self.assertEqual(45, restarted.settings.daily_start_minute)
            self.assertEqual(0, provider.operation_calls)

    def test_start_prunes_retired_provider_cache_and_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            codex = ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="codex:1"
            )
            retired = ProviderViewState.waiting(
                "claude", "Claude Code", installed=True, runtime_identity="claude:1"
            )
            store.save(
                AppSettings(
                    True,
                    True,
                    True,
                    {"codex": "codex:1", "claude": "claude:1"},
                    {"codex": "codex:1", "claude": "claude:1"},
                ),
                {"codex": codex, "claude": retired},
            )

            controller = ApplicationController([FakeProvider(codex)], store)
            controller.start()

            self.assertEqual({"codex"}, set(controller.states))
            self.assertEqual(
                {"codex": "codex:1"},
                controller.settings.compatible_runtime_identities,
            )
            self.assertEqual(
                {"codex": "codex:1"}, controller.settings.checked_runtime_identities
            )
            self.assertTrue(controller.settings.automation_enabled)
            self.assertTrue(controller.settings.start_with_windows)
            self.assertEqual({"codex"}, set(store.load_provider_cache()))

    def test_startup_with_automation_off_only_detects_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:1"
            ))
            controller = ApplicationController(
                [provider], AppStateStore(Path(directory) / "state.json")
            )
            controller.start()
            self.assertEqual(1, provider.detect_calls)
            self.assertEqual(0, provider.operation_calls)
            self.assertEqual("NONE", controller.decisions(now=100)["codex"].action)

    def test_cached_countdown_survives_restart_without_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            cached = ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:1"
            ).with_reset(1_000, verified_at=50)
            store.save(AppSettings(True, False, True, {"codex": "runtime:1"}), {"codex": cached})
            provider = FakeProvider(ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:1"
            ))
            controller = ApplicationController([provider], store)
            controller.start()
            self.assertEqual(1_000, controller.states["codex"].reset_at)
            self.assertEqual("WAIT", controller.decisions(now=900)["codex"].action)
            self.assertEqual(0, provider.operation_calls)

    def test_successful_probe_accepts_changed_version_and_keeps_automation_on(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            store.save(AppSettings(True, False, True, {"codex": "runtime:old"}), {})
            provider = FakeProvider(ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:new"
            ))
            controller = ApplicationController([provider], store)
            controller.start()
            self.assertEqual("PROBE", controller.decisions(now=100)["codex"].action)
            controller.apply_compatibility("codex", CompatibilityResult(
                True, "Waiting", "Compatible.", "runtime:new"
            ))
            self.assertTrue(controller.settings.automation_enabled)
            self.assertEqual("runtime:new", controller.settings.compatible_runtime_identities["codex"])

    def test_failed_probe_pauses_provider_without_turning_global_control_off(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            store.save(AppSettings(True, False, True, {"codex": "runtime:old"}), {})
            provider = FakeProvider(ProviderViewState.waiting(
                "codex", "Codex", installed=True, runtime_identity="runtime:new"
            ))
            controller = ApplicationController([provider], store)
            controller.start()
            controller.apply_compatibility("codex", CompatibilityResult(
                False, "Needs attention", "Capabilities changed.", "runtime:new"
            ))
            self.assertTrue(controller.settings.automation_enabled)
            self.assertEqual("Needs attention", controller.states["codex"].status)
            self.assertFalse(controller.states["codex"].automation_supported)
            self.assertEqual("NONE", controller.decisions(now=101)["codex"].action)

            restarted = ApplicationController([provider], store)
            restarted.start()
            self.assertFalse(restarted.states["codex"].automation_supported)
            self.assertEqual("Needs attention", restarted.states["codex"].status)

    def test_newer_verified_evidence_recovers_a_temporary_needs_attention_state(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(
                ProviderViewState(
                    "codex",
                    "Codex",
                    True,
                    True,
                    "Ready",
                    "Four fixed-reset observations.",
                    runtime_identity="runtime:1",
                    reset_at=1000,
                    last_verified_at=200,
                    used_percent=15,
                    usage_checked_at=200,
                )
            )
            controller = ApplicationController(
                [provider], AppStateStore(Path(directory) / "state.json")
            )
            controller.start()
            controller.states["codex"] = ProviderViewState(
                "codex",
                "Codex",
                True,
                True,
                "Needs attention",
                "One observation was inconclusive.",
                runtime_identity="runtime:1",
                reset_at=1000,
                used_percent=0,
                usage_checked_at=100,
            )

            controller.refresh_local_states()

            recovered = controller.states["codex"]
            self.assertEqual("Ready", recovered.status)
            self.assertEqual(200, recovered.last_verified_at)
            self.assertEqual(15, recovered.used_percent)

    def test_restart_migrates_legacy_exhausted_cache_back_to_recoverable_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            legacy = ProviderViewState(
                "codex",
                "Codex",
                True,
                True,
                "Needs attention",
                "The last check reported that this window was exhausted.",
                runtime_identity="runtime:1",
                reset_at=1_000,
                usage_checked_at=900,
            )
            store.save(
                AppSettings(
                    True,
                    False,
                    True,
                    {"codex": "runtime:1"},
                    {"codex": "runtime:1"},
                ),
                {"codex": legacy},
            )
            detected = ProviderViewState(
                "codex",
                "Codex",
                True,
                True,
                "Waiting",
                "The last check reported that this window was exhausted.",
                runtime_identity="runtime:1",
                reset_at=1_000,
                usage_checked_at=900,
                quota_state="EXHAUSTED",
            )

            controller = ApplicationController([FakeProvider(detected)], store)
            controller.start()

            self.assertEqual("Waiting", controller.states["codex"].status)
            self.assertEqual("EXHAUSTED", controller.states["codex"].quota_state)
            self.assertEqual("ROLLOVER", controller.decisions(now=1_015)["codex"].action)


if __name__ == "__main__":
    unittest.main()
