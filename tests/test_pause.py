"""Temporary pause and real-history presentation, with no provider traffic."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QAccessible

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore, ProviderViewState
from sentinel.desktop import DesktopShell, MainWindow
from sentinel.history import SafeHistory
from sentinel.provider_runtime import CodexOperationRunner
from sentinel.schedule import tomorrow_first_start
from sentinel.ui_components import automatic_start_history_copy, pause_until_text
from test_desktop import FakeProvider, FakeStartup, FakeThreadPool
from test_provider_runtime import FakeClient, FakeSession, payload


ZONE = ZoneInfo("America/Toronto")
WEEK = ((4, 0),) * 5 + ((5, 0),) * 2


class PauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 9, 4, 12).timestamp()
        self.state = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ).with_reset(int(self.now - 100), verified_at=self.now - 200)
        self.provider = FakeProvider(self.state)
        self.provider.history = SafeHistory(Path(self.tmp.name) / "sentinel.jsonl")
        self.store = AppStateStore(Path(self.tmp.name) / "app-state.json")
        self.settings = AppSettings(
            automation_enabled=True, schedule_mode="weekly", weekly_start_times=WEEK,
            compatible_runtime_identities={"codex": "runtime:1"},
        )
        self.store.save(self.settings, {})
        self.controller = ApplicationController([self.provider], self.store)
        self.controller.start()

    def window(self):
        window = MainWindow(self.controller, {"codex": self.provider}, FakeStartup())
        window.clock_timer.stop()
        window.automation_timer.stop()
        window.thread_pool = FakeThreadPool()
        self.addCleanup(window.close)
        return window

    def test_pause_blocks_due_rollover_and_survives_reload_without_changing_routine(self):
        self.assertEqual("ROLLOVER", self.controller.decisions(now=self.now)["codex"].action)
        self.assertTrue(self.controller.pause_until_tomorrow(now=self.now))
        paused = self.controller.settings
        self.assertTrue(paused.automation_enabled)
        self.assertEqual(self.settings, replace(paused, automation_paused_until=None))
        fresh = ApplicationController([self.provider], self.store)
        fresh.start()
        self.assertEqual(paused, fresh.settings)
        self.assertEqual("WAIT", fresh.decisions(now=self.now)["codex"].action)
        window = self.window()
        window.evaluate_automation(now=self.now)
        self.assertEqual([], window.thread_pool.workers)

    def test_expiry_and_resume_return_to_existing_decision_without_side_effects(self):
        self.controller.pause_until_tomorrow(now=self.now)
        expiry = self.controller.settings.automation_paused_until
        original_settings = self.store.path.read_bytes()
        baseline = ApplicationController([self.provider], AppStateStore(Path(self.tmp.name) / "other.json"))
        baseline.settings = self.settings
        baseline.states = self.controller.states.copy()
        for at in (expiry, expiry + 3600, expiry + 86400):
            self.assertEqual(baseline.decisions(now=at), self.controller.decisions(now=at))
        self.assertEqual(original_settings, self.store.path.read_bytes())
        self.assertEqual(0, self.provider.action_calls)
        self.assertTrue(self.controller.resume_automation())
        self.assertIsNone(self.store.load().automation_paused_until)
        self.assertEqual(baseline.decisions(now=self.now), self.controller.decisions(now=self.now))

    def test_expiry_keeps_reset_buffer_and_existing_provider_guards(self):
        self.controller.pause_until_tomorrow(now=self.now)
        expiry = self.controller.settings.automation_paused_until
        self.controller.states["codex"] = replace(self.state, reset_at=int(expiry))
        self.assertEqual("WAIT", self.controller.decisions(now=expiry)["codex"].action)
        self.controller.states["codex"] = replace(self.state, automation_blocked_until=expiry + 500)
        self.assertEqual("WAIT", self.controller.decisions(now=expiry)["codex"].action)
        self.controller.states["codex"] = replace(self.state, status="Needs attention")
        self.assertEqual("NONE", self.controller.decisions(now=expiry)["codex"].action)

    def test_off_wins_and_resume_cannot_reenable_automation(self):
        self.controller.pause_until_tomorrow(now=self.now)
        expiry = self.controller.settings.automation_paused_until
        self.controller.set_automation_enabled(False)
        self.assertIsNone(self.store.load().automation_paused_until)
        self.controller.resume_automation()
        self.assertFalse(self.store.load().automation_enabled)
        self.assertEqual("NONE", self.controller.decisions(now=expiry + 1)["codex"].action)

    def test_schedule_edits_do_not_move_captured_pause(self):
        self.controller.pause_until_tomorrow(now=self.now)
        target = self.controller.settings.automation_paused_until
        self.controller.set_weekly_start_times(((9, 30),) * 7)
        self.controller.set_schedule_mode("daily")
        self.controller.set_daily_start_time(11, 45)
        self.controller.set_schedule_mode("continuous")
        self.assertEqual(target, self.store.load().automation_paused_until)
        self.assertFalse(self.controller.pause_until_tomorrow(now=self.now))
        self.assertEqual("WAIT", self.controller.decisions(now=self.now)["codex"].action)

    def test_weekday_weekend_midnight_year_and_dst_targets(self):
        cases = [
            ((2026, 9, 4, 1), (2026, 9, 5, 5, 0), WEEK),
            ((2026, 9, 6, 23), (2026, 9, 7, 4, 0), WEEK),
            ((2026, 12, 31, 23), (2027, 1, 1, 4, 0), WEEK),
            ((2026, 3, 7, 12), (2026, 3, 8, 3, 30), ((2, 30),) * 7),
            ((2026, 10, 31, 12), (2026, 11, 1, 1, 30), ((1, 30),) * 7),
        ]
        for before, expected, times in cases:
            with self.subTest(before=before):
                target = tomorrow_first_start("weekly", now=datetime(*before, tzinfo=ZONE).timestamp(), weekly_times=times, timezone=ZONE)
                self.assertEqual(datetime(*expected, tzinfo=ZONE, fold=0).timestamp(), target)

    def test_daily_supported_continuous_and_off_unavailable(self):
        self.controller.set_schedule_mode("continuous")
        self.assertFalse(self.controller.pause_until_tomorrow(now=self.now))
        self.controller.set_schedule_mode("daily")
        self.assertTrue(self.controller.pause_until_tomorrow(now=self.now))
        self.assertEqual(datetime(2026, 9, 5, 4).timestamp(), self.controller.settings.automation_paused_until)
        self.controller.set_automation_enabled(False)
        self.assertFalse(self.controller.pause_until_tomorrow(now=self.now))

    def test_corrupt_pause_fails_closed_and_old_state_still_loads(self):
        for invalid in (True, "tomorrow", [], {}, float("inf"), float("nan"), -1, 1e100):
            with self.subTest(invalid=invalid):
                self.store.path.write_text(json.dumps({"settings": {"automation_enabled": True, "automation_paused_until": invalid}}))
                self.assertFalse(self.store.load().automation_enabled)
        self.store.path.write_text('{"settings":{"automation_enabled":true}}')
        self.assertTrue(self.store.load().automation_enabled)
        self.assertIsNone(self.store.load().automation_paused_until)

    def test_failed_pause_and_failed_resume_roll_back_and_block_decisions(self):
        with patch.object(self.store, "save", side_effect=OSError("read only")):
            self.assertFalse(self.controller.pause_until_tomorrow(now=self.now))
        self.assertIsNone(self.controller.settings.automation_paused_until)
        self.assertEqual("WAIT", self.controller.decisions(now=self.now)["codex"].action)
        self.controller.pause_until_tomorrow(now=self.now)
        target = self.controller.settings.automation_paused_until
        with patch.object(self.store, "save", side_effect=OSError("read only")):
            self.assertFalse(self.controller.resume_automation())
        self.assertEqual(target, self.controller.settings.automation_paused_until)

    def test_dashboard_tray_and_reload_share_stored_timestamp(self):
        window = self.window()
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)
        window.toggle_temporary_pause(now=self.now)
        target = self.store.load().automation_paused_until
        display = pause_until_text(target)
        shell.refresh_menu(now=self.now)
        self.assertIn(display, window.schedule_card.next_label.text())
        self.assertIn(display, window.overall_detail.text())
        self.assertIn(display, shell.status_action.text())
        self.assertIn(display, shell.tray.toolTip())
        self.assertFalse(shell.status_action.isEnabled())
        self.assertEqual("Resume automation", shell.pause_action.text())
        self.assertTrue(window.dashboard_automation_toggle.isChecked())
        window.hide()
        shell.restore_window()
        self.assertEqual(target, self.store.load().automation_paused_until)
        with patch("sentinel.desktop.time.time", return_value=self.now):
            shell.pause_action.trigger()
        self.assertIsNone(self.store.load().automation_paused_until)
        self.assertEqual([], window.thread_pool.workers)
        shell.refresh_menu(now=self.now)
        self.assertIn(display, shell.pause_action.text())
        self.controller.set_schedule_mode("continuous")
        window.refresh_clock(now=self.now)
        shell.refresh_menu(now=self.now)
        self.assertFalse(shell.pause_action.isVisible())

    def test_busy_operation_cannot_be_paused_midflight(self):
        window = self.window()
        window.active_operations["codex"] = "rollover"
        window.toggle_temporary_pause(now=self.now)
        window.refresh_clock(now=self.now)
        self.assertIsNone(self.store.load().automation_paused_until)
        self.assertFalse(window.schedule_card.pause_button.isEnabled())

    def test_pause_controls_fit_minimum_window_and_have_accessible_names(self):
        window = self.window()
        window.resize(720, 560)
        window.show()
        for paused in (False, True):
            if paused:
                self.controller.pause_until_tomorrow(now=self.now)
            window.refresh_clock(now=self.now)
            self.app.processEvents()
            button = window.schedule_card.pause_button
            self.assertLessEqual(button.geometry().right(), window.schedule_card.width() - 20)
            self.assertEqual("Resume automation" if paused else "Pause until tomorrow", button.text())
            self.assertEqual(button.text(), QAccessible.queryAccessibleInterface(button).text(QAccessible.Text.Name))
            self.assertTrue(button.isEnabled())
            for label in (window.schedule_card.detail_label, window.schedule_card.next_label):
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))

    def test_pause_blocks_probe_and_bootstrap_including_confirmation_race(self):
        window = self.window()
        self.controller.pause_until_tomorrow(now=self.now)
        for state in (
            replace(self.state, reset_at=None),
            replace(self.state, runtime_identity="new-runtime"),
        ):
            self.controller.states["codex"] = state
            self.assertEqual("WAIT", self.controller.decisions(now=self.now)["codex"].action)
        self.controller.resume_automation()

        def confirm_and_pause():
            self.controller.pause_until_tomorrow(now=self.now)
            return True

        window.confirm_bootstrap = confirm_and_pause
        with patch("sentinel.desktop.time.time", return_value=self.now):
            window.start_bootstrap("codex")
        self.assertEqual([], window.thread_pool.workers)

    def record_start(self, at, mode="rollover"):
        history = self.provider.history
        attempt = history.reserve_trigger(mode=mode, idempotency_key=f"{mode}:{int(at)}", boundary_reset_at=int(at - 60), model="test-model", reasoning_effort="low", now=at)
        history.transition_trigger(attempt.attempt_id, "launch_attempted", now=at + 1)
        history.transition_trigger(attempt.attempt_id, "verified", now=at + 35)

    def test_real_sync_path_cannot_advance_last_automatic_start_but_new_record_can(self):
        self.record_start(self.now - 100)
        window = self.window()
        window.refresh_clock(now=self.now)
        before = window.last_action_label.text()
        self.assertIn("Successful", before)
        times = iter(self.now + offset for offset in (0, 10, 20, 30))
        client = FakeClient([payload(int(self.now + 18000))] * 4)
        runner = CodexOperationRunner(self.provider.history, session_factory=lambda: FakeSession(client), clock=lambda: next(times), sleep=lambda _: None)
        result = runner.sync("runtime:1")
        window.active_operations["codex"] = "sync"
        window._operation_completed("codex", result)
        window.refresh_clock(now=self.now)
        self.assertEqual(before, window.last_action_label.text())
        self.assertEqual((4, 0, 0, 0), (client.read_calls, client.model_calls, client.thread_calls, client.turn_calls))
        self.record_start(self.now + 200, mode="bootstrap")
        self.assertEqual(before, automatic_start_history_copy(self.provider.history, now=self.now))
        self.record_start(self.now + 500)
        window.refresh_clock(now=self.now + 600)
        self.assertNotEqual(before, window.last_action_label.text())
        self.assertIn("Successful", window.last_action_label.text())
        self.provider.history.path.write_text("broken history")
        window.refresh_clock(now=self.now + 600)
        self.assertIn("History unavailable", window.last_action_label.text())


if __name__ == "__main__":
    unittest.main()
