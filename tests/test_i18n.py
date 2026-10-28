"""Language fallback, persistence, dynamic copy and automation invariants."""
from dataclasses import replace
import ast
import os
from pathlib import Path
from datetime import datetime
from string import Formatter
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QLocale, QSettings, QTime
from PySide6.QtWidgets import QApplication, QMessageBox
from sentinel import i18n
from sentinel.app_state import AppSettings, ProviderViewState
from sentinel.presentation import operational_presentation
from sentinel.desktop import DesktopShell
from sentinel.ui_components import _schedule_time, weekly_schedule_preview_details
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

    def test_literal_translation_calls_have_catalogue_entries(self):
        def literals(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield node.value
            elif isinstance(node, ast.IfExp):
                yield from literals(node.body)
                yield from literals(node.orelse)

        missing = []
        for path in Path(i18n.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and node.args):
                    for source in literals(node.args[0]):
                        if source and not source.isdigit() and source not in i18n.ZH_CN:
                            missing.append((path.name, node.lineno, source))
        self.assertEqual([], missing)

    def test_formatted_update_errors_are_translated_before_values_are_inserted(self):
        from urllib.error import HTTPError
        from sentinel.updates import GitHubReleaseUpdater, UpdateError, parse_release
        from test_updates import release_payload

        def unavailable(_request, _timeout):
            raise HTTPError("https://example.invalid", 503, "unavailable", {}, None)

        with self.assertRaises(UpdateError) as error:
            GitHubReleaseUpdater(opener=unavailable).check()
        self.assertIn("503", str(error.exception))
        self.assertNotIn("GitHub replied with", str(error.exception))
        for windows, platforms in ((True, ("windows",)), (False, ("linux",))):
            with self.subTest(windows=windows), self.assertRaises(UpdateError) as error:
                parse_release(release_payload(version="1.4.1", include_checksum=False,
                                              platforms=platforms),
                              installed_version="1.4.0", windows=windows)
            self.assertNotIn("The release is missing", str(error.exception))

    def test_linux_archive_rejection_is_translated_without_changing_the_guard(self):
        from sentinel.linux_update import LinuxUpdateError, _safe_member_path
        with self.assertRaises(LinuxUpdateError) as error:
            _safe_member_path("../outside")
        self.assertNotIn("The downloaded archive was rejected", str(error.exception))
        self.assertNotIn("it tried to write", str(error.exception))

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

    def test_chinese_midnight_survives_schedule_and_reset_formatting(self):
        midnight = datetime(2030, 1, 16, 0, 0).timestamp()
        now = midnight - 3600
        self.assertEqual("明天 00:00", _schedule_time(midnight, now=now))
        preview = weekly_schedule_preview_details(((0, 0),) * 7, now=now)
        self.assertEqual("00:00", preview.first_start)
        state = replace(ProviderViewState.waiting("codex", "Codex", installed=True),
                        status="Ready", runtime_identity="runtime:1", reset_at=int(midnight),
                        weekly_used_percent=20, quota_state="ANCHORED")
        settings = AppSettings(automation_enabled=True,
                              compatible_runtime_identities={"codex": "runtime:1"})
        result = operational_presentation(settings, state, now=now)
        self.assertIn("00:01", result.next_action)

    def test_weekly_widget_identifiers_are_language_independent(self):
        window, _ = self.make_window(ProviderViewState.waiting("codex", "Codex", installed=True))
        self.assertEqual("weeklyMondayTime", window.weekly_day_times[0].objectName())
        self.assertIn("星期一", window.weekly_day_times[0].accessibleName())

    def test_tray_resume_survives_switch_to_continuous_during_pause(self):
        window, provider = self.make_window(ProviderViewState.waiting("codex", "Codex", installed=True))
        window.controller.settings = replace(window.controller.settings,
            automation_enabled=True, schedule_mode="continuous", automation_paused_until=2000)
        shell = DesktopShell(window)
        self.addCleanup(shell.tray.hide)
        shell.refresh_menu(now=1000)
        self.assertTrue(shell.pause_action.isVisible())
        self.assertEqual("恢复自动启动", shell.pause_action.text())
        self.assertEqual(0, provider.action_calls)

    def test_saved_language_is_applied_in_a_new_process(self):
        # Use real Qt INI persistence without touching this user's registry.
        with tempfile.TemporaryDirectory() as directory:
            script = '''
import sys
from PySide6.QtCore import QSettings
from sentinel import i18n
i18n.QSettings = lambda *args: QSettings(sys.argv[1], QSettings.Format.IniFormat)
i18n.QSettings.Status = QSettings.Status
if sys.argv[2] == "save":
    i18n.save_language("zh_CN")
else:
    i18n.configure_language()
    assert i18n.current_language() == "zh_CN"
    assert i18n.tr("Settings") == "设置"
'''
            environment = dict(os.environ)
            environment.pop("USAGELOOP_LANGUAGE", None)
            for action in ("save", "restart"):
                result = subprocess.run([sys.executable, "-c", script,
                    str(Path(directory) / "language.ini"), action],
                    env=environment, capture_output=True, text=True, timeout=20)
                self.assertEqual(0, result.returncode, result.stderr)
