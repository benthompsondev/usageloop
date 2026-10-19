"""Language fallback, persistence, dynamic copy and automation invariants."""
from dataclasses import replace
import os
from pathlib import Path
from string import Formatter
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QLocale, QSettings, QTime
from PySide6.QtWidgets import QApplication, QMessageBox
from sentinel import i18n
from sentinel.app_state import AppSettings, ProviderViewState
from sentinel.presentation import operational_presentation
from sentinel.ui_components import TimeEntry, CHAIN_OUTCOME_COPY, _automatic_action_copy
import test_desktop as desktop_fixtures


class LocalizationTests(unittest.TestCase):
    make_window = desktop_fixtures.DesktopTests.make_window

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        settings = QSettings(str(Path(temporary.name) / "language.ini"), QSettings.Format.IniFormat)
        self.settings = settings
        self.addCleanup(patch.stopall)
        factory = patch("sentinel.i18n.QSettings", return_value=settings).start()
        factory.Status = QSettings.Status
        patch.object(i18n, "_language", "zh_CN").start()

    def test_preference_round_trip_does_not_change_running_language(self):
        self.assertEqual("system", i18n.language_preference())
        i18n.save_language("en")
        self.assertEqual("en", i18n.language_preference())
        self.assertEqual("设置", i18n.tr("Settings"))
        with self.assertRaises(ValueError):
            i18n.save_language("unsupported")
        self.assertEqual("en", i18n.language_preference())

    def test_invalid_saved_preference_and_unknown_text_fall_back(self):
        self.settings.setValue("language", "invalid")
        self.assertEqual("system", i18n.language_preference())
        self.assertEqual("New text {0}", i18n.tr("New text {0}"))
        with patch.object(i18n, "_language", "en"):
            self.assertEqual("Settings", i18n.tr("Settings"))

    def test_system_script_and_environment_override(self):
        for locale, expected in [("zh_CN", "zh_CN"), ("zh_SG", "zh_CN"),
                                 ("zh_TW", "en"), ("en_US", "en")]:
            with self.subTest(locale=locale), patch.dict(os.environ, {"USAGELOOP_LANGUAGE": "system"}), \
                    patch("sentinel.i18n.QLocale.system", return_value=QLocale(locale)):
                self.assertEqual(expected, i18n.resolved_language())
        with patch.dict(os.environ, {"USAGELOOP_LANGUAGE": "en"}):
            self.assertEqual("en", i18n.resolved_language())

    def test_catalogue_preserves_all_format_fields(self):
        def fields(text):
            return sorted((field, spec, conversion) for _, field, spec, conversion
                          in Formatter().parse(text) if field is not None)
        for source, translated in i18n.ZH_CN.items():
            with self.subTest(source=source):
                self.assertTrue(translated)
                self.assertEqual(fields(source), fields(translated))

    def test_ui_and_language_choice_do_not_start_requests_or_modify_schedule(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=True)
        window, provider = self.make_window(state)
        self.assertEqual(["概览", "设置", "关于"], [button.text() for button in window.nav_buttons])
        self.assertEqual("自动衔接五小时窗口", window.automation_title_label.text())
        self.assertEqual("设置启动计划", window.overall_title.text())
        before = window.controller.store.load()
        window.language_choice.setCurrentIndex(window.language_choice.findData("en"))
        self.assertEqual("en", i18n.language_preference())
        self.assertEqual(before, window.controller.store.load())
        self.assertEqual({}, window.active_operations)
        self.assertEqual((0, 0, 0), (provider.probe_calls, provider.action_calls, provider.sync_calls))
        self.assertEqual("设置", window.nav_buttons[1].text())

    def test_failed_preference_save_restores_previous_value(self):
        i18n.save_language("system")
        with patch.object(self.settings, "status", return_value=QSettings.Status.AccessError):
            with self.assertRaises(OSError):
                i18n.save_language("en")
        self.assertEqual("system", i18n.language_preference())

    def test_chinese_probe_keeps_internal_status_in_english(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=True)
        window, _ = self.make_window(state)
        window.thread_pool = desktop_fixtures.FakeThreadPool()
        window.recheck_compatibility()
        self.assertEqual("Checking", window.controller.states["codex"].status)
        self.assertEqual("正在检查 Codex 连接", window.overall_title.text())
        self.assertEqual(1, len(window.thread_pool.workers))

    def test_presentation_language_preserves_all_decision_fields(self):
        state = ProviderViewState.waiting("codex", "Codex", installed=True,
                                          runtime_identity="runtime:1")
        settings = AppSettings(automation_enabled=True,
                              compatible_runtime_identities={"codex": "runtime:1"},
                              checked_runtime_identities={"codex": "runtime:1"})
        states = [None, state, replace(state, installed=False),
                  replace(state, status="Checking"), replace(state, status="Starting"),
                  replace(state, status="Needs attention"),
                  replace(state, reset_at=20000, weekly_used_percent=10),
                  replace(state, weekly_used_percent=99)]
        for item in states:
            with self.subTest(state=item):
                with patch.object(i18n, "_language", "en"):
                    english = operational_presentation(settings, item, now=1000)
                chinese = operational_presentation(settings, item, now=1000)
                for field in ("kind", "tone", "manual_start_visible"):
                    self.assertEqual(getattr(english, field), getattr(chinese, field))
        self.assertEqual("Waiting", state.status)

    def test_chinese_time_input_uses_24_hours(self):
        editor = TimeEntry()
        self.assertEqual("HH:mm", editor.displayFormat())
        editor.setTime(QTime(0, 0))
        self.assertEqual("00:00", editor.text())
        editor.setTime(QTime(12, 0))
        self.assertEqual("12:00", editor.text())
        with patch.object(i18n, "_language", "en"):
            english = TimeEntry()
            self.assertEqual("h:mm AP", english.displayFormat())

    def test_outcome_values_translate_without_changing_identifiers(self):
        for outcome in CHAIN_OUTCOME_COPY:
            state = ProviderViewState.waiting("codex", "Codex", installed=True)
            state = replace(state, last_action=outcome)
            text = _automatic_action_copy(state, now=100)
            self.assertTrue(text.startswith("上次自动启动："))
            self.assertNotIn(outcome, text)
            self.assertNotIn(CHAIN_OUTCOME_COPY[outcome], text)
            self.assertEqual(outcome, state.last_action)

    def test_startup_configuration_installs_and_replaces_qt_translator(self):
        with patch.dict(os.environ, {"USAGELOOP_LANGUAGE": "zh_CN"}):
            i18n.configure_language()
        self.assertEqual("取消", QApplication.translate("QPlatformTheme", "Cancel"))
        with patch.dict(os.environ, {"USAGELOOP_LANGUAGE": "en"}):
            i18n.configure_language()
        self.assertEqual("Cancel", QApplication.translate("QPlatformTheme", "Cancel"))

    def test_standard_dialog_buttons_are_translated(self):
        translator = i18n.DialogTranslator()
        self.assertEqual("取消", translator.translate("QPlatformTheme", "Cancel"))
        self.assertIsNone(translator.translate("Unrelated", "Cancel"))
        with patch.object(i18n, "_language", "en"):
            self.assertIsNone(translator.translate("QPlatformTheme", "Cancel"))
