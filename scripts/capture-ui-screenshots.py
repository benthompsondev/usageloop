"""Render deterministic Codex-only screenshots with no provider traffic."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import tempfile
import time

from PySide6.QtWidgets import QApplication, QScrollArea

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import MainWindow
from sentinel.product import PRODUCT
from sentinel.updates import ReleaseAsset, ReleaseInfo, UpdateCheckResult


class PreviewProvider:
    provider_id = "codex"

    def __init__(self, state: ProviderViewState):
        self.state = state

    def detect(self) -> ProviderViewState:
        return self.state


class PreviewStartup:
    def is_enabled(self) -> bool:
        return False

    def set_enabled(self, _enabled: bool) -> None:
        return None


class PreviewUpdater:
    def check(self):
        raise AssertionError("Screenshot rendering must not contact GitHub.")


def save(window: MainWindow, target: Path, app: QApplication) -> None:
    window.show()
    for _ in range(3):
        app.processEvents()
    if not window.grab().save(str(target), "PNG"):
        raise RuntimeError(f"Could not save {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    now = time.time()
    codex = ProviderViewState(
        "codex", "Codex", True, True, "Ready", "Fixed reset verified.",
        "codex-runtime", "0.96.0", int(now + 3 * 3600 + 42 * 60), now - 60,
        "Anchor verified", 18, now - 60, 34, int(now + 4 * 24 * 3600),
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = PreviewProvider(codex)
        controller = ApplicationController(
            [provider], AppStateStore(Path(directory) / "state.json")
        )
        controller.start()
        controller.set_automation_enabled(True)
        window = MainWindow(
            controller, {"codex": provider}, PreviewStartup(), updater=PreviewUpdater(),
            confirm_enable=lambda: False, confirm_bootstrap=lambda: False,
            confirm_install=lambda _version: False,
        )
        for width, height in ((1024, 768), (1366, 768), (1920, 1080)):
            window.resize(width, height)
            save(window, args.output / f"dashboard-{width}x{height}.png", app)
        window.resize(1040, 720)
        save(window, args.output / "dashboard.png", app)

        window.show_page(1)
        save(window, args.output / "settings.png", app)
        release = ReleaseInfo(
            "0.9.0", ("Codex reliability fixes", "Clearer reset-clock status"),
            "https://github.com/example/usage-loop/releases/tag/v0.9.0",
            ReleaseAsset(PRODUCT.installer_filename, "https://github.com/example/installer"),
            ReleaseAsset(PRODUCT.checksum_filename, "https://github.com/example/checksum"),
        )
        window.update_panel._operation_completed(
            "check", UpdateCheckResult("update_available", release)
        )
        settings = window.pages.widget(1)
        if isinstance(settings, QScrollArea):
            settings.verticalScrollBar().setValue(settings.verticalScrollBar().maximum())
        save(window, args.output / "updates.png", app)

        window.show_page(2)
        save(window, args.output / "about.png", app)
        window.show_page(0)
        controller.set_automation_enabled(False)
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-automation-off.png", app)
        controller.update_provider_state(
            replace(codex, installed=False, status="Needs attention", reset_at=None)
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-codex-missing.png", app)
        controller.update_provider_state(
            replace(codex, status="Needs attention", detail="Capability probe failed safely.")
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-compatibility-failure.png", app)
        controller.update_provider_state(replace(codex, last_verified_at=now - 7 * 3600))
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-stale.png", app)
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
