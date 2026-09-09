"""Manual recovery through the real window, provider, runner and coordinator.

All quota, process, clock and startup surfaces are local test doubles.
"""
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore
from sentinel.desktop import MainWindow
from sentinel.history import SafeHistory
from sentinel.provider_runtime import CodexOperationRunner
from sentinel.providers import CodexProvider
from sentinel.ui_components import present_provider_state
from test_desktop import FakeStartup, FakeThreadPool
from test_provider_runtime import FakeClient, FakeSession, payload


class ManualLoopStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="usageloop-manual-test-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        isolated = patch.dict(os.environ, {key: str(root / key) for key in (
            "LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME")})
        isolated.start()
        self.addCleanup(isolated.stop)
        self.now = datetime(2026, 9, 9, 10, 22).timestamp()
        self.fixed = None
        self.used = 0
        self.weekly = 47
        self.verify = True
        self.history = SafeHistory(root / "history.jsonl")
        self.client = FakeClient([])
        self.client.read_rate_limits = self.read_quota
        self.client.start_turn = self.send_turn
        self.client.start_thread = self.start_thread
        self.runner = CodexOperationRunner(self.history,
            session_factory=lambda: FakeSession(self.client), clock=lambda: self.now,
            sleep=self.advance)
        self.provider = CodexProvider(history=self.history,
            executable_finder=lambda: root, identity_reader=lambda _: "fixture:1",
            version_reader=lambda _: "fixture", operation_runner=self.runner,
            now=lambda: self.now)
        self.controller = ApplicationController([self.provider], AppStateStore(root / "state.json"))
        self.controller.start()
        self.controller._save_settings(AppSettings(automation_enabled=True,
            schedule_mode="weekly", weekly_start_times=((4, 0),) * 7,
            compatible_runtime_identities={"codex": "fixture:1"}))
        self.window = MainWindow(self.controller, {"codex": self.provider}, FakeStartup(),
                                 confirm_bootstrap=lambda: True)
        self.window.clock_timer.stop()
        self.window.automation_timer.stop()
        self.window.thread_pool = FakeThreadPool()
        self.addCleanup(self.window.close)
        self.wall = patch("sentinel.desktop.time.time", lambda: self.now)
        self.wall.start()
        self.addCleanup(self.wall.stop)
        self.sync()

    def advance(self, seconds):
        self.now += seconds

    def read_quota(self):
        self.client.read_calls += 1
        value = payload(int(self.fixed or self.now + 18000), used=self.used)
        value["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] = self.weekly
        return value

    def start_thread(self, params):
        self.client.thread_calls += 1
        self.thread_params = params
        return "fixture-thread"

    def send_turn(self, params):
        self.client.turn_calls += 1
        self.turn_params = params
        if self.verify:
            self.fixed = int(self.now + 18000)

    def sync(self):
        self.window.start_usage_sync("codex")
        self.window.thread_pool.workers[-1].run()

    def click_start(self):
        count = len(self.window.thread_pool.workers)
        self.window.start_bootstrap("codex")
        if len(self.window.thread_pool.workers) > count:
            self.window.thread_pool.workers[-1].run()

    @property
    def state(self):
        return self.controller.states["codex"]

    @property
    def button(self):
        return self.window.provider_cards["codex"].action_button

    def test_unanchored_sync_offers_exactly_one_guarded_lightweight_start(self):
        self.assertEqual("UNANCHORED", self.state.quota_state)
        self.assertGreater(self.state.reset_at, self.now)
        self.assertEqual("Start continuous loop now", self.button.text())
        self.assertFalse(self.button.isHidden())
        settings = self.controller.settings
        reads = self.client.read_calls
        self.click_start()
        self.assertEqual(8, self.client.read_calls - reads)  # preflight + verification
        self.assertEqual(1, self.client.turn_calls)
        self.assertEqual("gpt-5.6-luna", self.thread_params["model"])
        self.assertEqual("low", self.turn_params["effort"])
        self.assertTrue(self.thread_params["ephemeral"])
        self.assertEqual("read-only", self.thread_params["sandbox"])
        self.assertEqual("ANCHOR_VERIFIED", self.state.last_action)
        self.assertEqual("verified", self.history.trigger_attempts()[-1].state)
        self.assertTrue(self.button.isHidden())
        self.assertEqual(settings, self.controller.settings)
        self.click_start()
        self.assertEqual(1, self.client.turn_calls)

    def test_current_anchor_hides_and_rejects_manual_start(self):
        self.fixed = int(self.now + 8000)
        self.sync()
        reads = self.client.read_calls
        self.assertEqual("ANCHORED", self.state.quota_state)
        self.assertTrue(self.button.isHidden())
        self.click_start()
        self.assertEqual(reads, self.client.read_calls)
        self.assertEqual(0, self.client.turn_calls)

    def test_anchor_appearing_after_render_is_caught_by_fresh_preflight(self):
        self.assertFalse(self.button.isHidden())
        self.fixed = int(self.now + 8000)
        reads = self.client.read_calls
        self.click_start()
        self.assertEqual(4, self.client.read_calls - reads)
        self.assertEqual(0, self.client.thread_calls)
        self.assertEqual(0, self.client.turn_calls)
        self.assertEqual("ALREADY_ANCHORED", self.state.last_action)
        self.assertTrue(self.button.isHidden())

    def test_exhausted_manual_check_keeps_fresh_reset_in_card_and_scheduler(self):
        # Old cached reset must not mask the useful timing from the fresh check.
        self.controller.update_provider_state(replace(self.state,
            reset_at=int(self.now - 86400), last_verified_at=self.now - 86500))
        self.used = 100
        self.fixed = int(datetime(2026, 9, 9, 12, 42).timestamp())
        self.click_start()
        self.assertEqual(0, self.client.turn_calls)
        self.assertEqual("EXHAUSTED", self.state.quota_state)
        self.assertEqual(self.fixed, self.state.reset_at)
        self.assertIsNone(self.state.last_verified_at)
        card = present_provider_state(self.state, now=self.now, automation_enabled=True)
        self.assertIn("12:42 PM", card.reset)
        self.assertEqual("WAIT", self.controller.decisions(now=self.now)["codex"].action)
        self.assertNotIn("due now", self.window.schedule_card.next_label.text())

    def test_sync_preserves_v134_read_only_reset_behavior_for_each_quota_state(self):
        for kind, used, fixed in (("UNANCHORED", 0, None),
                                  ("ANCHORED", 12, int(self.now + 5000)),
                                  ("EXHAUSTED", 100, int(self.now + 7000))):
            with self.subTest(kind=kind):
                self.used, self.fixed = used, fixed
                before = self.client.read_calls
                self.sync()
                self.assertEqual(4, self.client.read_calls - before)
                expected = fixed or int(self.now + 18000)
                self.assertEqual(expected, self.state.reset_at)
                self.assertEqual(kind, self.state.quota_state)
                self.assertEqual(used, self.state.used_percent)
                self.assertGreater(self.state.reset_at, self.now)
                self.assertIn("Resets", present_provider_state(self.state,
                    now=self.now, automation_enabled=True).reset)
        self.assertEqual(0, self.client.model_calls)
        self.assertEqual(0, self.client.thread_calls)
        self.assertEqual(0, self.client.turn_calls)
        self.assertEqual([], self.history.trigger_attempts())

    def test_success_hands_back_to_existing_weekly_and_continuous_scheduler(self):
        for mode in ("weekly", "continuous"):
            self.controller.set_schedule_mode(mode)
            if self.client.turn_calls == 0:
                self.click_start()
            self.assertEqual(mode, self.controller.settings.schedule_mode)
            self.assertEqual("WAIT", self.controller.decisions(now=self.now)["codex"].action)
            self.assertEqual("ROLLOVER", self.controller.decisions(now=self.fixed + 60)["codex"].action)
        self.assertEqual(1, self.client.turn_calls)

    def test_repeated_clicks_share_one_worker_and_uncertain_attempt_is_not_retried(self):
        self.verify = False
        self.window.start_bootstrap("codex")
        count = len(self.window.thread_pool.workers)
        self.window.start_bootstrap("codex")
        self.assertEqual(count, len(self.window.thread_pool.workers))
        self.window.thread_pool.workers[-1].run()
        self.assertEqual("ANCHOR_NOT_VERIFIED", self.state.last_action)
        self.controller.start()
        self.window.refresh_clock(now=self.now)
        self.click_start()
        self.assertEqual("BOOTSTRAP_COOLDOWN", self.state.last_action)
        self.assertEqual(1, self.client.turn_calls)

    def test_manual_start_respects_automation_pause_and_compatibility(self):
        for change in (lambda: self.controller.set_automation_enabled(False),
                       lambda: self.controller.pause_until_tomorrow(now=self.now),
                       lambda: self.controller.update_provider_state(replace(self.state,
                            runtime_identity="changed"))):
            settings, state = self.controller.settings, self.state
            change()
            self.window.refresh_clock(now=self.now)
            self.assertTrue(self.button.isHidden())
            self.click_start()
            self.assertEqual(0, self.client.turn_calls)
            self.controller._save_settings(settings)
            self.controller.update_provider_state(state)

    def test_weekly_and_model_gates_still_prevent_manual_send(self):
        self.weekly = 99
        self.click_start()
        self.assertEqual("WEEKLY_EXHAUSTED", self.state.last_action)
        self.assertEqual(0, self.client.turn_calls)
        self.weekly = 47
        self.client.model = False
        self.sync()
        self.click_start()
        self.assertEqual("TRIGGER_NOT_SENT", self.state.last_action)
        self.assertEqual(0, self.client.turn_calls)


if __name__ == "__main__":
    unittest.main()
