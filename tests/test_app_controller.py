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
            self.assertEqual("NONE", controller.decisions(now=101)["codex"].action)


if __name__ == "__main__":
    unittest.main()
