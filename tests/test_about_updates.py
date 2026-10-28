"""Updates stay discoverable without starting network or provider work."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from sentinel import i18n
from sentinel.product import PRODUCT
from sentinel.update_ui import UpdatePanel
from sentinel.updates import ReleaseAsset, ReleaseInfo, UpdateCheckResult, VerifiedInstaller
from test_layout import SUPPORTED_SIZES, build_window


class AboutUpdatesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app, self.window = build_window(Path(temporary.name))
        self.window.clock_timer.stop()
        self.window.automation_timer.stop()
        self.addCleanup(self.window.close)

    def settle(self):
        for _ in range(4):
            self.app.processEvents()

    def assert_visible_in_page(self, widget):
        viewport = self.window.pages.widget(2).viewport()
        position = widget.mapTo(viewport, widget.rect().topLeft())
        self.assertGreaterEqual(position.x(), 0)
        self.assertGreaterEqual(position.y(), 0)
        self.assertLessEqual(position.x() + widget.width(), viewport.width())
        self.assertLessEqual(position.y() + widget.height(), viewport.height())

    def test_about_owns_the_only_panel_and_navigation_never_checks(self):
        panel = self.window.update_panel
        with patch.object(panel.updater, "check") as check:
            for index in (2, 1, 0, 2):
                self.window.show_page(index)
            check.assert_not_called()
        self.assertEqual([panel], self.window.findChildren(UpdatePanel))
        self.assertTrue(self.window.pages.widget(2).isAncestorOf(panel))
        self.assertFalse(self.window.pages.widget(1).isAncestorOf(panel))

    def test_version_status_and_actions_fit_both_languages_and_host_surfaces(self):
        self.window.close()
        for language in ("en", "zh_CN"):
            for host in ("Windows", "Linux"):
                with self.subTest(language=language, host=host), \
                        patch.object(i18n, "_language", language), \
                        tempfile.TemporaryDirectory() as directory:
                    self.app, self.window = build_window(Path(directory), platform_name=host)
                    self.window.clock_timer.stop()
                    self.window.automation_timer.stop()
                    try:
                        self.window.show_page(2)
                        self.window.show()
                        panel = self.window.update_panel
                        installed = next(label for label in panel.findChildren(QLabel)
                                         if PRODUCT.version in label.text())
                        release = ReleaseInfo(
                            "1.4.1", (i18n.tr("Updates are easier to find in About."),) * 10,
                            PRODUCT.releases_url,
                            ReleaseAsset("synthetic", "https://example.invalid/unused"),
                            ReleaseAsset("synthetic.sha256", "https://example.invalid/unused"),
                        )
                        for size in (*SUPPORTED_SIZES, (720, 560)):
                            self.window.resize(*size)
                            for state in ("idle", "available", "downloaded", "error"):
                                with self.subTest(size=size, state=state):
                                    panel.reset()
                                    if state == "available":
                                        panel._operation_completed("check", UpdateCheckResult("update_available", release))
                                    elif state == "downloaded":
                                        panel._set_state("downloaded", i18n.tr("Installer downloaded and its SHA-256 checksum matched the release."))
                                        panel.action_button.setText(i18n.tr("Install update" if host == "Windows" else "Install and restart"))
                                    elif state == "error":
                                        panel._operation_failed("check", i18n.tr("GitHub returned an unsupported release result."))
                                    self.settle()
                                    self.window.pages.widget(2).verticalScrollBar().setValue(0)
                                    self.settle()
                                    for widget in (installed, panel.state_label, panel.action_button):
                                        self.assert_visible_in_page(widget)
                    finally:
                        self.window.close()

    def test_about_return_reveals_updates_and_keyboard_reaches_primary_action(self):
        self.window.resize(1024, 768)
        self.window.show_page(2)
        self.window.show()
        self.settle()
        page = self.window.pages.widget(2)
        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
        self.window.show_page(0)
        self.window.nav_buttons[2].setFocus()
        QTest.keyClick(self.window.nav_buttons[2], Qt.Key.Key_Space)
        self.settle()
        self.assertEqual(0, page.verticalScrollBar().value())
        unfocused = self.window.update_panel.action_button.grab().toImage()
        QTest.keyClick(self.window.nav_buttons[2], Qt.Key.Key_Tab)
        self.settle()
        self.assertIs(self.window.focusWidget(), self.window.update_panel.action_button)
        self.assertNotEqual(unfocused, self.window.update_panel.action_button.grab().toImage())
        with patch.object(self.window.update_panel, "start_check") as check:
            QTest.keyClick(self.window.update_panel.action_button, Qt.Key.Key_Space)
            check.assert_called_once_with()

    def test_model_support_shortcut_reveals_about_and_starts_one_manual_check(self):
        self.window.show_page(2)
        self.window.show()
        self.settle()
        page = self.window.pages.widget(2)
        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
        self.window.show_page(0)
        with patch.object(self.window.update_panel, "start_model_support_check") as check:
            self.window._check_updates_for_model_support()
            check.assert_called_once_with()
        self.settle()
        self.assertEqual(2, self.window.pages.currentIndex())
        self.assert_visible_in_page(self.window.update_panel.action_button)

    def test_update_result_survives_navigation(self):
        panel = self.window.update_panel
        panel._operation_completed("check", UpdateCheckResult("latest"))
        status = panel.state_label.text()
        self.window.show_page(1)
        self.window.show_page(2)
        self.assertEqual("latest", panel.state)
        self.assertEqual(status, panel.state_label.text())

    def test_available_update_buttons_are_adjacent_in_both_tab_directions(self):
        panel = self.window.update_panel
        panel._operation_completed("check", UpdateCheckResult("update_available", ReleaseInfo(
            "1.4.1", (), PRODUCT.releases_url,
            ReleaseAsset("synthetic", "https://example.invalid/unused"),
            ReleaseAsset("synthetic.sha256", "https://example.invalid/unused"),
        )))
        self.window.show_page(2)
        self.window.show()
        self.settle()
        panel.action_button.setFocus()
        QTest.keyClick(panel.action_button, Qt.Key.Key_Tab)
        self.assertIs(self.window.focusWidget(), panel.dismiss_button)
        QTest.keyClick(panel.dismiss_button, Qt.Key.Key_Backtab)
        self.assertIs(self.window.focusWidget(), panel.action_button)

    def test_dashboard_shortcut_preserves_pending_and_prepared_updates(self):
        panel = self.window.update_panel
        panel.release = ReleaseInfo("1.4.1", (), PRODUCT.releases_url,
            ReleaseAsset("synthetic", "https://example.invalid/unused"),
            ReleaseAsset("synthetic.sha256", "https://example.invalid/unused"))
        panel.installer = VerifiedInstaller(Path("synthetic-installer.exe"), "0" * 64)
        for state in ("checking", "downloading", "available", "downloaded", "manual", "launching"):
            with self.subTest(state=state):
                panel.state = state
                before = (panel.release, panel.installer, panel.staged, panel.action_button.text())
                self.window.show_page(0)
                with patch.object(panel.thread_pool, "start") as start:
                    self.window._check_updates_for_model_support()
                    start.assert_not_called()
                self.assertEqual(2, self.window.pages.currentIndex())
                self.assertEqual(state, panel.state)
                self.assertEqual(before, (panel.release, panel.installer, panel.staged, panel.action_button.text()))
