"""Layout regression tests for the defect where the header clipped its right edge.

The original bug: the header tagline used `QSizePolicy.Ignored`, which is a
growing policy rather than a shrinking one. The brand block expanded with the
window and pushed the navigation and trust chip past the right edge, where a
maximized Windows window hides them under its invisible resize border.

These assert containment directly rather than checking a minimum width, because
a wider minimum window would have hidden the bug instead of fixing it.
"""

import unittest

from PySide6.QtCore import QSize
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import MainWindow
from sentinel.ui_components import ElidingLabel


#: The desktop sizes the product claims to support.
SUPPORTED_SIZES = (
    (1024, 768),
    (1280, 720),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
)


class StubProvider:
    def __init__(self, state):
        self.provider_id = state.provider_id
        self.state = state

    def detect(self):
        return self.state


class StubStartup:
    def is_enabled(self):
        return False

    def set_enabled(self, _enabled):
        return None


class StubUpdater:
    def check(self):
        raise AssertionError("layout tests must not contact GitHub")


def build_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    states = [ProviderViewState.waiting("codex", "Codex", installed=True)]
    providers = [StubProvider(state) for state in states]
    controller = ApplicationController(providers, AppStateStore(tmp_path / "state.json"))
    controller.start()
    window = MainWindow(
        controller,
        {provider.provider_id: provider for provider in providers},
        StubStartup(),
        updater=StubUpdater(),
        confirm_enable=lambda: False,
        confirm_bootstrap=lambda: False,
        confirm_install=lambda _v: False,
    )
    return app, window


class HeaderContainmentTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.app, self.window = build_window(Path(self._dir.name))
        self.addCleanup(self.window.close)
        self.header = self.window.findChild(QFrame, "appHeader")

    def settle(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        # Hiding or showing the chip invalidates the layout, so let it settle
        # fully rather than reading a half-applied geometry.
        for _ in range(3):
            self.window.layout().activate()
            for _ in range(4):
                self.app.processEvents()

    def overflowing_children(self):
        """Every visible header child that starts before 0 or ends past the edge."""
        header_width = self.header.width()
        offenders = []
        for child in self.header.findChildren(QLabel) + self.header.findChildren(QPushButton):
            if not child.isVisible():
                continue
            left = child.mapTo(self.header, child.rect().topLeft()).x()
            right = left + child.width()
            if left < 0 or right > header_width:
                offenders.append((child.objectName() or child.text(), left, right, header_width))
        return offenders

    def test_header_never_overflows_at_supported_sizes(self):
        for width, height in SUPPORTED_SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                self.assertEqual([], self.overflowing_children())

    def test_header_never_overflows_when_maximized(self):
        self.window.showMaximized()
        for _ in range(6):
            self.app.processEvents()
        self.assertEqual([], self.overflowing_children())

    def test_header_survives_a_window_far_narrower_than_supported(self):
        self.settle(560, 500)
        self.assertEqual([], self.overflowing_children())

    def test_navigation_stays_reachable_at_every_size(self):
        for width, height in (*SUPPORTED_SIZES, (640, 520)):
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                for button in self.window.nav_buttons:
                    self.assertTrue(button.isVisible(), button.text())
                    self.assertGreater(button.width(), 0)

    def test_trust_chip_is_shown_whole_or_not_at_all(self):
        """A clipped chip is the reported defect; hiding it is the fallback."""
        for width, height in SUPPORTED_SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                self.assertTrue(self.window.trust_chip.isVisible())
                chip = self.window.trust_chip
                left = chip.mapTo(self.header, chip.rect().topLeft()).x()
                self.assertLessEqual(left + chip.width(), self.header.width())
                self.assertGreaterEqual(chip.width(), chip.sizeHint().width())

    def test_controls_keep_their_full_width_however_wide_the_brand_gets(self):
        """The brand may take the slack; it must never take it from the controls.

        The original defect was the brand block winning space at the controls'
        expense, so this asserts the controls always get at least what they ask
        for, which is the invariant that was actually violated.
        """
        for width, height in SUPPORTED_SIZES:
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                controls = self.window.header_controls
                self.assertGreaterEqual(controls.width(), controls.sizeHint().width())

    def test_header_parts_fit_inside_the_narrowest_supported_window(self):
        """The wordmark and controls do not shrink, so together they must fit.

        Everything else in the header elides or hides. If these two fixed pieces
        plus the side margins ever exceed the narrowest supported width, the
        header has to clip something no matter how the layout is written.
        """
        narrowest = min(width for width, _height in SUPPORTED_SIZES)
        fixed = (
            self.window.brand_block.minimumSizeHint().width()
            + self.window.header_controls.sizeHint().width()
            + self.window.HEADER_SIDE_MARGIN * 2
        )
        self.assertLess(fixed, narrowest)

    def test_header_minimum_does_not_force_a_wide_window(self):
        self.assertLessEqual(self.header.minimumSizeHint().width(), 1024)
        self.assertLessEqual(self.window.minimumSizeHint().width(), 1024)

    def test_pages_never_need_a_horizontal_scrollbar(self):
        from PySide6.QtWidgets import QScrollArea

        for width, height in SUPPORTED_SIZES:
            for index in range(self.window.pages.count()):
                self.window.show_page(index)
                self.settle(width, height)
                page = self.window.pages.widget(index)
                if isinstance(page, QScrollArea):
                    with self.subTest(size=f"{width}x{height}", page=index):
                        self.assertFalse(page.horizontalScrollBar().isVisible())


class ElidingLabelTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def test_minimum_width_is_zero_so_it_cannot_drive_a_layout(self):
        label = ElidingLabel("Keep your Codex reset clock running.")
        self.assertEqual(0, label.minimumSizeHint().width())

    def test_size_hint_reports_the_full_text(self):
        text = "Keep your Codex reset clock running."
        label = ElidingLabel(text)
        expected = QFontMetrics(label.font()).horizontalAdvance(text)
        self.assertEqual(expected, label.sizeHint().width())

    def test_text_is_elided_when_the_label_is_too_narrow(self):
        label = ElidingLabel("Keep your Codex reset clock running.")
        self.addCleanup(label.close)
        label.show()
        label.setFixedWidth(40)
        self.app.processEvents()
        shown = QLabel.text(label)
        self.assertNotEqual(label.full_text(), shown)
        self.assertLess(len(shown), len(label.full_text()))

    def test_full_text_is_preserved_across_elision(self):
        text = "Keep your Codex reset clock running."
        label = ElidingLabel(text)
        self.addCleanup(label.close)
        label.show()
        label.setFixedWidth(30)
        self.app.processEvents()
        label.setFixedWidth(600)
        self.app.processEvents()
        self.assertEqual(text, label.full_text())
        self.assertEqual(text, QLabel.text(label))

    def test_setting_new_text_replaces_the_stored_original(self):
        label = ElidingLabel("first")
        label.setText("second")
        self.assertEqual("second", label.full_text())




class LargerUiFontTests(unittest.TestCase):
    """Windows text scaling makes header controls much wider than the default.

    A guessed brand floor stopped protecting the trust chip once the navigation
    grew, so the chip stayed visible while there was no room for it. This pins
    the behaviour at an enlarged font instead of assuming the default one.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.app = QApplication.instance() or QApplication([])
        self._original_font = self.app.font()
        larger = QFont(self._original_font)
        larger.setPointSizeF(max(1.0, self._original_font.pointSizeF()) * 1.6)
        self.app.setFont(larger)
        self.addCleanup(self.app.setFont, self._original_font)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        _app, self.window = build_window(Path(self._dir.name))
        self.addCleanup(self.window.close)
        self.header = self.window.findChild(QFrame, "appHeader")

    def settle(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        for _ in range(3):
            self.window.layout().activate()
            for _ in range(4):
                self.app.processEvents()

    def test_nothing_in_the_header_clips_with_a_larger_font(self):
        for width, height in (*SUPPORTED_SIZES, (720, 560)):
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                header_width = self.header.width()
                for child in self.header.findChildren(QLabel) + self.header.findChildren(QPushButton):
                    if not child.isVisible():
                        continue
                    left = child.mapTo(self.header, child.rect().topLeft()).x()
                    self.assertGreaterEqual(left, 0, child.objectName() or child.text())
                    self.assertLessEqual(
                        left + child.width(),
                        header_width,
                        child.objectName() or child.text(),
                    )

    def test_chip_hides_rather_than_clipping_when_it_cannot_fit(self):
        self.settle(720, 560)
        controls = self.window.header_controls
        if self.window.trust_chip.isVisible():
            self.assertGreaterEqual(controls.width(), controls.sizeHint().width())
        # Whether it hid or fitted, navigation must survive either way.
        for button in self.window.nav_buttons:
            self.assertTrue(button.isVisible())




class SettingsSurfaceTests(unittest.TestCase):
    """Normal controls stay prominent while raw diagnostics stay collapsed."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.app, self.window = build_window(Path(self._dir.name))
        self.addCleanup(self.window.close)
        self.window.show_page(1)
        self.window.resize(1366, 768)
        self.window.show()
        for _ in range(4):
            self.app.processEvents()

    def test_consumer_settings_show_automation_schedule_startup_and_updates(self):
        self.assertEqual(
            "Keep my 5-hour windows ready", self.window.automation_title_label.text()
        )
        self.assertEqual("Continuous", self.window.schedule_mode.currentText())
        self.assertIsNotNone(self.window.startup_toggle)
        self.assertEqual("Check for updates", self.window.update_panel.action_button.text())

    def test_technical_details_start_collapsed(self):
        self.assertFalse(self.window.diagnostic_text.isVisible())

    def test_technical_details_still_contain_the_raw_summary(self):
        text = self.window.diagnostic_text.text()
        self.assertIn("Raw state:", text)
        self.assertIn("Automation", text)

    def test_copying_the_summary_puts_it_on_the_clipboard(self):
        self.window._copy_diagnostics()
        clipboard = QApplication.clipboard()
        self.assertEqual(self.window.diagnostic_text.text(), clipboard.text())


class FooterTests(unittest.TestCase):
    """A quiet status strip so a tall window ends deliberately."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.app, self.window = build_window(Path(self._dir.name))
        self.addCleanup(self.window.close)
        self.footer = self.window.footer_widget

    def settle(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        for _ in range(3):
            self.window.layout().activate()
            for _ in range(4):
                self.app.processEvents()

    def test_footer_is_visible_on_every_page(self):
        for index in range(self.window.pages.count()):
            self.window.show_page(index)
            self.settle(1366, 768)
            self.assertTrue(self.footer.isVisible())

    def test_footer_never_overflows(self):
        for width, height in (*SUPPORTED_SIZES, (720, 560)):
            with self.subTest(size=f"{width}x{height}"):
                self.settle(width, height)
                footer_width = self.footer.width()
                for child in self.footer.findChildren(QLabel):
                    if not child.isVisible():
                        continue
                    left = child.mapTo(self.footer, child.rect().topLeft()).x()
                    self.assertGreaterEqual(left, 0)
                    self.assertLessEqual(left + child.width(), footer_width)

    def test_footer_carries_the_version(self):
        self.settle(1366, 768)
        from sentinel.product import PRODUCT

        texts = [label.text() for label in self.footer.findChildren(QLabel)]
        self.assertTrue(any(PRODUCT.version in text for text in texts))

    def test_footer_does_not_eat_the_page(self):
        self.settle(1920, 1080)
        self.assertLess(self.footer.height(), 60)


if __name__ == "__main__":
    unittest.main()
