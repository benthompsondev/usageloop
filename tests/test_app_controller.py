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

    def test_definite_local_failure_can_retry_only_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            failed = ProviderViewState(
                "claude",
                "Claude Code",
                True,
                True,
                "Needs attention",
                "Launch failed.",
                runtime_identity="runtime:1",
                used_percent=0,
                usage_checked_at=90,
                weekly_used_percent=10,
                weekly_reset_at=1000,
                retry_after_restart=True,
            )
            store.save(
                AppSettings(True, False, True, {"claude": "runtime:1"}),
                {"claude": failed},
            )
            provider = FakeProvider(
                ProviderViewState.waiting(
                    "claude", "Claude Code", installed=True, runtime_identity="runtime:1"
                )
            )
            controller = ApplicationController([provider], store)
            controller.start()
            restored = controller.states["claude"]
            self.assertEqual("Waiting", restored.status)
            self.assertFalse(restored.retry_after_restart)
            self.assertEqual(10, restored.weekly_used_percent)
            self.assertEqual("BOOTSTRAP", controller.decisions(now=100)["claude"].action)

    def test_newer_local_claude_status_refreshes_without_provider_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppStateStore(Path(directory) / "state.json")
            provider = FakeProvider(
                ProviderViewState(
                    "claude",
                    "Claude Code",
                    True,
                    True,
                    "Waiting",
                    "Fresh local status.",
                    runtime_identity="runtime:1",
                    reset_at=1000,
                    last_verified_at=200,
                    used_percent=0,
                    usage_checked_at=200,
                    weekly_used_percent=10,
                    weekly_reset_at=9000,
                )
            )
            controller = ApplicationController([provider], store)
            controller.start()
            controller.states["claude"] = ProviderViewState(
                "claude",
                "Claude Code",
                True,
                True,
                "Waiting",
                "Old cache.",
                runtime_identity="runtime:1",
                usage_checked_at=100,
            )
            controller.refresh_local_states()
            self.assertEqual(1000, controller.states["claude"].reset_at)
            self.assertEqual(0, provider.operation_calls)

    def test_local_refresh_preserves_compatibility_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider(
                ProviderViewState.waiting(
                    "claude", "Claude Code", installed=True, runtime_identity="runtime:1"
                )
            )
            controller = ApplicationController(
                [provider], AppStateStore(Path(directory) / "state.json")
            )
            controller.start()
            controller.states["claude"] = ProviderViewState(
                "claude",
                "Claude Code",
                True,
                False,
                "Needs attention",
                "Capability missing.",
                runtime_identity="runtime:1",
            )
            controller.refresh_local_states()
            self.assertEqual("Needs attention", controller.states["claude"].status)

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


if __name__ == "__main__":
    unittest.main()
