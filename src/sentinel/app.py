"""UsageLoop desktop entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from .app_controller import ApplicationController
from .app_state import AppStateStore
from .desktop import DesktopShell, MainWindow
from .product import PRODUCT
from .providers import CodexProvider
from .single_instance import ActivationChannel, InstanceCoordinator, SingleInstanceGuard
from .startup import StartupManager, reconcile_startup_preference


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=Path(PRODUCT.executable_name).stem)
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start in the system tray without opening the main window.",
    )
    parser.add_argument("--activation-smoke", help=argparse.SUPPRESS)
    parser.add_argument(
        "--activation-smoke-auto-hide", action="store_true", help=argparse.SUPPRESS
    )
    return parser


def _run_activation_smoke(
    application: QApplication, name: str, *, background: bool, auto_hide: bool
) -> int:
    """Packaged Windows acceptance path. It never constructs a provider."""
    guard = SingleInstanceGuard(f"Local\\UsageLoop-activation-smoke-{name}")
    channel = ActivationChannel(f"UsageLoop-activation-smoke-{name}")
    if not InstanceCoordinator(guard, channel).claim(background=background):
        return 0
    window = QWidget()
    window.setWindowTitle("UsageLoop activation smoke")
    window.resize(420, 180)

    def restore() -> None:
        window.showNormal()
        window.raise_()
        window.activateWindow()

    channel.activation_requested.connect(restore)
    application._sentinel_window = window  # type: ignore[attr-defined]
    application._sentinel_instance_guard = guard  # type: ignore[attr-defined]
    application._sentinel_activation_channel = channel  # type: ignore[attr-defined]
    if not background:
        window.show()
        if auto_hide:
            QTimer.singleShot(500, window.hide)
    return application.exec()


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    if args.activation_smoke:
        return _run_activation_smoke(
            application,
            args.activation_smoke,
            background=args.background,
            auto_hide=args.activation_smoke_auto_hide,
        )
    instance_guard = SingleInstanceGuard(PRODUCT.single_instance_name)
    activation_channel = ActivationChannel(PRODUCT.single_instance_name)
    coordinator = InstanceCoordinator(instance_guard, activation_channel)
    if not coordinator.claim(background=args.background):
        return 0
    application.setApplicationName(PRODUCT.display_name)
    application.setApplicationVersion(PRODUCT.version)
    application.setOrganizationName(PRODUCT.publisher)
    application.setStyle("Fusion")

    providers = [CodexProvider()]
    controller = ApplicationController(providers, AppStateStore())
    controller.start()
    startup = StartupManager(str(Path(sys.executable).resolve()))
    try:
        reconcile_startup_preference(controller.settings.start_with_windows, startup)
    except OSError:
        # Startup registration is optional. The Settings control remains the
        # visible recovery path if Windows temporarily denies registry access.
        pass
    window = MainWindow(
        controller,
        {provider.provider_id: provider for provider in providers},
        startup,
    )
    shell = DesktopShell(window)
    activation_channel.activation_requested.connect(shell.restore_window)
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    application.setQuitOnLastWindowClosed(not tray_available)
    application._sentinel_window = window  # type: ignore[attr-defined]
    application._sentinel_shell = shell  # type: ignore[attr-defined]
    application._sentinel_instance_guard = instance_guard  # type: ignore[attr-defined]
    application._sentinel_activation_channel = activation_channel  # type: ignore[attr-defined]
    if not args.background or not tray_available:
        window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
