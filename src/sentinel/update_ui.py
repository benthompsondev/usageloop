"""Qt view for the user-initiated, checksum-gated update flow."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .product import PRODUCT
from .updates import (
    GitHubReleaseUpdater,
    ReleaseInfo,
    UpdateCheckResult,
    UpdateError,
    VerifiedInstaller,
)


class UpdateSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class UpdateWorker(QRunnable):
    def __init__(self, action: str, operation: Callable[[], object]):
        super().__init__()
        self.action = action
        self.operation = operation
        self.signals = UpdateSignals()

    def run(self) -> None:
        try:
            result = self.operation()
        except UpdateError as exc:
            self.signals.failed.emit(self.action, str(exc))
        except Exception:
            self.signals.failed.emit(
                self.action, "The update step stopped unexpectedly. Your current app was not changed."
            )
        else:
            self.signals.completed.emit(self.action, result)


class UpdatePanel(QFrame):
    installer_launched = Signal()

    def __init__(
        self,
        updater: GitHubReleaseUpdater | None = None,
        *,
        confirm_install: Callable[[str], bool] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("surfaceCard")
        self.updater = updater or GitHubReleaseUpdater()
        self.confirm_install = confirm_install or self._confirm_install
        self.thread_pool = QThreadPool.globalInstance()
        self.release: ReleaseInfo | None = None
        self.installer: VerifiedInstaller | None = None
        self.state = "idle"
        self._checking_model_support = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)
        title = QLabel("Updates")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        copy = QLabel(
            "GitHub is checked only when you press the button. Update traffic is separate "
            "from Codex and cannot use subscription quota."
        )
        copy.setProperty("muted", True)
        copy.setWordWrap(True)
        layout.addWidget(copy)

        installed = QLabel(f"Installed version  {PRODUCT.version}")
        installed.setObjectName("secondaryMetric")
        layout.addWidget(installed)
        self.state_label = QLabel("No update check has run.")
        self.state_label.setObjectName("updateState")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)
        self.notes_label = QLabel()
        self.notes_label.setProperty("muted", True)
        self.notes_label.setWordWrap(True)
        self.notes_label.setVisible(False)
        layout.addWidget(self.notes_label)

        actions = QHBoxLayout()
        self.action_button = QPushButton("Check for updates")
        self.action_button.setObjectName("primaryButton")
        self.action_button.clicked.connect(self._primary_action)
        self.dismiss_button = QPushButton("Not now")
        self.dismiss_button.clicked.connect(self.reset)
        self.dismiss_button.setVisible(False)
        actions.addWidget(self.action_button)
        actions.addWidget(self.dismiss_button)
        actions.addStretch()
        layout.addLayout(actions)

    def _primary_action(self) -> None:
        if self.state in {"idle", "latest", "error"}:
            self.start_check()
        elif self.state == "available":
            self.start_download()
        elif self.state == "downloaded":
            self.start_install()

    def start_check(self) -> None:
        self._set_busy("checking", "Checking GitHub…")
        worker = UpdateWorker("check", self.updater.check)
        worker.signals.completed.connect(self._operation_completed)
        worker.signals.failed.connect(self._operation_failed)
        self.thread_pool.start(worker)

    def start_model_support_check(self) -> None:
        self._checking_model_support = True
        self.start_check()

    def start_download(self) -> None:
        if self.release is None:
            return
        self._set_busy("downloading", "Downloading and verifying the Windows installer…")
        worker = UpdateWorker("download", lambda: self.updater.download(self.release))
        worker.signals.completed.connect(self._operation_completed)
        worker.signals.failed.connect(self._operation_failed)
        self.thread_pool.start(worker)

    def start_install(self) -> None:
        if self.installer is None or self.release is None:
            return
        try:
            confirmed = self.confirm_install(self.release.version)
        except Exception:
            self._operation_failed(
                "install",
                "The install confirmation could not be opened. Try again.",
            )
            return
        if not confirmed:
            return
        try:
            self.updater.launch_installer(self.installer)
        except UpdateError as exc:
            self._operation_failed("install", str(exc))
            return
        except Exception:
            self._operation_failed(
                "install",
                "The verified installer could not be started. Your current app is still running.",
            )
            return
        self._set_state(
            "launching",
            "Installer started. This app will close so Windows can finish the update.",
            status="success",
        )
        self.action_button.setEnabled(False)
        self.installer_launched.emit()

    def _operation_completed(self, action: str, result: object) -> None:
        if action == "check":
            if not isinstance(result, UpdateCheckResult):
                self._operation_failed(action, "GitHub returned an unsupported release result.")
                return
            if result.status == "no_release":
                self.release = None
                self._set_state(
                    "no_release",
                    "No public release is available yet. Your installed app was not changed.",
                )
                self.action_button.setText("Check again")
                return
            if result.status == "latest":
                self.release = None
                message = f"You are on the latest version ({PRODUCT.version})."
                if self._checking_model_support:
                    message += (
                        " Support for the changed Codex model lineup may require "
                        "a future UsageLoop update."
                    )
                self._set_state(
                    "latest",
                    message,
                    status="success",
                )
                self.action_button.setText("Check again")
                return
            if result.status != "update_available" or not isinstance(result.release, ReleaseInfo):
                self._operation_failed(action, "GitHub returned an unsupported release result.")
                return
            release = result.release
            self.release = release
            self._set_state(
                "available", f"Version {release.version} is available.", status="success"
            )
            if release.notes:
                self.notes_label.setText(
                    "What changed\n" + "\n".join(f"• {note}" for note in release.notes)
                )
                self.notes_label.setVisible(True)
            self.action_button.setText("Download installer")
            self.dismiss_button.setVisible(True)
            return
        if action == "download" and isinstance(result, VerifiedInstaller):
            self.installer = result
            self._set_state(
                "downloaded",
                "Installer downloaded and its SHA-256 checksum matched the release.",
                status="success",
            )
            self.action_button.setText("Install update")
            self.dismiss_button.setVisible(True)
            return
        self._operation_failed(action, "The update result was not understood.")

    def _operation_failed(self, _action: str, message: str) -> None:
        self._set_state("error", message, status="error")
        self.action_button.setText("Try again")
        self.dismiss_button.setVisible(False)

    def _set_busy(self, state: str, message: str) -> None:
        self._set_state(state, message)
        self.action_button.setText("Working…")
        self.action_button.setEnabled(False)
        self.dismiss_button.setVisible(False)

    def _set_state(self, state: str, message: str, *, status: str = "") -> None:
        self.state = state
        self.state_label.setText(message)
        self.state_label.setProperty("status", status)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.action_button.setEnabled(True)

    def reset(self) -> None:
        self._checking_model_support = False
        self.release = None
        self.installer = None
        self.notes_label.clear()
        self.notes_label.setVisible(False)
        self.dismiss_button.setVisible(False)
        self._set_state("idle", "No update check has run.")
        self.action_button.setText("Check for updates")

    def _confirm_install(self, version: str) -> bool:
        answer = QMessageBox.question(
            self,
            f"Install {PRODUCT.display_name} update?",
            f"The verified installer for version {version} is ready. {PRODUCT.display_name} will "
            "close and the normal per-user setup window will open.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes
