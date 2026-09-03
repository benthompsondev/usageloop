"""Disposable real Qt/controller/store preview. No provider or registry access.

May be frozen with PyInstaller to check packaged Pause/Resume and restart.
Only the synthetic state under the system temp directory is read or written.
"""

from pathlib import Path
import tempfile
import time

from PySide6.QtWidgets import QApplication

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore, ProviderViewState
from sentinel.desktop import DesktopShell, MainWindow
from sentinel.history import SafeHistory
from sentinel.product import PRODUCT
from sentinel.single_instance import ActivationChannel, InstanceCoordinator, SingleInstanceGuard


class PreviewProvider:
    provider_id = "codex"

    def __init__(self, root):
        self.history = SafeHistory(root / "preview-history.jsonl")
        self.state = ProviderViewState(
            "codex", "Codex", True, True, "Ready", "Synthetic preview only.",
            runtime_identity="preview", reset_at=int(time.time() + 7200),
            last_verified_at=time.time(), usage_checked_at=time.time(),
            used_percent=12, weekly_used_percent=20,
            weekly_reset_at=int(time.time() + 86400 * 3),
        )

    def detect(self):
        return self.state

    def run_action(self, *_args, **_kwargs):
        self.history.record_error("preview_would_start")
        raise AssertionError("Preview cannot contact a provider")

    probe = run_action
    sync_usage = run_action


class PreviewStartup:
    def is_enabled(self):
        return False

    def set_enabled(self, _enabled):
        pass


class PreviewUpdater:
    def check(self):
        raise AssertionError("Preview cannot contact GitHub")


def main():
    root = Path(tempfile.gettempdir()) / "UsageLoop-1.2.0-pause-preview"
    root.mkdir(exist_ok=True)
    store = AppStateStore(root / "preview-state.json")
    if not store.path.exists():
        store.save(AppSettings(
            automation_enabled=True, first_run_complete=True,
            schedule_mode="weekly", weekly_start_times=((4, 0),) * 5 + ((5, 0),) * 2,
            compatible_runtime_identities={"codex": "preview"},
        ), {})
    app = QApplication([])
    guard = SingleInstanceGuard("UsageLoop-disposable-pause-preview")
    channel = ActivationChannel("UsageLoop-disposable-pause-preview")
    coordinator = InstanceCoordinator(guard, channel)
    if not coordinator.claim(background=False):
        return 0
    app.setStyle("Fusion")
    provider = PreviewProvider(root)
    controller = ApplicationController([provider], store)
    controller.start()
    window = MainWindow(
        controller, {"codex": provider}, PreviewStartup(), updater=PreviewUpdater(),
        confirm_enable=lambda: True, confirm_bootstrap=lambda: False,
    )
    window.setWindowTitle(f"UsageLoop {PRODUCT.version} - Disposable preview")
    window.resize(720, 560)
    shell = DesktopShell(window)
    channel.activation_requested.connect(shell.restore_window)
    app.aboutToQuit.connect(channel.close)
    app.aboutToQuit.connect(guard.close)
    # The shortcut opens the real tray menu even if Windows hides its icon.
    from PySide6.QtGui import QCursor, QKeySequence, QShortcut
    shortcut = QShortcut(QKeySequence("Ctrl+Shift+T"), window)
    shortcut.activated.connect(lambda: shell.tray.contextMenu().popup(QCursor.pos()))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
