"""Window Sentinel desktop entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .app_controller import ApplicationController
from .app_state import AppStateStore
from .desktop import DesktopShell, MainWindow
from .product import PRODUCT
from .providers import ClaudeProvider, CodexProvider
from .startup import StartupManager


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=Path(PRODUCT.executable_name).stem)
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start in the system tray without opening the main window.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(PRODUCT.display_name)
    application.setApplicationVersion(PRODUCT.version)
    application.setOrganizationName(PRODUCT.publisher)
    application.setStyle("Fusion")

    providers = [CodexProvider(), ClaudeProvider()]
    controller = ApplicationController(providers, AppStateStore())
    controller.start()
    startup = StartupManager(str(Path(sys.executable).resolve()))
    window = MainWindow(
        controller,
        {provider.provider_id: provider for provider in providers},
        startup,
    )
    shell = DesktopShell(window)
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    application.setQuitOnLastWindowClosed(not tray_available)
    application._sentinel_window = window  # type: ignore[attr-defined]
    application._sentinel_shell = shell  # type: ignore[attr-defined]
    if not args.background or not tray_available:
        window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
