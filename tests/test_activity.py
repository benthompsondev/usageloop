"""Recent starts is useful across restarts and cannot launch Codex or edit state."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from sentinel.activity import RecentStartsDialog, START_OUTCOMES, activity_time
from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import DesktopShell, MainWindow
from sentinel.history import SafeHistory
from sentinel.ui_theme import desktop_stylesheet
from test_desktop import FakeProvider, FakeStartup, FakeThreadPool


class ActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.history = SafeHistory(Path(self.tmp.name) / "history.jsonl")
        self.now = 1788523200

    def record(self, state="verified", *, mode="rollover", offset=0):
        at = self.now + offset
        attempt = self.history.reserve_trigger(mode=mode, idempotency_key=f"test:{at}", boundary_reset_at=None, model="test", reasoning_effort="low", now=at)
        if state == "reserved":
            return
        self.history.transition_trigger(attempt.attempt_id, "launch_attempted", now=at + 1)
        self.history.transition_trigger(attempt.attempt_id, state, now=at + 30)

    def dialog(self):
        dialog = RecentStartsDialog(self.history)
        dialog.setStyleSheet(desktop_stylesheet())
        self.addCleanup(dialog.close)
        return dialog

    def texts(self, dialog):
        return "\n".join(label.text() for label in dialog.scroll.widget().findChildren(QLabel))

    def test_latest_ten_attempts_newest_first_and_manual_distinct(self):
        for i in range(12):
            self.record(mode="bootstrap" if i == 11 else "rollover", offset=i * 3600)
        text = self.texts(self.dialog())
        self.assertEqual(10, text.count("Confirmed"))
        self.assertTrue(text.startswith("Manual first start"))
        self.assertLess(text.index(activity_time(self.now + 11 * 3600)), text.index(activity_time(self.now + 10 * 3600)))
        self.assertNotIn(activity_time(self.now), text)
        self.assertNotIn("test:", text)
        self.assertNotIn("bootstrap", text)

    def test_all_saved_outcomes_are_honest_after_restart(self):
        for i, state in enumerate(START_OUTCOMES):
            if state == "launch_attempted":
                # Leave an interrupted attempt at its durable intermediate state.
                a = self.history.reserve_trigger(mode="rollover", idempotency_key="interrupted", boundary_reset_at=None, model="test", reasoning_effort="low", now=self.now - 100)
                self.history.transition_trigger(a.attempt_id, state, now=self.now - 99)
            else:
                self.record(state, offset=i * 100)
        self.history = SafeHistory(self.history.path)
        text = self.texts(self.dialog())
        for _, _, detail in START_OUTCOMES.values():
            self.assertIn(detail, text)
        self.assertNotIn("Starting", text)

    def test_empty_corrupt_and_unavailable_history_are_distinct(self):
        dialog = self.dialog()
        self.assertIn("No starts recorded yet", self.texts(dialog))
        self.history.path.write_text("broken")
        dialog.refresh()
        self.assertIn("History unavailable", self.texts(dialog))
        self.assertEqual("broken", self.history.path.read_text())
        with patch.object(self.history, "trigger_attempts", side_effect=PermissionError):
            dialog.refresh()
        self.assertIn("History unavailable", self.texts(dialog))

    def test_refresh_and_reopen_read_only_and_show_new_records(self):
        self.record()
        dialog = self.dialog()
        before = self.history.path.read_bytes()
        dialog.refresh()
        self.assertEqual(before, self.history.path.read_bytes())
        self.history.record_error("sync_failed")
        dialog.refresh()
        self.assertEqual(1, self.texts(dialog).count("Confirmed"))
        self.record("failed_guarded", offset=500)
        dialog.refresh()
        self.assertIn("Not confirmed", self.texts(dialog))
        self.assertEqual(self.texts(dialog), self.texts(self.dialog()))

    def test_dashboard_and_tray_open_same_read_only_window(self):
        provider = FakeProvider(ProviderViewState.waiting("codex", "Codex", installed=True))
        provider.history = self.history
        store = AppStateStore(Path(self.tmp.name) / "state.json")
        controller = ApplicationController([provider], store)
        controller.start()
        window = MainWindow(controller, {"codex": provider}, FakeStartup())
        window.clock_timer.stop()
        window.automation_timer.stop()
        window.thread_pool = FakeThreadPool()
        self.addCleanup(window.close)
        window.schedule_card.history_button.click()
        dialog = window.recent_starts_dialog
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.isVisible())
        dialog.close()
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)
        shell.history_action.trigger()
        self.assertIs(dialog, window.recent_starts_dialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual([], window.thread_pool.workers)
        self.assertEqual((0, 0, 0), (provider.action_calls, provider.probe_calls, provider.sync_calls))
        self.assertFalse(self.history.path.exists())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertFalse(dialog.isVisible())

    def test_small_dialog_scrolls_without_clipping_or_horizontal_scroll(self):
        for i in range(10):
            self.record("failed_guarded", offset=i * 100)
        dialog = self.dialog()
        dialog.resize(460, 360)
        dialog.show()
        self.app.processEvents()
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(0, dialog.scroll.horizontalScrollBar().maximum())
        for label in dialog.scroll.widget().findChildren(QLabel):
            if label.wordWrap():
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))

    def test_bad_timestamp_does_not_crash_presentation(self):
        self.assertEqual("Time unavailable", activity_time(float("nan")))
        self.assertEqual("Time unavailable", activity_time(float("inf")))

    def test_existing_13_records_expose_saved_model_and_effort_without_rewriting(self):
        self.record()
        before = self.history.path.read_bytes()
        self.assertIn("Model: test · Reasoning: low", self.texts(self.dialog()))
        self.assertEqual(before, self.history.path.read_bytes())

    def test_absent_or_unsafe_metadata_does_not_break_history_or_leak_text(self):
        import json
        self.record()
        rows = [json.loads(line) for line in self.history.path.read_text().splitlines()]
        rows[0]["model"] = "private text <bad>"
        rows[0].pop("reasoning_effort")
        self.history.path.write_text("\n".join(json.dumps(row) for row in rows))
        text = self.texts(self.dialog())
        self.assertIn("Model: Not recorded · Reasoning: Not recorded", text)
        self.assertNotIn("private", text)
        self.assertIn("Confirmed", text)


if __name__ == "__main__":
    unittest.main()
