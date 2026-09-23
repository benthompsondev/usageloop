"""Focused release regressions; all providers and state roots here are synthetic."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtWidgets import QApplication

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.classifier import Classification
from sentinel.history import SafeHistory
from sentinel.presentation import operational_presentation
from sentinel.providers import CodexProvider, CompatibilityResult
from sentinel.quota import QuotaSnapshot, QuotaWindow
from sentinel.desktop import MainWindow
from test_desktop import FakeStartup, FakeThreadPool


class StaticProvider:
    provider_id = "codex"

    def __init__(self, state):
        self.state = state
        self.probe_calls = 0

    def detect(self):
        return self.state

    def probe(self):
        self.probe_calls += 1
        return CompatibilityResult(True, "Waiting", "Compatible.", self.state.runtime_identity)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.history = SafeHistory(Path(self.root.name) / "history.jsonl")
        self.provider = StaticProvider(ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"
        ))
        self.store = AppStateStore(Path(self.root.name) / "state.json")
        self.controller = self.start_controller()
        self.assertTrue(self.controller.set_automation_enabled(True))

    def start_controller(self):
        controller = ApplicationController(
            [self.provider], self.store, error_history=self.history
        )
        controller.start()
        return controller

    def failure(self, category="app_server_unavailable", identity="runtime:1"):
        return CompatibilityResult(
            False, "Reconnecting", "The Codex connection is temporarily unavailable.",
            identity, failure_category=category,
        )

    def success(self, identity="runtime:1"):
        return CompatibilityResult(True, "Waiting", "Compatibility confirmed.", identity)

    def test_transient_retry_timing_survives_restart_and_success_only_restores_eligibility(self):
        c = self.controller
        self.assertTrue(c.apply_compatibility("codex", self.failure(), now=100))
        self.assertEqual(1, c.states["codex"].compatibility_attempts)
        self.assertEqual(160, c.states["codex"].compatibility_next_retry_at)
        self.assertEqual("WAIT", c.decisions(now=159)["codex"].action)
        self.assertEqual("PROBE", c.decisions(now=160)["codex"].action)
        c = self.start_controller()
        self.assertEqual(1, c.states["codex"].compatibility_attempts)
        self.assertEqual("WAIT", c.decisions(now=159)["codex"].action)
        self.assertTrue(c.apply_compatibility("codex", self.failure(), now=160))
        self.assertEqual(280, c.states["codex"].compatibility_next_retry_at)
        self.assertTrue(c.apply_compatibility("codex", self.success(), now=280))
        self.assertIsNone(c.states["codex"].compatibility_next_retry_at)
        self.assertEqual("runtime:1", c.settings.compatible_runtime_identities["codex"])
        self.assertNotEqual("ROLLOVER", c.decisions(now=280)["codex"].action)

    def test_exhaustion_caps_episode_and_does_not_reset_on_ticks_or_restart(self):
        c = self.controller
        for now, next_retry in ((100, 160), (160, 280), (280, 580),
                                (580, 1180), (1180, 2080), (2080, None)):
            self.assertTrue(c.apply_compatibility("codex", self.failure(), now=now))
            self.assertEqual(next_retry, c.states["codex"].compatibility_next_retry_at)
            self.assertEqual("WAIT" if next_retry else "NONE",
                             c.decisions(now=now)["codex"].action)
        self.assertEqual(6, c.states["codex"].compatibility_attempts)
        c = self.start_controller()
        self.assertEqual("NONE", c.decisions(now=4000)["codex"].action)
        self.assertEqual(6, c.states["codex"].compatibility_attempts)

    def test_nonretryable_pause_off_and_obsolete_identity_cannot_resume(self):
        c = self.controller
        self.assertTrue(c.apply_compatibility("codex", self.failure("authentication_unavailable"), now=100))
        self.assertIsNone(c.states["codex"].compatibility_next_retry_at)
        self.assertEqual("NONE", c.decisions(now=1000)["codex"].action)
        self.provider.state = replace(self.provider.state, runtime_identity="runtime:2")
        c.refresh_local_states()
        self.assertFalse(c.apply_compatibility("codex", self.success(), now=200))
        self.assertNotIn("runtime:1", c.settings.compatible_runtime_identities.values())
        self.assertTrue(c.set_automation_enabled(False))
        self.assertEqual("NONE", c.decisions(now=300)["codex"].action)

    def test_off_and_explicit_pause_end_pending_retries_without_reenable(self):
        c = self.controller
        self.assertTrue(c.apply_compatibility("codex", self.failure(), now=100))
        self.assertTrue(c.set_automation_enabled(False))
        self.assertIsNone(c.states["codex"].compatibility_next_retry_at)
        self.assertEqual("NONE", c.decisions(now=160)["codex"].action)
        self.assertTrue(c.set_automation_enabled(True))
        self.assertEqual("NONE", c.decisions(now=160)["codex"].action)
        later = datetime(2026, 9, 23, 12).timestamp()
        self.assertTrue(c.apply_compatibility("codex", self.failure(), now=later, explicit=True))
        c.set_schedule_mode("daily")
        self.assertTrue(c.pause_until_tomorrow(now=later + 1))
        self.assertIsNone(c.states["codex"].compatibility_next_retry_at)
        self.assertEqual("WAIT", c.decisions(now=later + 2)["codex"].action)

    def test_expiration_is_actionable_once_and_preserves_history(self):
        c = self.controller
        self.assertTrue(c.apply_compatibility("codex", self.failure(), now=100))
        self.assertEqual(["codex"], c.expire_compatibility(now=3701))
        self.assertEqual([], c.expire_compatibility(now=3702))
        self.assertEqual("NONE", c.decisions(now=3702)["codex"].action)
        self.assertEqual(1, sum(row["phase"] == "exhausted"
                                for row in self.history.recent_compatibility_events()))

    def test_read_only_sync_updates_quota_without_erasing_recovery_or_send_guard(self):
        c = self.controller
        prior = replace(c.states["codex"], automation_blocked_until=1000,
                        last_action="ANCHOR_NOT_VERIFIED")
        c.update_provider_state(prior)
        c.apply_compatibility("codex", self.failure(), now=100)
        incident = c.states["codex"].compatibility_incident_id
        sample = replace(c.states["codex"], status="Ready", reset_at=900,
                         weekly_used_percent=20, usage_checked_at=130,
                         compatibility_incident_id=None,
                         compatibility_next_retry_at=None)
        self.assertTrue(c.apply_sync_result(sample))
        state = c.states["codex"]
        self.assertEqual(incident, state.compatibility_incident_id)
        self.assertEqual(160, state.compatibility_next_retry_at)
        self.assertEqual(900, state.reset_at)
        self.assertEqual(1000, state.automation_blocked_until)
        self.assertEqual("ANCHOR_NOT_VERIFIED", state.last_action)
        self.assertEqual("WAIT", c.decisions(now=140)["codex"].action)
        c.apply_compatibility("codex", self.success(), now=160)
        self.assertEqual("WAIT", c.decisions(now=170)["codex"].action)

    def test_notification_dedup_survives_restart(self):
        c = self.controller
        c.apply_compatibility("codex", self.failure(), now=100)
        self.assertTrue(c.mark_compatibility_notified("codex", kind="blocked"))
        self.assertFalse(c.mark_compatibility_notified("codex", kind="blocked"))
        c = self.start_controller()
        self.assertFalse(c.mark_compatibility_notified("codex", kind="blocked"))
        self.assertTrue(c.mark_compatibility_notified("codex", kind="terminal"))
        self.assertFalse(c.mark_compatibility_notified("codex", kind="terminal"))

    def test_due_opportunity_is_recorded_once_without_becoming_a_trigger_attempt(self):
        c = self.controller
        c.set_schedule_mode("daily")
        c.set_daily_start_time(4, 30)
        due = datetime(2026, 9, 23, 5, 0).timestamp()
        boundary = datetime(2026, 9, 22, 23, 0).timestamp()
        c.update_provider_state(replace(c.states["codex"], reset_at=int(boundary)))
        c.apply_compatibility("codex", self.failure(), now=due - 60)
        self.assertTrue(c.record_blocked_opportunity("codex", now=due))
        self.assertFalse(c.record_blocked_opportunity("codex", now=due + 15))
        c = self.start_controller()
        self.assertFalse(c.record_blocked_opportunity("codex", now=due + 30))
        rows = [json.loads(line) for line in self.history.path.read_text().splitlines()]
        self.assertEqual(1, sum(row.get("event") == "compatibility_blocked_start" for row in rows))
        self.assertEqual([], self.history.trigger_attempts())

    def test_recovery_refresh_keeps_verified_boundary_through_restart(self):
        clock = [900]
        provider = CodexProvider(
            history=self.history, executable_finder=lambda: Path("fake-codex"),
            identity_reader=lambda _: "runtime:1", version_reader=lambda _: "test",
            now=lambda: clock[0],
        )
        controller = ApplicationController([provider], self.store, error_history=self.history)
        controller.start()
        controller.set_automation_enabled(True)
        controller.update_provider_state(replace(
            controller.states["codex"], reset_at=940, last_verified_at=900,
            usage_checked_at=900, weekly_used_percent=10, weekly_reset_at=80_000,
            quota_state="ANCHORED",
        ))
        controller.apply_compatibility("codex", self.failure(), now=1000)
        for observed_at in (1010, 1020, 1030, 1040):
            self.history.record_observation(
                QuotaSnapshot(observed_at, (
                    QuotaWindow("codex", "primary", 0, 300, observed_at + 18_000, None),
                    QuotaWindow("codex", "secondary", 10, 10080, 80_000, None),
                )),
                Classification("UNANCHORED", "high", "sliding", {"sample_count": 4}),
                "test",
            )
        clock[0] = 1040
        controller.apply_compatibility("codex", self.success(), now=1040)
        controller.refresh_local_states()
        state = controller.states["codex"]
        self.assertEqual(940, state.reset_at)
        self.assertEqual(19040, self.history.load_recent(now=1040)[-1].windows[0].resets_at)
        self.assertEqual("ROLLOVER", controller.decisions(now=1040)["codex"].action)
        self.assertEqual("due", operational_presentation(controller.settings, state, now=1040).kind)
        controller = ApplicationController([provider], self.store, error_history=self.history)
        controller.start()
        self.assertEqual(940, controller.states["codex"].reset_at)
        self.assertEqual("ROLLOVER", controller.decisions(now=1040)["codex"].action)
        day = datetime(2026, 9, 23, 11, 0).timestamp()
        controller.update_provider_state(replace(
            controller.states["codex"],
            reset_at=int(datetime(2026, 9, 23, 5, 0).timestamp()),
            last_verified_at=day - 6 * 3600,
        ))
        controller.set_schedule_mode("daily")
        controller.set_daily_start_time(4, 30)
        self.assertEqual("WAIT", controller.decisions(now=day)["codex"].action)
        controller = ApplicationController([provider], self.store, error_history=self.history)
        controller.start()
        self.assertEqual("WAIT", controller.decisions(now=day)["codex"].action)


class PresentationTests(unittest.TestCase):
    NOW = datetime(2026, 9, 23, 11, 0).timestamp()

    def settings(self, **changes):
        from sentinel.app_state import AppSettings
        return replace(AppSettings(automation_enabled=True, schedule_mode="daily",
                                   daily_start_hour=4, daily_start_minute=30,
                                   compatible_runtime_identities={"codex": "runtime:1"},
                                   checked_runtime_identities={"codex": "runtime:1"}), **changes)

    def state(self, **changes):
        return replace(ProviderViewState.waiting("codex", "Codex", installed=True,
                                                runtime_identity="runtime:1"), **changes)

    def test_normal_daily_wait_and_weekly_pause_have_no_alarm_or_manual_start(self):
        daily = self.state(reset_at=int(datetime(2026, 9, 23, 5).timestamp()),
                           quota_state="UNANCHORED", usage_checked_at=self.NOW,
                           weekly_used_percent=20, weekly_reset_at=int(self.NOW + 50000))
        result = operational_presentation(self.settings(), daily, now=self.NOW)
        self.assertEqual("waiting", result.kind)
        self.assertIn("scheduled", result.next_action.lower())
        self.assertFalse(result.manual_start_visible)
        self.assertNotIn("attention", result.card_status.lower())
        self.assertNotIn("first window", result.banner_title.lower())
        weekly = self.state(reset_at=int(datetime(2026, 9, 23, 0, 0).timestamp()),
                            quota_state="UNANCHORED", usage_checked_at=self.NOW,
                            weekly_used_percent=20, weekly_reset_at=int(self.NOW + 50000))
        at_night = datetime(2026, 9, 23, 2).timestamp()
        result = operational_presentation(
            self.settings(schedule_mode="weekly", weekly_start_times=((4, 30),) * 7),
            weekly, now=at_night,
        )
        self.assertEqual("overnight_pause", result.kind)
        self.assertFalse(result.manual_start_visible)
        self.assertIn("pause", result.next_action.lower())
        self.assertIn("pause", result.tray_text.lower())

    def test_valid_weekly_only_is_calm_but_unknown_and_missing_weekly_are_not(self):
        weekly_only = self.state(quota_state="ABSENT", quota_evidence="valid_weekly_only",
                                 usage_checked_at=self.NOW, weekly_used_percent=18,
                                 weekly_reset_at=int(self.NOW + 50000))
        result = operational_presentation(self.settings(), weekly_only, now=self.NOW)
        self.assertEqual("no_five_hour", result.kind)
        self.assertFalse(result.manual_start_visible)
        self.assertIn("isn't reporting a five-hour window", result.banner_detail)
        for state in (replace(weekly_only, quota_evidence="inconclusive"),
                      replace(weekly_only, quota_state="UNKNOWN"),
                      replace(weekly_only, weekly_used_percent=None)):
            self.assertNotEqual("no_five_hour",
                                operational_presentation(self.settings(), state, now=self.NOW).kind)

    def test_protection_failure_off_and_reconnecting_qualify_next_action(self):
        due = self.state(reset_at=int(self.NOW - 3600), quota_state="UNANCHORED",
                         weekly_used_percent=99, weekly_reset_at=int(self.NOW + 50000))
        protected = operational_presentation(self.settings(), due, now=self.NOW)
        self.assertEqual("weekly_protected", protected.kind)
        self.assertFalse(protected.manual_start_visible)
        self.assertIn("weekly", protected.next_action.lower())
        failed = operational_presentation(self.settings(), replace(due,
            status="Needs attention", compatibility_failure_category="authentication_unavailable"), now=self.NOW)
        self.assertEqual("needs_attention", failed.kind)
        self.assertNotIn("due now", failed.next_action.lower())
        reconnecting = operational_presentation(self.settings(), replace(due,
            status="Reconnecting", compatibility_failure_category="app_server_unavailable",
            compatibility_next_retry_at=self.NOW + 60), now=self.NOW)
        self.assertEqual("reconnecting", reconnecting.kind)
        self.assertIn("reconnect", reconnecting.tray_text.lower())
        off = operational_presentation(self.settings(automation_enabled=False), due, now=self.NOW)
        self.assertEqual("automation_off", off.kind)
        self.assertFalse(off.manual_start_visible)


class LateProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.provider = StaticProvider(ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="runtime:1"))
        self.controller = ApplicationController(
            [self.provider], AppStateStore(Path(directory.name) / "state.json"),
            error_history=SafeHistory(Path(directory.name) / "history.jsonl"))
        self.controller.start()
        self.controller.set_automation_enabled(True)
        self.window = MainWindow(self.controller, {"codex": self.provider}, FakeStartup())
        self.window.clock_timer.stop()
        self.window.automation_timer.stop()
        self.window.thread_pool = FakeThreadPool()
        self.addCleanup(self.window.close)
        self.notices = []
        self.window.notification_requested.connect(lambda *args: self.notices.append(args))

    def pending(self):
        before = len(self.window.thread_pool.workers)
        self.window._start_operation("codex", "probe", automatic=True)
        self.assertEqual(before + 1, len(self.window.thread_pool.workers))

    def success(self):
        return CompatibilityResult(True, "Waiting", "Compatible.", "runtime:1")

    def test_automation_off_or_pause_discards_late_success(self):
        for stop in ("off", "pause"):
            with self.subTest(stop=stop):
                self.pending()
                if stop == "off":
                    self.controller.set_automation_enabled(False)
                else:
                    self.controller.set_schedule_mode("daily")
                    self.controller.pause_until_tomorrow(now=time.time())
                self.window._operation_completed("codex", self.success())
                self.assertNotIn("codex", self.controller.settings.compatible_runtime_identities)
                self.assertEqual([], self.notices)
                if stop == "off":
                    self.controller.set_automation_enabled(True)

    def test_obsolete_identity_and_newer_compatibility_state_win(self):
        self.pending()
        current = self.controller.states["codex"]
        self.controller.update_provider_state(replace(current, runtime_identity="runtime:2"))
        self.window._operation_completed("codex", self.success())
        self.assertEqual("runtime:2", self.controller.states["codex"].runtime_identity)
        self.assertNotIn("codex", self.controller.settings.compatible_runtime_identities)

    def test_provider_identity_change_during_probe_rejects_stale_success(self):
        self.pending()
        self.provider.state = replace(self.provider.state, runtime_identity="runtime:2")
        self.window.evaluate_automation(now=time.time())
        self.assertEqual("runtime:1", self.controller.states["codex"].runtime_identity)
        self.assertEqual(1, len(self.window.thread_pool.workers))
        self.window._operation_completed("codex", self.success())
        self.assertEqual("runtime:2", self.controller.states["codex"].runtime_identity)
        self.assertNotIn("codex", self.controller.settings.compatible_runtime_identities)
        self.assertEqual([], self.notices)

    def test_provider_identity_change_during_probe_rejects_stale_failure(self):
        self.pending()
        self.provider.state = replace(self.provider.state, runtime_identity="runtime:2")
        self.window.evaluate_automation(now=time.time())
        self.assertEqual(1, len(self.window.thread_pool.workers))
        self.window._operation_failed("codex", "app_server_timeout")
        self.assertEqual("runtime:2", self.controller.states["codex"].runtime_identity)
        self.assertIsNone(self.controller.states["codex"].compatibility_incident_id)
        self.assertEqual([], self.notices)

    def test_technical_details_show_blocked_and_recovered_incident_after_restart(self):
        now = time.time()
        state = replace(self.controller.states["codex"], reset_at=int(now - 100),
                        last_verified_at=now - 200, weekly_used_percent=10,
                        weekly_reset_at=int(now + 80_000))
        self.controller.update_provider_state(state)
        failure = CompatibilityResult(False, "Needs attention", "Temporary failure.",
                                      "runtime:1", failure_category="app_server_timeout")
        self.controller.apply_compatibility("codex", failure, now=now)
        self.assertTrue(self.controller.record_blocked_opportunity("codex", now=now + 1))
        self.assertFalse(self.controller.record_blocked_opportunity("codex", now=now + 2))
        self.controller.apply_compatibility("codex", self.success(), now=now + 3)
        self.controller.start()
        self.window.show_page(1)
        self.window.refresh_clock(now=now + 4)
        detail = self.window.diagnostic_text.text()
        self.assertIn("app_server_timeout", detail)
        self.assertIn("Scheduled start blocked", detail)
        self.assertIn("Recovered", detail)
        self.assertEqual(1, sum(row["phase"] == "blocked_start" for row in
                                self.controller.error_history.recent_compatibility_events()))
        self.assertEqual([], self.controller.error_history.trigger_attempts())
        self.assertEqual(0, self.provider.probe_calls)

    def test_late_failure_cannot_override_newer_compatibility_success(self):
        self.pending()
        self.controller.apply_compatibility("codex", self.success(), explicit=True)
        self.window._operation_completed("codex", CompatibilityResult(
            False, "Needs attention", "Late failure.", "runtime:1",
            failure_category="app_server_timeout"))
        self.assertEqual("runtime:1", self.controller.settings.compatible_runtime_identities["codex"])
        self.assertTrue(self.controller.states["codex"].automation_supported)

    def test_duplicate_scheduler_ticks_queue_one_probe(self):
        current = time.time()
        self.window.evaluate_automation(now=current)
        self.window.evaluate_automation(now=current + 1)
        self.assertEqual(1, len(self.window.thread_pool.workers))
        self.assertEqual("probe", self.window.active_operations["codex"])

    def test_blocked_and_terminal_notifications_each_emit_once_then_recovery_once(self):
        self.controller.apply_compatibility("codex", CompatibilityResult(
            False, "Needs attention", "Temporary failure.", "runtime:1",
            failure_category="app_server_timeout"), now=time.time())
        self.window._notify_compatibility("codex", "Blocked", "No start.", kind="blocked")
        self.window._notify_compatibility("codex", "Blocked", "No start.", kind="blocked")
        self.window._notify_compatibility("codex", "Action needed", "Check Codex.", kind="terminal")
        self.window._notify_compatibility("codex", "Action needed", "Check Codex.", kind="terminal")
        self.assertEqual(["Blocked", "Action needed"], [title for title, _ in self.notices])
        self.pending()
        self.window._operation_completed("codex", self.success())
        self.assertEqual("Codex connection restored", self.notices[-1][0])
        self.assertEqual(3, len(self.notices))
