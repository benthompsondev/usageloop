import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QTime, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QStackedWidget

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import (
    DesktopShell,
    MainWindow,
    present_provider_state,
    tray_tooltip_text,
)
from sentinel.provider_runtime import CHAIN_RESULT_OUTCOMES, ProviderOperationResult
from sentinel.product import PRODUCT
from sentinel.providers import CompatibilityResult
from sentinel.ui_components import (
    CHAIN_OUTCOME_COPY,
    _automatic_action_copy,
    daily_schedule_example,
    weekly_schedule_preview,
)
from sentinel.updates import ReleaseAsset, ReleaseInfo, UpdateError, VerifiedInstaller


class FakeProvider:
    def __init__(self, state):
        self.provider_id = state.provider_id
        self.state = state
        self.probe_calls = 0
        self.action_calls = 0
        self.sync_calls = 0

    def detect(self):
        return self.state

    def probe(self):
        self.probe_calls += 1

    def run_action(self, mode, *, current_state=None):
        self.action_calls += 1

    def sync_usage(self, *, current_state=None):
        self.sync_calls += 1
        synced = ProviderViewState(
            "codex", "Codex", True, True, "Ready", "Synced.",
            runtime_identity="runtime:1", reset_at=18_000,
            last_verified_at=130, used_percent=12, usage_checked_at=130,
            weekly_used_percent=20, weekly_reset_at=900_000,
        )
        return ProviderOperationResult("SYNC_UPDATED", synced, False)


class FakeThreadPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)


class FakeStartup:
    def __init__(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.enabled = enabled


class FakeInstallerUpdater:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.launches = []

    def launch_installer(self, installer):
        self.launches.append(installer)
        if self.fail:
            raise UpdateError("Windows refused the installer.")


class DesktopTests(unittest.TestCase):
    def test_dashboard_toggle_persists_on_and_off_and_updates_settings(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(2_000_000_000, verified_at=100)
        window, provider = self.make_window(state)
        window.thread_pool = FakeThreadPool()

        window.dashboard_automation_toggle.setChecked(True)

        self.assertTrue(window.controller.settings.automation_enabled)
        self.assertTrue(window.automation_toggle.isChecked())
        self.assertTrue(window.dashboard_automation_toggle.isChecked())
        self.assertNotIn(
            "No automatic requests", window.schedule_card.next_label.text()
        )
        restarted = window.controller.store.load()
        self.assertTrue(restarted.automation_enabled)

        window.dashboard_automation_toggle.setChecked(False)

        self.assertFalse(window.controller.settings.automation_enabled)
        self.assertFalse(window.automation_toggle.isChecked())
        self.assertFalse(window.dashboard_automation_toggle.isChecked())
        self.assertFalse(window.controller.store.load().automation_enabled)
        self.assertEqual(0, provider.action_calls)

    def test_settings_toggle_updates_dashboard_toggle(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )

        window.automation_toggle.setChecked(True)
        self.assertTrue(window.dashboard_automation_toggle.isChecked())

        window.automation_toggle.setChecked(False)
        self.assertFalse(window.dashboard_automation_toggle.isChecked())

    def test_dashboard_toggle_preserves_daily_and_continuous_schedule_modes(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(2_000_000_000, verified_at=100)
        for mode in ("daily", "continuous"):
            with self.subTest(mode=mode):
                window, _provider = self.make_window(state)
                window.thread_pool = FakeThreadPool()
                window.controller.set_schedule_mode(mode)
                window.refresh_clock(now=1_000)

                window.dashboard_automation_toggle.setChecked(True)

                self.assertEqual(mode, window.controller.settings.schedule_mode)
                self.assertEqual(mode, window.controller.store.load().schedule_mode)
                window.close()

    def test_failed_dashboard_save_rolls_both_toggles_back_and_pauses(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        window.thread_pool = FakeThreadPool()

        with patch.object(
            window.controller.store,
            "save",
            side_effect=RuntimeError("unexpected state backend failure"),
        ), patch.object(QMessageBox, "warning") as warning:
            window.dashboard_automation_toggle.setChecked(True)

        self.assertFalse(window.controller.settings.automation_enabled)
        self.assertFalse(window.dashboard_automation_toggle.isChecked())
        self.assertFalse(window.automation_toggle.isChecked())
        self.assertEqual("state_write_failed", window.controller.persistence_error)
        self.assertEqual("WAIT", window.controller.decisions(now=100)["codex"].action)
        self.assertEqual([], window.thread_pool.workers)
        self.assertEqual(0, provider.action_calls)
        warning.assert_called_once()

    def test_overall_status_glyph_is_compact_noninteractive_and_stateful(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=True)
        window, _provider = self.make_window(state)

        self.assertIsInstance(window.overall_icon, QLabel)
        self.assertLessEqual(window.overall_icon.width(), 32)
        self.assertLessEqual(window.overall_icon.height(), 32)
        self.assertEqual(Qt.FocusPolicy.NoFocus, window.overall_icon.focusPolicy())
        self.assertEqual("info", window.overall_icon.property("tone"))
        self.assertEqual("○", window.overall_icon.text())

        window.controller.update_provider_state(
            state.with_reset(20_000, verified_at=100)
        )
        window.refresh_clock(now=1_000)
        self.assertEqual("success", window.overall_icon.property("tone"))
        self.assertEqual("✓", window.overall_icon.text())

        window.controller.persistence_error = "state_write_failed"
        window.refresh_clock(now=1_000)
        self.assertEqual("warning", window.overall_icon.property("tone"))
        self.assertEqual("!", window.overall_icon.text())

    def test_failed_setting_save_reverts_visible_toggle_and_shows_attention(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        with patch.object(
            window.controller.store,
            "save",
            side_effect=PermissionError("private path"),
        ), patch.object(QMessageBox, "warning") as warning:
            window.automation_toggle.setChecked(True)

        self.assertFalse(window.controller.settings.automation_enabled)
        self.assertFalse(window.automation_toggle.isChecked())
        self.assertEqual("UsageLoop needs attention", window.overall_title.text())
        self.assertIn("Local state: Needs attention", window.diagnostic_text.text())
        warning.assert_called_once()
        self.assertEqual(0, provider.action_calls)

    def test_failed_startup_preference_save_restores_registration_and_toggle(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )

        with patch.object(
            window.controller.store,
            "save",
            side_effect=PermissionError("private path"),
        ), patch.object(QMessageBox, "warning"):
            window.startup_toggle.setChecked(True)

        self.assertFalse(window.controller.settings.start_with_windows)
        self.assertFalse(window.startup_manager.is_enabled())
        self.assertFalse(window.startup_toggle.isChecked())

    def test_failed_preflight_state_save_starts_no_provider_worker(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        window.thread_pool = FakeThreadPool()

        with patch.object(
            window.controller.store,
            "save",
            side_effect=PermissionError("private path"),
        ):
            window._start_operation("codex", "rollover")

        self.assertEqual([], window.thread_pool.workers)
        self.assertNotIn("codex", window.active_operations)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual("UsageLoop needs attention", window.overall_title.text())

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
        self.assertEqual(
            "Keep my 5-hour windows ready", window.automation_title_label.text()
        )
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
        self.assertIn(
            "Codex starts a new 5-hour reset clock", window.dashboard_intro.text()
        )
        self.assertIn(
            "does not add quota or bypass limits",
            window.dashboard_clarifier.text(),
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
        self.assertEqual("Everything is set", window.overall_title.text())
        self.assertEqual("0h 15m", card.countdown_label.text())
        self.assertIn("12% used", card.usage_label.text())
        self.assertIn("34% used", window.weekly_detail.text())
        self.assertIn("Last automatic start", window.last_action_label.text())
        self.assertIn("Successful", window.last_action_label.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_weekly_guard_threshold_is_not_presented_as_safe(self):
        state = ProviderViewState(
            "codex",
            "Codex",
            True,
            True,
            "Ready",
            "Verified.",
            reset_at=1000,
            last_verified_at=90,
            used_percent=0,
            usage_checked_at=90,
            weekly_used_percent=99,
            weekly_reset_at=5000,
        )
        window, _provider = self.make_window(state)

        window.refresh_clock(now=100)

        self.assertEqual("PROTECTED", window.weekly_status.text())

    def test_clipboard_failure_is_visible_instead_of_silent(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        with (
            patch("sentinel.desktop.QApplication.clipboard", side_effect=RuntimeError),
            patch("sentinel.desktop.QMessageBox.warning") as warning,
        ):
            window.copy_summary_button.click()

        self.assertEqual("Copy failed", window.copy_summary_button.text())
        warning.assert_called_once()

    def test_daily_schedule_settings_persist_and_update_summary_without_provider_work(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(2_000_000_000, verified_at=1_999_990_000)
        window, provider = self.make_window(state)

        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("daily"))
        window.daily_time.setTime(QTime(6, 30))
        self.app.processEvents()

        self.assertEqual("daily", window.controller.settings.schedule_mode)
        self.assertEqual(6, window.controller.settings.daily_start_hour)
        self.assertEqual(30, window.controller.settings.daily_start_minute)
        self.assertEqual("At 6:30 AM each day", window.schedule_card.mode_label.text())
        self.assertIn("Your current window ends", window.daily_schedule_example.text())
        self.assertIn("UsageLoop will start the next one", window.daily_schedule_example.text())
        self.assertIn("6:30 AM", window.daily_schedule_example.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)

    def test_settings_use_plain_schedule_labels(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )

        self.assertEqual(
            ["Continuous", "Once each day", "Weekly routine"],
            [window.schedule_mode.itemText(index) for index in range(3)],
        )
        self.assertEqual(
            "When should the next 5-hour window start?",
            window.schedule_mode_title.text(),
        )
        self.assertEqual("Start time", window.daily_time_title.text())

    def test_weekly_editor_seeds_once_and_supports_group_and_individual_times(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(2_000_000_000, verified_at=1_999_990_000)
        window, provider = self.make_window(state)
        window.daily_time.setTime(QTime(6, 30))

        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("weekly"))
        self.app.processEvents()

        self.assertFalse(window.weekly_schedule_panel.isHidden())
        self.assertEqual(((6, 30),) * 7, window.controller.settings.weekly_start_times)

        window.weekday_quick_time.setTime(QTime(4, 0))
        window.apply_weekdays.click()
        window.weekend_quick_time.setTime(QTime(5, 0))
        window.apply_weekend.click()
        window.weekly_day_times[2].setTime(QTime(4, 30))
        self.app.processEvents()

        expected = (
            (4, 0),
            (4, 0),
            (4, 30),
            (4, 0),
            (4, 0),
            (5, 0),
            (5, 0),
        )
        self.assertEqual(expected, window.controller.settings.weekly_start_times)
        self.assertEqual(expected, window.controller.store.load().weekly_start_times)
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(0, provider.sync_calls)

    def test_failed_weekly_edit_restores_last_durable_time(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("weekly"))
        self.app.processEvents()

        with patch.object(
            window.controller.store, "save", side_effect=OSError("write failed")
        ), patch.object(QMessageBox, "warning") as warning:
            window.weekly_day_times[0].setTime(QTime(8, 15))
            self.app.processEvents()

        self.assertEqual((4, 0), window.controller.settings.weekly_start_times[0])
        self.assertEqual(QTime(4, 0), window.weekly_day_times[0].time())
        warning.assert_called_once()

    def test_weekly_preview_explains_next_start_reset_and_pause(self):
        zone = ZoneInfo("America/Toronto")
        now = datetime(2026, 8, 30, 12, 0, tzinfo=zone).timestamp()
        times = ((4, 0),) * 5 + ((5, 0),) * 2

        preview = weekly_schedule_preview(times, now=now, timezone=zone)

        self.assertEqual(
            "Tomorrow: first start around 4:00 AM · next reset around 9:00 AM\n"
            "Overnight pause begins around 11:00 PM.",
            preview,
        )

    def test_weekly_dashboard_reports_overnight_pause_truthfully(self):
        zone = ZoneInfo("America/Toronto")
        reset = int(datetime(2026, 8, 30, 22, 0, tzinfo=zone).timestamp())
        now = datetime(2026, 8, 31, 3, 0, tzinfo=zone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(reset, verified_at=reset - 100)
        window, _provider = self.make_window(state)
        window.controller.set_automation_enabled(True)
        window.controller.set_schedule_mode("weekly")

        window.refresh_clock(now=now)

        self.assertEqual("Weekly routine", window.schedule_card.mode_label.text())
        self.assertIn("Overnight pause", window.schedule_card.next_label.text())
        self.assertIn("4:00 AM", window.schedule_card.next_label.text())

    def test_weekly_dashboard_reports_active_window_crossing_start(self):
        zone = ZoneInfo("America/Toronto")
        reset = int(datetime(2026, 8, 31, 8, 0, tzinfo=zone).timestamp())
        now = datetime(2026, 8, 31, 4, 0, tzinfo=zone).timestamp()
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(reset, verified_at=reset - 100)
        window, _provider = self.make_window(state)
        window.controller.set_automation_enabled(True)
        window.controller.set_schedule_mode("weekly")

        window.refresh_clock(now=now)

        self.assertIn("Current window", window.schedule_card.next_label.text())
        self.assertIn("verified reset", window.schedule_card.next_label.text())

    def test_weekly_pause_starts_no_worker_and_due_rollover_starts_at_most_one(self):
        zone = ZoneInfo("America/Toronto")
        reset = int(datetime(2026, 8, 30, 22, 0, tzinfo=zone).timestamp())
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(reset, verified_at=reset - 100)
        window, provider = self.make_window(state)
        window.thread_pool = FakeThreadPool()
        window.controller.set_automation_enabled(True)
        window.controller.set_schedule_mode("weekly")
        window.controller.apply_compatibility(
            "codex", CompatibilityResult(True, "Ready", "Compatible.", "runtime:1")
        )

        window.evaluate_automation(
            now=datetime(2026, 8, 31, 3, 0, tzinfo=zone).timestamp()
        )

        self.assertEqual([], window.thread_pool.workers)
        self.assertEqual(0, provider.action_calls)

        window.evaluate_automation(
            now=datetime(2026, 8, 31, 4, 0, tzinfo=zone).timestamp()
        )
        window.evaluate_automation(
            now=datetime(2026, 8, 31, 4, 1, tzinfo=zone).timestamp()
        )

        self.assertEqual(1, len(window.thread_pool.workers))
        self.assertEqual("rollover", window.active_operations["codex"])
        self.assertEqual(0, provider.action_calls)

    def test_about_has_plain_product_explanation_and_star_link(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        self.assertIn(
            "A new window begins when you actually use Codex",
            window.about_description.text(),
        )
        self.assertIn("does not increase your quota", window.about_description.text())
        self.assertIn(
            "A GitHub star helps other Codex users",
            window.star_description.text(),
        )
        self.assertEqual("★ Open GitHub to star UsageLoop", window.star_button.text())

        with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            window.star_button.click()

        open_url.assert_called_once()
        self.assertEqual(PRODUCT.github_url, open_url.call_args.args[0].toString())

    def test_about_opens_the_two_centralized_feedback_forms(self):
        window, _provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )

        with patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            window.about_link_buttons["Report a problem"].click()
            window.about_link_buttons["Request a feature"].click()

        self.assertEqual(
            [PRODUCT.bug_report_url, PRODUCT.feature_request_url],
            [call.args[0].toString() for call in open_url.call_args_list],
        )

    def test_manual_sync_is_available_when_automation_is_off_and_never_triggers(self):
        state = ProviderViewState(
            "codex", "Codex", True, True, "Ready", "Stale.",
            runtime_identity="runtime:1", reset_at=9_000,
            last_verified_at=20, used_percent=80, usage_checked_at=20,
            weekly_used_percent=90, weekly_reset_at=800_000,
        )
        window, provider = self.make_window(state)
        pool = FakeThreadPool()
        window.thread_pool = pool

        window.start_usage_sync("codex")
        pool.workers[0].run()
        self.app.processEvents()

        self.assertEqual(1, provider.sync_calls)
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(12, window.controller.states["codex"].used_percent)
        self.assertEqual(20, window.controller.states["codex"].weekly_used_percent)
        self.assertIn("Updated just now", window.provider_cards["codex"].sync_status.text())

    def test_repeated_sync_presses_cannot_overlap_sampling(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        )
        window, provider = self.make_window(state)
        pool = FakeThreadPool()
        window.thread_pool = pool

        window.start_usage_sync("codex")
        window.start_usage_sync("codex")

        self.assertEqual(1, len(pool.workers))
        self.assertEqual("Syncing…", window.provider_cards["codex"].sync_status.text())
        self.assertFalse(window.provider_cards["codex"].sync_button.isEnabled())
        self.assertEqual(0, provider.sync_calls)

    def test_sync_is_unavailable_when_codex_is_missing(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=False)
        window, provider = self.make_window(state)
        pool = FakeThreadPool()
        window.thread_pool = pool

        window.start_usage_sync("codex")

        self.assertEqual([], pool.workers)
        self.assertEqual(0, provider.sync_calls)
        self.assertEqual(
            "Codex not available", window.provider_cards["codex"].sync_status.text()
        )

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

    def _prepare_install(self, window, updater):
        window.update_panel.updater = updater
        window.update_panel.confirm_install = lambda _version: True
        window.update_panel.release = ReleaseInfo(
            "0.9.1",
            (),
            "https://github.com/benthompsondev/usageloop/releases/tag/v0.9.1",
            ReleaseAsset(PRODUCT.installer_filename, "https://github.com/example"),
            ReleaseAsset(PRODUCT.checksum_filename, "https://github.com/example"),
        )
        window.update_panel.installer = VerifiedInstaller(
            Path(PRODUCT.installer_filename), "0" * 64
        )
        window.update_panel._set_state("downloaded", "Installer verified.")
        window.update_panel.action_button.setText("Install update")

    def test_successful_installer_start_schedules_exit_without_provider_traffic(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        updater = FakeInstallerUpdater()
        self._prepare_install(window, updater)

        with patch("sentinel.desktop.QTimer.singleShot") as single_shot:
            window.update_panel.action_button.click()

        self.assertEqual(1, len(updater.launches))
        self.assertTrue(window.force_close)
        single_shot.assert_called_once()
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(0, provider.sync_calls)

    def test_failed_installer_start_keeps_app_open(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        updater = FakeInstallerUpdater(fail=True)
        self._prepare_install(window, updater)

        with patch("sentinel.desktop.QTimer.singleShot") as single_shot:
            window.update_panel.action_button.click()

        self.assertFalse(window.force_close)
        single_shot.assert_not_called()
        self.assertEqual("error", window.update_panel.state)
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(0, provider.sync_calls)

    def test_shutdown_handoff_failure_is_visible_and_keeps_app_open(self):
        window, provider = self.make_window(
            ProviderViewState.waiting("codex", "Codex", installed=True)
        )
        updater = FakeInstallerUpdater()
        self._prepare_install(window, updater)

        with patch(
            "sentinel.desktop.QTimer.singleShot",
            side_effect=RuntimeError("Qt timer unavailable"),
        ):
            window.update_panel.action_button.click()

        self.assertEqual(1, len(updater.launches))
        self.assertFalse(window.force_close)
        self.assertEqual("error", window.update_panel.state)
        self.assertIn("close automatically", window.update_panel.state_label.text())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(0, provider.sync_calls)

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

    def test_tray_tooltip_refresh_uses_only_cached_state(self):
        state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(14_000, verified_at=100)
        window, provider = self.make_window(state)
        window.controller.set_automation_enabled(True)
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)

        window.refresh_clock(now=740)

        self.assertEqual("UsageLoop · 3h 41m left", shell.tray.toolTip())
        self.assertEqual(0, provider.probe_calls)
        self.assertEqual(0, provider.action_calls)
        self.assertEqual(0, provider.sync_calls)


class TrayTooltipTests(unittest.TestCase):
    def state(self, **overrides):
        values = dict(
            provider_id="codex",
            display_name="Codex",
            installed=True,
            automation_supported=True,
            status="Waiting",
            detail="Detected.",
            runtime_identity="runtime:1",
        )
        values.update(overrides)
        return ProviderViewState(**values)

    def settings(self, **overrides):
        from sentinel.app_state import AppSettings

        values = dict(automation_enabled=True)
        values.update(overrides)
        return AppSettings(**values)

    def test_automation_off(self):
        tooltip = tray_tooltip_text(
            self.settings(automation_enabled=False), self.state(), now=100
        )
        self.assertEqual("UsageLoop · Automation off", tooltip)

    def test_active_countdown(self):
        tooltip = tray_tooltip_text(
            self.settings(), self.state(status="Ready", reset_at=13_360), now=100
        )
        self.assertEqual("UsageLoop · 3h 41m left", tooltip)

    def test_daily_next_start(self):
        reset = datetime(2026, 9, 1, 5, 0).timestamp()
        now = datetime(2026, 9, 1, 5, 2).timestamp()
        tooltip = tray_tooltip_text(
            self.settings(
                schedule_mode="daily", daily_start_hour=4, daily_start_minute=0
            ),
            self.state(reset_at=int(reset)),
            now=now,
        )
        self.assertEqual("UsageLoop · next start tomorrow at 4:00 AM", tooltip)

    def test_weekly_overnight_pause_uses_next_first_start(self):
        reset = datetime(2026, 8, 30, 22, 0).timestamp()
        now = datetime(2026, 8, 31, 3, 0).timestamp()
        tooltip = tray_tooltip_text(
            self.settings(
                schedule_mode="weekly",
                weekly_start_times=((4, 0),) * 5 + ((5, 0),) * 2,
            ),
            self.state(reset_at=int(reset)),
            now=now,
        )

        self.assertEqual("UsageLoop · next start today at 4:00 AM", tooltip)

    def test_waiting_for_codex_status(self):
        tooltip = tray_tooltip_text(self.settings(), self.state(), now=100)
        self.assertEqual("UsageLoop · waiting for Codex status", tooltip)

    def test_no_cached_state_waits_for_codex_status(self):
        tooltip = tray_tooltip_text(self.settings(), None, now=100)
        self.assertEqual("UsageLoop · waiting for Codex status", tooltip)

    def test_needs_attention(self):
        tooltip = tray_tooltip_text(
            self.settings(), self.state(status="Needs attention"), now=100
        )
        self.assertEqual("UsageLoop · Needs attention", tooltip)

    def test_unknown_or_degraded_state_is_consumer_safe(self):
        internal_status = "INTERNAL_DEGRADED_42"
        tooltip = tray_tooltip_text(
            self.settings(), self.state(status=internal_status), now=100
        )
        self.assertEqual("UsageLoop · Status unavailable", tooltip)
        self.assertNotIn(internal_status, tooltip)
        self.assertLessEqual(len(tooltip), 127)




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
        self.assertEqual("STARTING NEXT WINDOW", presented.status)

    def test_consumer_copy_explains_quota_and_sync_without_internal_terms(self):
        presented = present_provider_state(
            self.provider(
                status="Ready",
                reset_at=1_700_001_000,
                last_verified_at=1_700_000_090,
                last_action="Started and verified the next window",
            ),
            now=1_700_000_100,
            automation_enabled=True,
        )
        self.assertTrue(presented.verified.startswith("Last synced "))
        self.assertIn("Last automatic start", presented.action)
        self.assertIn("Successful", presented.action)

    def test_unverified_start_is_never_presented_as_successful(self):
        presented = present_provider_state(
            self.provider(
                status="Needs attention",
                last_action="Start not verified; no retry",
            ),
            now=1_700_000_100,
            automation_enabled=True,
        )

        self.assertEqual(
            "Last automatic start: Outcome unclear · No retry",
            presented.action,
        )
        self.assertNotIn("Successful", presented.action)

    def test_every_declared_chain_outcome_has_consumer_copy(self):
        self.assertEqual(CHAIN_RESULT_OUTCOMES, frozenset(CHAIN_OUTCOME_COPY))
        for outcome in CHAIN_RESULT_OUTCOMES:
            with self.subTest(outcome=outcome):
                copy = _automatic_action_copy(
                    self.provider(last_action=outcome, last_verified_at=90),
                    now=100,
                )
                self.assertTrue(copy.startswith("Last automatic start:"))
                self.assertNotIn(outcome, copy)
                self.assertNotIn(outcome.replace("_", " ").title(), copy)

    def test_weekly_protection_outcomes_name_the_weekly_guard(self):
        for outcome in ("WEEKLY_UNAVAILABLE", "WEEKLY_EXHAUSTED"):
            with self.subTest(outcome=outcome):
                copy = _automatic_action_copy(
                    self.provider(last_action=outcome), now=100
                )
                self.assertIn("Weekly", copy)
                self.assertNotIn(outcome, copy)

    def test_daily_example_uses_past_tense_and_tomorrow_after_a_missed_time(self):
        zone = ZoneInfo("America/Toronto")
        now = datetime(2026, 9, 1, 7, 9, tzinfo=zone).timestamp()
        reset = datetime(2026, 9, 1, 5, 9, tzinfo=zone).timestamp()

        copy = daily_schedule_example(
            reset, hour=4, minute=0, now=now, timezone=zone
        )

        self.assertEqual(
            "Your previous window ended today at 5:09 AM. "
            "UsageLoop will start the next one tomorrow at 4:00 AM.",
            copy,
        )

    def test_daily_example_uses_future_tense_and_truthful_days(self):
        zone = ZoneInfo("America/Toronto")
        now = datetime(2026, 9, 1, 7, 9, tzinfo=zone).timestamp()
        reset = datetime(2026, 9, 1, 13, 30, tzinfo=zone).timestamp()

        copy = daily_schedule_example(
            reset, hour=4, minute=0, now=now, timezone=zone
        )

        self.assertEqual(
            "Your current window ends today at 1:30 PM. "
            "UsageLoop will start the next one tomorrow at 4:00 AM.",
            copy,
        )

    def test_daily_example_uses_explicit_dates_when_the_reset_is_distant(self):
        zone = ZoneInfo("America/Toronto")
        now = datetime(2026, 9, 1, 7, 9, tzinfo=zone).timestamp()
        reset = datetime(2026, 9, 10, 13, 30, tzinfo=zone).timestamp()

        copy = daily_schedule_example(
            reset, hour=4, minute=0, now=now, timezone=zone
        )

        self.assertEqual(
            "Your current window ends Thu, Sep 10 at 1:30 PM. "
            "UsageLoop will start the next one Fri, Sep 11 at 4:00 AM.",
            copy,
        )

    def test_daily_example_says_when_the_scheduled_start_is_due_now(self):
        zone = ZoneInfo("America/Toronto")
        now = datetime(2026, 9, 1, 7, 9, tzinfo=zone).timestamp()
        reset = datetime(2026, 9, 1, 1, 0, tzinfo=zone).timestamp()

        copy = daily_schedule_example(
            reset, hour=4, minute=0, now=now, timezone=zone
        )

        self.assertEqual(
            "Your previous window ended today at 1:00 AM. "
            "UsageLoop is due to start the next one now.",
            copy,
        )

    def test_missing_provider_is_unchanged(self):
        presented = present_provider_state(
            self.provider(installed=False), now=100, automation_enabled=False
        )
        self.assertEqual("NEEDS ATTENTION", presented.status)



if __name__ == "__main__":
    unittest.main()
