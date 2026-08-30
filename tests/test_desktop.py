import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStackedWidget

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import MainWindow, DesktopShell, present_provider_state


class FakeProvider:
    def __init__(self, state):
        self.provider_id = state.provider_id
        self.state = state
        self.probe_calls = 0
        self.action_calls = 0

    def detect(self):
        return self.state

    def probe(self):
        self.probe_calls += 1

    def run_action(self, mode):
        self.action_calls += 1


class FakeStartup:
    def __init__(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.enabled = enabled


class DesktopTests(unittest.TestCase):
    def test_packaged_entrypoint_runs_as_a_script(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                os.fspath(Path(os.sys.executable)),
                os.fspath(repo_root / "packaging" / "entrypoint.py"),
                "--help",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--background", result.stdout)

    def test_packaging_excludes_unrelated_icu_dlls_from_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        spec = (repo_root / "packaging" / "WindowSentinel.spec").read_text(
            encoding="utf-8"
        )

        self.assertIn('"icuuc.dll"', spec)
        self.assertIn('"icudt78.dll"', spec)
        self.assertIn("a.binaries = [", spec)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, state):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        provider = FakeProvider(state)
        controller = ApplicationController(
            [provider], AppStateStore(Path(temporary.name) / "state.json")
        )
        controller.start()
        window = MainWindow(
            controller,
            {state.provider_id: provider},
            FakeStartup(),
            confirm_enable=lambda: True,
            confirm_bootstrap=lambda: True,
        )
        self.addCleanup(window.close)
        return window, provider

    def test_window_shows_primary_control_and_provider_state(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        self.assertEqual("Keep my 5-hour windows ready", window.automation_toggle.text())
        self.assertEqual("WAITING", window.provider_cards["codex"].status_label.text())
        self.assertLessEqual(window.minimumSizeHint().width(), 1366)
        self.assertLessEqual(window.minimumSizeHint().height(), 768)
        self.assertEqual(3, window.findChild(QStackedWidget).count())
        self.assertEqual(
            ["Dashboard", "Settings", "About"],
            [button.text() for button in window.nav_buttons],
        )

    def test_local_countdown_refresh_does_not_call_provider(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(10_000, verified_at=100)
        window, provider = self.make_window(state)
        window.refresh_clock(now=6_340)
        self.assertEqual("1h 01m", window.provider_cards["codex"].countdown_label.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_automation_off_evaluation_performs_zero_provider_operations(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        )
        window, provider = self.make_window(state)
        window.evaluate_automation(now=100)
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_unavailable_provider_renders_needs_attention(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=False)
        window, _provider = self.make_window(state)
        self.assertEqual("NOT DETECTED", window.provider_cards["codex"].status_label.text())

    def test_claude_detection_is_consumer_friendly_automation_paused(self):
        state = ProviderViewState(
            "claude",
            "Claude Code",
            True,
            False,
            "Needs attention",
            "Technical compatibility reason.",
        )
        presented = present_provider_state(state, now=100)
        self.assertEqual("AUTOMATION PAUSED", presented.status)
        self.assertEqual("Support being verified", presented.headline)
        self.assertNotIn("Technical", presented.detail)

    def test_stale_verified_data_is_labelled_without_provider_work(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(30_000, verified_at=100)
        window, provider = self.make_window(state)
        window.refresh_clock(now=25_000)
        self.assertEqual("STALE", window.provider_cards["codex"].status_label.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_update_panel_does_not_check_until_user_clicks(self):
        class FakeUpdater:
            def __init__(self):
                self.check_calls = 0

            def check(self):
                self.check_calls += 1

        state = ProviderViewState.waiting("codex", "Codex", installed=True)
        updater = FakeUpdater()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        provider = FakeProvider(state)
        controller = ApplicationController(
            [provider], AppStateStore(Path(temporary.name) / "state.json")
        )
        controller.start()
        window = MainWindow(
            controller,
            {state.provider_id: provider},
            FakeStartup(),
            updater=updater,
            confirm_enable=lambda: True,
            confirm_bootstrap=lambda: True,
        )
        self.addCleanup(window.close)
        self.assertEqual(0, updater.check_calls)
        self.assertEqual("Check for updates", window.update_panel.action_button.text())

    def test_tray_shell_can_hide_and_restore_same_window(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)
        window.show()
        shell.hide_window()
        self.assertFalse(window.isVisible())
        shell.restore_window()
        self.assertTrue(window.isVisible())


if __name__ == "__main__":
    unittest.main()
