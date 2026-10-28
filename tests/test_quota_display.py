"""Quota bars show remaining snapshots without changing request decisions."""
from dataclasses import replace
from datetime import datetime
import unittest
from unittest.mock import patch

from sentinel import i18n
from sentinel.app_state import ProviderViewState, automation_decision
import test_desktop as fixtures
from PySide6.QtWidgets import QApplication


class QuotaDisplayTests(unittest.TestCase):
    make_window = fixtures.DesktopTests.make_window

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def state(self):
        return replace(ProviderViewState.waiting("codex", "Codex", installed=True),
            **dict(status="Ready", runtime_identity="runtime:1", reset_at=2000,
                   last_verified_at=800, usage_checked_at=900, used_percent=12,
                   weekly_used_percent=34, weekly_reset_at=8000,
                   quota_state="ANCHORED"))

    def test_normal_empty_and_exhausted_bars_show_remaining_only(self):
        for language in ("en", "zh_CN"):
            for used, remaining in ((0, 100), (12, 88), (100, 0)):
                with self.subTest(language=language, used=used), patch.object(i18n, "_language", language):
                    state = replace(self.state(), used_percent=used, weekly_used_percent=used)
                    window, provider = self.make_window(state)
                    before = window.controller.store.load()
                    window.refresh_clock(now=1000)
                    card = window.provider_cards["codex"]
                    self.assertEqual(remaining, card.usage_bar.value())
                    self.assertEqual(remaining, window.weekly_bar.value())
                    self.assertIn(f"{remaining}%", card.usage_label.text())
                    self.assertIn(i18n.tr("remaining"), card.usage_label.text())
                    self.assertIn(i18n.tr("remaining"), window.weekly_value.text())
                    self.assertEqual(card.usage_label.text(), card.usage_bar.accessibleName())
                    self.assertEqual(before, window.controller.store.load())
                    self.assertEqual(state, window.controller.states["codex"])
                    self.assertEqual((0, 0, 0), (provider.action_calls, provider.sync_calls, provider.probe_calls))

    def test_unknown_is_not_presented_as_full_allowance(self):
        state = replace(self.state(), used_percent=None, weekly_used_percent=None)
        window, _ = self.make_window(state)
        window.refresh_clock(now=1000)
        card = window.provider_cards["codex"]
        self.assertIn("Not checked", card.usage_label.text())
        self.assertFalse(card.usage_bar.isEnabled())
        self.assertFalse(window.weekly_bar.isEnabled())
        self.assertEqual("—", window.weekly_value.text())

    def test_expired_snapshot_keeps_percentage_and_requests_a_read_only_sync(self):
        state = replace(self.state(), reset_at=950, weekly_reset_at=950)
        window, provider = self.make_window(state)
        window.refresh_clock(now=1000)
        card = window.provider_cards["codex"]
        self.assertEqual(88, card.usage_bar.value())
        self.assertIn("Reset passed; sync to update", card.usage_label.text())
        self.assertIn("Reset passed; sync to update", window.weekly_detail.text())
        self.assertEqual("SYNC NEEDED", window.weekly_status.text())
        self.assertEqual((0, 0, 0), (provider.action_calls, provider.sync_calls, provider.probe_calls))

    def test_last_sync_uses_the_latest_quota_read_not_old_anchor_time(self):
        state = replace(self.state(), last_verified_at=datetime(2030, 1, 15, 5).timestamp(),
                        usage_checked_at=datetime(2030, 1, 15, 7, 30).timestamp())
        window, _ = self.make_window(state)
        window.refresh_clock(now=datetime(2030, 1, 15, 8).timestamp())
        self.assertIn("7:30", window.provider_cards["codex"].metadata_label.text())

    def test_weekly_protection_still_uses_consumed_quota(self):
        for reset in (8000, 950):
            with self.subTest(weekly_reset_at=reset):
                window, _ = self.make_window(replace(self.state(), weekly_used_percent=99, weekly_reset_at=reset))
                window.controller.set_automation_enabled(True)
                window.controller.settings = replace(window.controller.settings,
                    compatible_runtime_identities={"codex": "runtime:1"})
                window.refresh_clock(now=1000)
                self.assertEqual(1, window.weekly_bar.value())
                self.assertEqual("1% remaining", window.weekly_value.text())
                self.assertEqual("PROTECTED", window.weekly_status.text())
                self.assertEqual("WEEKLY PROTECTED", window.provider_cards["codex"].status_label.text())

    def test_remaining_quota_does_not_clear_an_uncertain_send(self):
        state = replace(self.state(), used_percent=0, outcome_category="GUARDED",
                        last_action="ANCHOR_NOT_VERIFIED", automation_blocked_until=20000)
        window, _ = self.make_window(state)
        window.controller.set_automation_enabled(True)
        window.controller.settings = replace(window.controller.settings,
            compatible_runtime_identities={"codex": "runtime:1"})
        window.refresh_clock(now=1000)
        card = window.provider_cards["codex"]
        self.assertEqual(100, card.usage_bar.value())
        self.assertEqual("CLOCK RUNNING", card.status_label.text())
        self.assertIn("unconfirmed", card.detail_label.text())
        self.assertEqual("GUARDED", window.controller.states["codex"].outcome_category)
        self.assertEqual("ANCHOR_NOT_VERIFIED", window.controller.states["codex"].last_action)
        self.assertTrue(card.action_button.isHidden())
        self.assertEqual("WAIT", automation_decision(state=state, enabled=True, now=1000,
            compatible_runtime_identity="runtime:1").action)
