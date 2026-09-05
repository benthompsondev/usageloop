"""Render deterministic Codex-only screenshots with no provider traffic."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from datetime import datetime
from pathlib import Path
import tempfile

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import QApplication, QDateTimeEdit, QScrollArea, QWidget

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppStateStore, ProviderViewState
from sentinel.desktop import MainWindow
from sentinel.history import SafeHistory
from sentinel.product import PRODUCT
from sentinel.providers import CompatibilityResult
from sentinel.updates import ReleaseAsset, ReleaseInfo, UpdateCheckResult


class PreviewProvider:
    provider_id = "codex"

    def __init__(self, state: ProviderViewState):
        self.state = state

    def detect(self) -> ProviderViewState:
        return self.state

    def probe(self) -> CompatibilityResult:
        return CompatibilityResult(True, "Waiting", "Compatibility confirmed.", self.state.runtime_identity)


class PreviewStartup:
    def is_enabled(self) -> bool:
        return False

    def set_enabled(self, _enabled: bool) -> None:
        return None


class PreviewUpdater:
    def check(self):
        raise AssertionError("Screenshot rendering must not contact GitHub.")


def save(
    window: MainWindow,
    target: Path,
    app: QApplication,
    *,
    focus: QWidget | None = None,
) -> None:
    window.hide()
    app.processEvents()
    window.show()
    for _ in range(3):
        app.processEvents()
    if focus is not None:
        window.activateWindow()
        focus.setFocus(Qt.FocusReason.OtherFocusReason)
        for _ in range(2):
            app.processEvents()
    if not window.grab().save(str(target), "PNG"):
        raise RuntimeError(f"Could not save {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--interactive", action="store_true", help="Keep the synthetic app open; no provider traffic.")
    parser.add_argument(
        "--platform",
        choices=("Windows", "Linux"),
        default="Windows",
        help=(
            "Which host surface to render. Linux captures only the two pages "
            "that actually differ, so the shared pages stay one set of images."
        ),
    )
    parser.add_argument(
        "--allow-foreign-host",
        action="store_true",
        help="Render the Windows set from a non-Windows machine anyway.",
    )
    parser.add_argument("--compatibility-failure", action="store_true", help="Preview a failed read-only check and its recovery.")
    args = parser.parse_args()
    if args.platform == "Windows" and os.name != "nt" and not args.allow_foreign_host:
        # Font metrics differ per host, so rendering the Windows set from Linux
        # rewrites every published Windows screenshot with a subtly different
        # image. That has already happened once by accident.
        parser.error(
            "Refusing to rewrite the Windows screenshots from a non-Windows host. "
            "Use --platform Linux for the Linux set, or pass --allow-foreign-host "
            "if you really mean to re-render the Windows images here."
        )
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    # Keep public screenshots stable between runs while expressing the fixture
    # in local time, just like the desktop UI does.
    now = datetime(2030, 1, 15, 7, 30).astimezone().timestamp()
    codex = ProviderViewState(
        "codex", "Codex", True, True, "Ready", "Fixed reset verified.",
        "codex-runtime", "0.96.0", int(now + 3 * 3600 + 42 * 60), now - 60,
        "Anchor verified", 18, now - 60, 34, int(now + 4 * 24 * 3600),
    )
    with tempfile.TemporaryDirectory() as directory:
        provider = PreviewProvider(codex)
        provider.history = SafeHistory(Path(directory) / "history.jsonl")
        for offset, outcome in ((-86400, "failed_guarded"), (-3 * 3600, "verified")):
            attempt = provider.history.reserve_trigger(
                mode="rollover", idempotency_key=f"preview:{abs(offset)}", boundary_reset_at=None,
                model="gpt-5.6-luna", reasoning_effort="low", now=now + offset,
            )
            provider.history.transition_trigger(attempt.attempt_id, "launch_attempted", now=now + offset + 1)
            provider.history.transition_trigger(attempt.attempt_id, outcome, now=now + offset + 30)
        controller = ApplicationController(
            [provider], AppStateStore(Path(directory) / "state.json")
        )
        controller.start()
        controller.set_automation_enabled(True)
        window = MainWindow(
            controller, {"codex": provider}, PreviewStartup(), updater=PreviewUpdater(),
            confirm_enable=lambda: False, confirm_bootstrap=lambda: False,
            confirm_install=lambda _version: False,
            # Pinning the host keeps a capture run comparable no matter which
            # machine renders it. Most pages are identical on both.
            platform_name=args.platform,
        )
        window.clock_timer.stop()
        window.automation_timer.stop()
        # Show the weekly routine and pause control in the public dashboard.
        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("weekly"))
        controller.set_weekly_start_times(
            ((4, 0), (4, 0), (4, 0), (4, 0), (4, 0), (5, 0), (5, 0))
        )
        window._sync_weekly_editor()
        controller.apply_compatibility("codex", CompatibilityResult(
            not args.compatibility_failure,
            "Needs attention" if args.compatibility_failure else "Ready",
            "The Codex compatibility check failed safely. No automatic request was sent."
            if args.compatibility_failure else "Compatibility confirmed.",
            codex.runtime_identity,
        ))
        window.refresh_clock(now=now)
        if args.interactive:
            window.resize(1040, 750)
            window.show()
            return app.exec()
        if args.platform == "Linux":
            # Only Settings and About differ on Linux: the startup card names
            # the session and the Windows-only updater is replaced by a notice.
            # Every other page is the same UI, so it stays one set of images.
            window.resize(1366, 768)
            window.show_page(1)
            save(window, args.output / "settings-linux.png", app)
            settings = window.pages.widget(1)
            if isinstance(settings, QScrollArea):
                settings.verticalScrollBar().setValue(
                    settings.verticalScrollBar().maximum()
                )
            save(window, args.output / "updates-linux.png", app)
            window.show_page(2)
            save(window, args.output / "about-linux.png", app)
            window.close()
            return 0

        window.show_recent_starts()
        save(window.recent_starts_dialog, args.output / "recent-starts.png", app)
        window.recent_starts_dialog.close()
        for width, height in (
            (1024, 768),
            (1280, 720),
            (1366, 768),
            (1600, 900),
            (1920, 1080),
        ):
            window.resize(width, height)
            save(window, args.output / f"dashboard-{width}x{height}.png", app)
        # Use the 1600x900 composition for the README hero so the current
        # window, schedule, and weekly guard are visible in one frame.
        window.resize(1600, 900)
        save(window, args.output / "dashboard.png", app)

        window.resize(1040, 720)
        window.show_page(1)
        save(window, args.output / "settings.png", app)
        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("daily"))
        window.daily_time.setTime(QTime(4, 0))
        window.refresh_clock(now=now)
        save(window, args.output / "settings-daily.png", app)
        window.schedule_mode.setCurrentIndex(window.schedule_mode.findData("weekly"))
        controller.set_weekly_start_times(
            ((4, 0), (4, 0), (4, 0), (4, 0), (4, 0), (5, 0), (5, 0))
        )
        window._sync_weekly_editor()
        window.refresh_clock(now=now)
        window.resize(1366, 768)
        save(window, args.output / "settings-weekly.png", app)
        window.weekday_quick_time.setCurrentSection(
            QDateTimeEdit.Section.MinuteSection
        )
        save(
            window,
            args.output / "settings-weekly-time-focused.png",
            app,
            focus=window.weekday_quick_time,
        )
        window.weekly_custom_days.toggle.setChecked(True)
        window.resize(1366, 900)
        app.processEvents()
        settings = window.pages.widget(1)
        if isinstance(settings, QScrollArea):
            settings.ensureWidgetVisible(window.weekly_preview_card, 24, 24)
        save(window, args.output / "settings-weekly-expanded.png", app)
        window.resize(1024, 768)
        app.processEvents()
        if isinstance(settings, QScrollArea):
            settings.ensureWidgetVisible(window.weekly_preview_card, 24, 24)
        save(window, args.output / "settings-weekly-expanded-1024x768.png", app)
        window.weekly_custom_days.toggle.setChecked(False)
        release = ReleaseInfo(
            "1.2.0",
            (
                "UsageLoop now keeps working safely if its local state files become unreadable.",
                "Daily start times are checked before they are saved.",
            ),
            "https://github.com/example/usage-loop/releases/tag/v1.2.0",
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
        save(window, args.output / "dashboard-provider-missing.png", app)
        controller.update_provider_state(
            replace(codex, status="Needs attention", detail="Capability probe failed safely.")
        )
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-compatibility-failure.png", app)
        controller.update_provider_state(replace(codex, last_verified_at=now - 7 * 3600))
        window.refresh_clock(now=now)
        save(window, args.output / "dashboard-stale.png", app)
        controller.update_provider_state(
            ProviderViewState.waiting(
                "codex",
                "Codex",
                installed=True,
                runtime_identity=codex.runtime_identity,
            )
        )
        window.refresh_clock(now=now)
        window.resize(1040, 720)
        save(window, args.output / "dashboard-first-run.png", app)
        window.resize(1920, 1080)
        save(window, args.output / "dashboard-first-run-1920x1080.png", app)
        window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
