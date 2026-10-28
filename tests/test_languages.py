"""Six-language coverage, restart isolation and rendered UI contracts."""
import ast
from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
from string import Formatter
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QLocale, QSettings, QTime, Qt
from PySide6.QtGui import QFontDatabase, QTextLayout
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QStyle, QStyleOptionButton, QStyleOptionComboBox

from sentinel import i18n
from sentinel.app_state import ProviderViewState
from sentinel.locales import CATALOGUES, DIALOG_BUTTONS
from sentinel.presentation import operational_presentation
from sentinel.ui_components import TimeEntry
from test_layout import build_window

LANGUAGE_CODES = ("en", "zh_CN", "es", "pt_BR", "fr", "ja")


class MultilingualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = QSettings(str(Path(self.directory.name) / "language.ini"), QSettings.Format.IniFormat)
        factory = patch("sentinel.i18n.QSettings", return_value=settings).start()
        factory.Status = QSettings.Status
        self.addCleanup(patch.stopall)

    def test_six_languages_have_matching_nonempty_catalogues_and_placeholders(self):
        self.assertEqual(set(LANGUAGE_CODES), {code for code, _ in i18n.LANGUAGES} - {"system"})
        source_keys = set(CATALOGUES["zh_CN"])
        for language, messages in CATALOGUES.items():
            with self.subTest(language=language):
                self.assertEqual(source_keys, set(messages))
                for source, translated in messages.items():
                    self.assertTrue(translated.strip(), (language, source))
                    fields = lambda value: sorted((field, spec, conversion)
                        for _, field, spec, conversion in Formatter().parse(value) if field is not None)
                    self.assertEqual(fields(source), fields(translated), (language, source))
        # Duplicate source keys would silently overwrite a translation in Python.
        paths = [Path(i18n.__file__).parent / "translations.py"]
        paths.extend((Path(i18n.__file__).parent / "locales").glob("*.py"))
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Dict):
                    keys = [key.value for key in node.keys if isinstance(key, ast.Constant)]
                    self.assertEqual(len(keys), len(set(keys)), str(path))

    def test_literal_calls_are_covered_in_every_catalogue(self):
        def literals(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield node.value
            elif isinstance(node, ast.IfExp):
                yield from literals(node.body)
                yield from literals(node.orelse)
        for path in Path(i18n.__file__).parent.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and node.args):
                    for source in literals(node.args[0]):
                        if source and not source.isdigit():
                            for language, messages in CATALOGUES.items():
                                self.assertIn(source, messages, (language, path.name, node.lineno))

    def test_preference_restart_and_unknown_fallback_for_every_language(self):
        script = '''
import sys
from PySide6.QtCore import QSettings
from sentinel import i18n
def settings(*_args): return QSettings(sys.argv[1], QSettings.Format.IniFormat)
settings.Status = QSettings.Status
i18n.QSettings = settings
if sys.argv[2] == 'save': i18n.save_language(sys.argv[3])
else:
    i18n.configure_language()
    assert i18n.current_language() == sys.argv[3]
    assert i18n.tr('Settings') == sys.argv[4]
    assert i18n.tr('An unknown future message') == 'An unknown future message'
'''
        environment = dict(os.environ)
        environment.pop("USAGELOOP_LANGUAGE", None)
        for language in LANGUAGE_CODES:
            expected = CATALOGUES.get(language, {}).get("Settings", "Settings")
            for action in ("save", "restart"):
                result = subprocess.run([sys.executable, "-c", script,
                    str(Path(self.directory.name) / (language + ".ini")), action, language, expected],
                    env=environment, capture_output=True, text=True, encoding="utf-8", timeout=20)
                self.assertEqual(0, result.returncode, (language, action, result.stderr))

    def test_system_locales_resolve_to_supported_languages(self):
        for name, expected in (("es_MX", "es"), ("pt_BR", "pt_BR"), ("pt_PT", "pt_BR"),
                ("fr_CA", "fr"), ("ja_JP", "ja"), ("zh_SG", "zh_CN"),
                ("zh_TW", "en"), ("de_DE", "en")):
            with self.subTest(locale=name), patch.dict(os.environ, {"USAGELOOP_LANGUAGE": "system"}), \
                    patch("sentinel.i18n.QLocale.system", return_value=QLocale(name)):
                self.assertEqual(expected, i18n.resolved_language())

    def test_time_controls_and_dialog_buttons_use_the_selected_language(self):
        moment = datetime(2030, 1, 15, 0, 5)
        for language in LANGUAGE_CODES:
            with self.subTest(language=language), patch.object(i18n, "_language", language):
                editor = TimeEntry(QTime(0, 5))
                self.addCleanup(editor.close)
                self.assertEqual("h:mm AP" if language == "en" else "HH:mm", editor.displayFormat())
                if language != "en":
                    self.assertEqual("00:05", editor.text())
                    self.assertEqual("00:05", i18n.format_datetime(moment, "%I:%M %p"))
                    translator = i18n.DialogTranslator()
                    self.assertEqual(DIALOG_BUTTONS[language][2], translator.translate("QPlatformTheme", "Cancel"))
                    self.assertEqual(DIALOG_BUTTONS[language][0], translator.translate("QMessageBox", "Yes"))

    def test_language_changes_do_not_write_scheduler_state_or_send_requests(self):
        for language in LANGUAGE_CODES:
            with self.subTest(language=language), patch.object(i18n, "_language", "en"):
                app, window = build_window(Path(self.directory.name) / language)
                window.clock_timer.stop()
                window.automation_timer.stop()
                try:
                    before = window.controller.store.path.read_bytes()
                    with patch.object(window.update_panel.updater, "check") as check:
                        window.language_choice.setCurrentIndex(window.language_choice.findData(language))
                        self.assertEqual(language, i18n.language_preference())
                        self.assertEqual("en", i18n.current_language())
                        self.assertEqual(before, window.controller.store.path.read_bytes())
                        self.assertFalse(window.controller.settings.automation_enabled)
                        self.assertEqual({}, window.active_operations)
                        check.assert_not_called()
                finally:
                    window.close()

    def test_status_translation_preserves_decisions_and_uncertain_start(self):
        from sentinel.app_state import AppSettings
        settings = AppSettings(automation_enabled=True, compatible_runtime_identities={"codex": "test"})
        base = replace(ProviderViewState.waiting("codex", "Codex", installed=True,
            runtime_identity="test"), weekly_used_percent=20, usage_checked_at=100)
        states = [base, replace(base, status="Ready", quota_state="ANCHORED",
            reset_at=5000, last_verified_at=100, last_action="ANCHOR_NOT_VERIFIED"),
            replace(base, outcome_category="GUARDED", last_action="ANCHOR_NOT_VERIFIED"),
            replace(base, weekly_used_percent=99), replace(base, status="Needs attention")]
        raw_guard = "Codex may have received the request, but observations did not prove anchoring; no retry is allowed."
        states.append(replace(base, outcome_category="GUARDED", last_action="ANCHOR_NOT_VERIFIED", detail=raw_guard))
        for state in states:
            with patch.object(i18n, "_language", "en"):
                original = operational_presentation(settings, state, now=200)
            for language in LANGUAGE_CODES:
                with self.subTest(language=language, state=state.last_action), patch.object(i18n, "_language", language):
                    translated = operational_presentation(settings, state, now=200)
                    for field in ("kind", "tone", "manual_start_visible"):
                        self.assertEqual(getattr(original, field), getattr(translated, field))
                    if state.detail == raw_guard and language != "en":
                        self.assertEqual(CATALOGUES[language][raw_guard], translated.card_detail)

    def test_header_settings_about_and_dialog_controls_fit_minimum_and_normal_sizes(self):
        for language in LANGUAGE_CODES:
            for host in ("Windows", "Linux"):
                with self.subTest(language=language, host=host), patch.object(i18n, "_language", language):
                    _, window = build_window(Path(self.directory.name) / (language + host), platform_name=host)
                    window.clock_timer.stop()
                    window.automation_timer.stop()
                    window.show()
                    try:
                        for size in ((720, 560), (1366, 768)):
                            window.resize(*size)
                            for page, mode in ((0, None), (1, "daily"), (1, "weekly"), (2, None)):
                                if mode is not None:
                                    window.schedule_mode.setCurrentIndex(window.schedule_mode.findData(mode))
                                    window.weekly_custom_days.toggle.setChecked(mode == "weekly")
                                window.show_page(page)
                                for _ in range(4): self.app.processEvents()
                                viewport = window.pages.widget(page).viewport()
                                if QFontDatabase.families():
                                    self.assertEqual(size, (window.width(), window.height()))
                                    if mode is not None:
                                        label = window.schedule_mode_title
                                        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), (language, mode, size))
                                        combo = window.schedule_mode
                                        option = QStyleOptionComboBox()
                                        combo.initStyleOption(option)
                                        contents = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, combo)
                                        self.assertGreaterEqual(contents.width(), combo.fontMetrics().horizontalAdvance(combo.currentText()), (language, mode, size))
                                    if mode == "weekly":
                                        for row in window.weekly_day_rows:
                                            label = row.findChild(QLabel, "weeklyDayLabel")
                                            self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()), (language, label.text(), size))
                                for button in window.nav_buttons:
                                    pos = button.mapTo(window, button.rect().topLeft())
                                    self.assertGreaterEqual(pos.x(), 0)
                                    self.assertLessEqual(pos.x() + button.width(), window.width())
                                for control in window.pages.widget(page).findChildren(QPushButton):
                                    if control.isVisible():
                                        pos = control.mapTo(viewport, control.rect().topLeft())
                                        self.assertGreaterEqual(pos.x(), 0, (language, control.text(), size))
                                        self.assertLessEqual(pos.x() + control.width(), viewport.width(), (language, control.text(), size))
                                        if QFontDatabase.families():
                                            option = QStyleOptionButton()
                                            option.initFrom(control)
                                            option.text = control.text()
                                            contents = control.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, control)
                                            self.assertGreaterEqual(contents.width() + 1, control.fontMetrics().horizontalAdvance(control.text()),
                                                (language, control.text(), size))
                        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel) as question:
                            self.assertFalse(window.update_panel._confirm_install("1.4.1"))
                            self.assertIn("1.4.1", question.call_args.args[2])
                    finally:
                        window.close()

    def test_japanese_font_can_render_safety_terms(self):
        families = QFontDatabase.families(QFontDatabase.WritingSystem.Japanese)
        if not families:
            self.skipTest("A Japanese-capable system font is required for glyph verification")
        from PySide6.QtGui import QFont
        font = QFont("Yu Gothic UI")
        # Measure shaped glyphs, including Qt's real fallback fonts. The
        # primary family alone need not contain every Japanese character.
        layout = QTextLayout("日本語読み取り専用未確認再試行上限週間開始", font)
        layout.beginLayout()
        layout.createLine()
        layout.endLayout()
        runs = layout.glyphRuns()
        self.assertTrue(runs)
        self.assertTrue(all(index != 0 for run in runs for index in run.glyphIndexes()))
