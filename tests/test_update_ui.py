import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from sentinel.update_ui import UpdatePanel
from sentinel.product import PRODUCT
from sentinel.updates import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheckResult,
    UpdateError,
    VerifiedInstaller,
)


class FakeUpdater:
    def check(self):
        raise AssertionError("The panel must not check automatically.")


class LaunchUpdater(FakeUpdater):
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.launches: list[VerifiedInstaller] = []

    def launch_installer(self, installer: VerifiedInstaller) -> None:
        self.launches.append(installer)
        if self.error is not None:
            raise self.error


class UpdatePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, *, platform_name="Windows"):
        panel = UpdatePanel(
            FakeUpdater(),
            confirm_install=lambda _version: False,
            platform_name=platform_name,
            managed_install=True,
        )
        self.addCleanup(panel.close)
        return panel

    def make_install_panel(self, updater, confirm_install):
        panel = UpdatePanel(
            updater,
            confirm_install=confirm_install,
            platform_name="Windows",
            managed_install=True,
        )
        self.addCleanup(panel.close)
        panel.release = ReleaseInfo(
            "0.9.1",
            ("Fix the Windows installer launch.",),
            "https://github.com/benthompsondev/usageloop/releases/tag/v0.9.1",
            ReleaseAsset(PRODUCT.installer_filename, "https://github.com/example"),
            ReleaseAsset(PRODUCT.checksum_filename, "https://github.com/example"),
        )
        panel.installer = VerifiedInstaller(
            Path(PRODUCT.installer_filename), "0" * 64
        )
        panel._set_state("downloaded", "Installer verified.", status="success")
        panel.action_button.setText("Install update")
        return panel

    def test_default_confirmation_uses_supported_yes_and_cancel_buttons(self) -> None:
        panel = UpdatePanel(FakeUpdater(), platform_name="Windows", managed_install=True)
        self.addCleanup(panel.close)

        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as question:
            self.assertTrue(panel._confirm_install("0.9.1"))

        buttons = question.call_args.args[3]
        default = question.call_args.args[4]
        self.assertEqual(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            buttons,
        )
        self.assertEqual(QMessageBox.StandardButton.Yes, default)

    def test_clicking_install_accepts_confirmation_launches_once_then_emits(self) -> None:
        updater = LaunchUpdater()
        confirmations = []
        panel = self.make_install_panel(
            updater, lambda version: confirmations.append(version) or True
        )
        emissions = []
        panel.installer_launched.connect(lambda: emissions.append("launched"))

        panel.action_button.click()

        self.assertEqual(["0.9.1"], confirmations)
        self.assertEqual([panel.installer], updater.launches)
        self.assertEqual(["launched"], emissions)
        self.assertEqual("launching", panel.state)

    def test_cancelling_install_launches_nothing(self) -> None:
        updater = LaunchUpdater()
        panel = self.make_install_panel(updater, lambda _version: False)
        emissions = []
        panel.installer_launched.connect(lambda: emissions.append("launched"))

        panel.action_button.click()

        self.assertEqual([], updater.launches)
        self.assertEqual([], emissions)
        self.assertEqual("downloaded", panel.state)

    def test_confirmation_exception_becomes_visible_error(self) -> None:
        updater = LaunchUpdater()

        def broken_confirmation(_version):
            raise RuntimeError("confirmation widget broke")

        panel = self.make_install_panel(updater, broken_confirmation)

        panel.action_button.click()

        self.assertEqual([], updater.launches)
        self.assertEqual("error", panel.state)
        self.assertIn("confirmation", panel.state_label.text().lower())

    def test_launcher_failure_becomes_visible_error_and_emits_nothing(self) -> None:
        updater = LaunchUpdater(error=UpdateError("Windows refused the installer."))
        panel = self.make_install_panel(updater, lambda _version: True)
        emissions = []
        panel.installer_launched.connect(lambda: emissions.append("launched"))

        panel.action_button.click()

        self.assertEqual(1, len(updater.launches))
        self.assertEqual([], emissions)
        self.assertEqual("error", panel.state)
        self.assertIn("Windows refused", panel.state_label.text())

    def test_unexpected_launcher_exception_becomes_visible_error(self) -> None:
        updater = LaunchUpdater(error=RuntimeError("launcher adapter broke"))
        panel = self.make_install_panel(updater, lambda _version: True)
        emissions = []
        panel.installer_launched.connect(lambda: emissions.append("launched"))

        panel.action_button.click()

        self.assertEqual([], emissions)
        self.assertEqual("error", panel.state)
        self.assertIn("could not be started", panel.state_label.text().lower())

    def test_latest_available_and_failure_states_have_clear_actions(self) -> None:
        panel = self.make_panel()
        panel._operation_completed("check", UpdateCheckResult("latest"))
        self.assertEqual("latest", panel.state)
        self.assertEqual("Check again", panel.action_button.text())

        panel._operation_completed("check", UpdateCheckResult("no_release"))
        self.assertEqual("no_release", panel.state)
        self.assertIn("No public release", panel.state_label.text())

        release = ReleaseInfo(
            "0.6.0",
            ("Cleaner dashboard", "Safer update checks"),
            "https://github.com/benthompsondev/usageloop/releases/tag/v0.6.0",
            ReleaseAsset(PRODUCT.installer_filename, "https://github.com/example"),
            ReleaseAsset(PRODUCT.checksum_filename, "https://github.com/example"),
        )
        panel._operation_completed("check", UpdateCheckResult("update_available", release))
        self.assertEqual("available", panel.state)
        self.assertEqual("Download installer", panel.action_button.text())
        self.assertIn("Cleaner dashboard", panel.notes_label.text())

        panel._operation_failed("check", "GitHub could not be reached.")
        self.assertEqual("error", panel.state)
        self.assertEqual("Try again", panel.action_button.text())
        self.assertIn("GitHub could not be reached", panel.state_label.text())

    def test_model_support_check_does_not_promise_a_future_update(self) -> None:
        panel = self.make_panel()
        with patch.object(panel, "start_check") as start_check:
            panel.start_model_support_check()
        start_check.assert_called_once_with()
        panel._operation_completed("check", UpdateCheckResult("latest"))

        self.assertIn("may require a future UsageLoop update", panel.state_label.text())
        self.assertNotIn("will require", panel.state_label.text())


if __name__ == "__main__":
    unittest.main()
