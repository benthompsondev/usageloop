"""Render deterministic product-shell screenshots without provider traffic."""

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
from sentinel.updates import ReleaseAsset, ReleaseInfo


class PreviewProvider:
    def __init__(self, state: ProviderViewState):
        self.provider_id = state.provider_id
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
        "codex",
        "Codex",
        True,
        True,
        "Ready",
        "The last verified five-hour window is ready.",
        "codex-runtime",
        "0.96.0",
        int(now + 3 * 3600 + 42 * 60),
        now - 60,
        "Anchor verified",
        0,
        now - 60,
    )
    claude = ProviderViewState(
        "claude",
        "Claude Code",
        True,
        True,
        "Ready",
        "The last observed Claude five-hour window is ready.",
        "claude-runtime",
        "2.1.7",
        int(now + 4 * 3600 + 18 * 60),
        now - 90,
        "Status observed",
        0,
        now - 90,
        12,
        int(now + 4 * 24 * 3600),
    )
    with tempfile.TemporaryDirectory() as directory:
        providers = [PreviewProvider(codex), PreviewProvider(claude)]
        controller = ApplicationController(
            providers, AppStateStore(Path(directory) / "state.json")
        )
        controller.start()
        window = MainWindow(
            controller,
            {provider.provider_id: provider for provider in providers},
            PreviewStartup(),
            updater=PreviewUpdater(),
            confirm_enable=lambda: False,
            confirm_bootstrap=lambda: False,
            confirm_install=lambda _version: False,
        )
        window.resize(1040, 720)
        save(window, args.output / "dashboard.png", app)
        for width, height in ((1024, 768), (1280, 720), (1366, 768), (1600, 900), (1920, 1080)):
            window.resize(width, height)
            save(window, args.output / f"dashboard-{width}x{height}.png", app)
        window.resize(1040, 720)

        window.show_page(1)
        save(window, args.output / "settings.png", app)
        window.resize(1920, 1080)
        save(window, args.output / "settings-1920x1080.png", app)
        window.resize(1040, 720)

        release = ReleaseInfo(
            "0.7.0",
            ("Sharper provider status", "Safer Windows update flow"),
            "https://github.com/example/window-sentinel/releases/tag/v0.7.0",
            ReleaseAsset(PRODUCT.installer_filename, "https://github.com/example"),
            ReleaseAsset(PRODUCT.checksum_filename, "https://github.com/example"),
        )
        window.update_panel._operation_completed("check", release)
        settings_scroll = window.pages.widget(1)
        if isinstance(settings_scroll, QScrollArea):
            settings_scroll.verticalScrollBar().setValue(
                settings_scroll.verticalScrollBar().maximum()
            )
        save(window, args.output / "updates.png", app)

        window.show_page(2)
        save(window, args.output / "about.png", app)
        window.resize(1920, 1080)
        save(window, args.output / "about-1920x1080.png", app)
        window.resize(1040, 720)

        window.show_page(0)
        controller.update_provider_state(
            replace(codex, installed=False, status="Needs attention", reset_at=None)
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-provider-missing.png", app)

        controller.update_provider_state(
            replace(codex, status="Needs attention", detail="Capability probe failed safely.")
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-compatibility-failure.png", app)

        controller.update_provider_state(
            replace(codex, last_verified_at=now - 7 * 3600)
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-stale.png", app)

        controller.update_provider_state(
            replace(
                claude,
                automation_supported=False,
                status="Needs attention",
                detail="Claude initialization capability could not be confirmed.",
                reset_at=None,
                last_verified_at=None,
            )
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-claude-paused.png", app)
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
