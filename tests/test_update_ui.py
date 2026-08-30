import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication

from sentinel.update_ui import UpdatePanel
from sentinel.updates import ReleaseAsset, ReleaseInfo


class FakeUpdater:
    def check(self):
        raise AssertionError("The panel must not check automatically.")


class UpdatePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self):
        panel = UpdatePanel(FakeUpdater(), confirm_install=lambda _version: False)
        self.addCleanup(panel.close)
        return panel

    def test_latest_available_and_failure_states_have_clear_actions(self) -> None:
        panel = self.make_panel()
        panel._operation_completed("check", None)
        self.assertEqual("latest", panel.state)
        self.assertEqual("Check again", panel.action_button.text())

        release = ReleaseInfo(
            "0.6.0",
            ("Cleaner dashboard", "Safer update checks"),
            "https://github.com/benthompsondev/codex-window-sentinel/releases/tag/v0.6.0",
            ReleaseAsset("WindowSentinel-Setup.exe", "https://github.com/example"),
            ReleaseAsset("WindowSentinel-Setup.exe.sha256", "https://github.com/example"),
        )
        panel._operation_completed("check", release)
        self.assertEqual("available", panel.state)
        self.assertEqual("Download installer", panel.action_button.text())
        self.assertIn("Cleaner dashboard", panel.notes_label.text())

        panel._operation_failed("check", "GitHub could not be reached.")
        self.assertEqual("error", panel.state)
        self.assertEqual("Try again", panel.action_button.text())
        self.assertIn("GitHub could not be reached", panel.state_label.text())


if __name__ == "__main__":
    unittest.main()
