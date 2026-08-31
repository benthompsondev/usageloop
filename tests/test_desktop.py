import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget

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

    def run_action(self, mode, *, current_state=None):
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
        spec = (repo_root / "packaging" / "UsageLoop.spec").read_text(
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
        self.assertEqual("Keep my Codex reset clock running", window.automation_toggle.text())
        # A freshly detected provider has never been checked, so it must not
        # borrow the language of a window that is actually counting down.
        self.assertEqual(
            "AUTOMATION OFF", window.provider_cards["codex"].status_label.text()
        )
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
        self.assertEqual("NEEDS ATTENTION", window.provider_cards["codex"].status_label.text())

    def test_shipped_window_contains_no_claude_copy(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        visible_copy = " ".join(label.text() for label in window.findChildren(QLabel))
        self.assertNotIn("Claude", visible_copy)

    def test_codex_card_shows_clock_usage_weekly_safety_and_last_action(self):
        state = ProviderViewState(
            "codex",
            "Codex",
            True,
            True,
            "Ready",
            "Verified.",
            reset_at=1000,
            last_verified_at=90,
            last_action="Anchor verified",
            used_percent=12,
            usage_checked_at=90,
            weekly_used_percent=34,
            weekly_reset_at=5000,
        )
        window, provider = self.make_window(state)
        window.controller.set_automation_enabled(True)
        window.refresh_clock(now=100)
        card = window.provider_cards["codex"]
        self.assertEqual("CLOCK RUNNING", card.status_label.text())
        self.assertEqual("0h 15m", card.countdown_label.text())
        self.assertIn("12% used", card.usage_label.text())
        self.assertIn("34% used", card.weekly_label.text())
        self.assertIn("Anchor verified", card.action_label.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_stale_verified_data_is_labelled_without_provider_work(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(30_000, verified_at=100)
        window, provider = self.make_window(state)
        window.refresh_clock(now=25_000)
        self.assertEqual("NEEDS ATTENTION", window.provider_cards["codex"].status_label.text())
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

    def test_tray_quit_closes_the_window_instead_of_hiding_it(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)
        window.show()
        shell.quit()
        self.assertTrue(window.force_close)
        self.assertFalse(window.isVisible())




class FirstRunStateMappingTests(unittest.TestCase):
    """A provider that has never been checked must not sound like one that has."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def provider(self, **overrides):
        base = dict(
            provider_id="codex",
            display_name="Codex",
            installed=True,
            automation_supported=True,
            status="Waiting",
            detail="Detected.",
        )
        base.update(overrides)
        return ProviderViewState(**base)

    def test_detected_with_no_evidence_and_automation_off_offers_setup(self):
        presented = present_provider_state(
            self.provider(), now=100, automation_enabled=False
        )
        self.assertEqual("AUTOMATION OFF", presented.status)
        self.assertEqual("No reset clock verified yet", presented.headline)
        self.assertIn("Turn on UsageLoop", presented.detail)
        self.assertEqual("neutral", presented.tone)

    def test_detected_with_no_evidence_and_automation_on_says_not_checked(self):
        presented = present_provider_state(
            self.provider(), now=100, automation_enabled=True
        )
        self.assertEqual("WAITING FOR RESET", presented.status)
        self.assertEqual("No reset clock verified yet", presented.headline)
        self.assertEqual("info", presented.tone)

    def test_no_first_run_state_claims_a_reset_it_never_saw(self):
        for enabled in (False, True):
            presented = present_provider_state(
                self.provider(), now=100, automation_enabled=enabled
            )
            with self.subTest(automation=enabled):
                self.assertNotIn("Waiting for reset", presented.headline)
                self.assertEqual("Reset time not verified", presented.reset)
                self.assertEqual("Not checked yet", presented.verified)

    def test_a_known_boundary_still_reports_the_countdown(self):
        presented = present_provider_state(
            self.provider(status="Ready", reset_at=1000, last_verified_at=90),
            now=100,
            automation_enabled=True,
        )
        self.assertEqual("CLOCK RUNNING", presented.status)
        self.assertEqual("0h 15m", presented.headline)

    def test_an_ended_window_is_waiting_not_not_checked(self):
        presented = present_provider_state(
            self.provider(status="Waiting", reset_at=50, last_verified_at=40),
            now=100,
            automation_enabled=False,
        )
        self.assertEqual("AUTOMATION OFF", presented.status)
        self.assertEqual("Reset reached", presented.headline)
        self.assertIn("Turn UsageLoop on", presented.detail)

    def test_needs_attention_still_wins_over_first_run_wording(self):
        presented = present_provider_state(
            self.provider(status="Needs attention"), now=100, automation_enabled=False
        )
        self.assertEqual("NEEDS ATTENTION", presented.status)

    def test_starting_still_wins_over_first_run_wording(self):
        presented = present_provider_state(
            self.provider(status="Starting"), now=100, automation_enabled=False
        )
        self.assertEqual("STARTING WINDOW", presented.status)

    def test_missing_provider_is_unchanged(self):
        presented = present_provider_state(
            self.provider(installed=False), now=100, automation_enabled=False
        )
        self.assertEqual("NEEDS ATTENTION", presented.status)



if __name__ == "__main__":
    unittest.main()
