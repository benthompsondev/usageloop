"""Disposable packaged MainWindow acceptance. No production provider or startup I/O."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time
import traceback
from unittest.mock import patch

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFontDatabase, QTextLayout
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import i18n
from .app_controller import ApplicationController
from .app_state import AppSettings, AppStateStore, ProviderViewState
from .desktop import MainWindow
from .history import SafeHistory
from .product import PRODUCT
from .updates import HostArtifact, ReleaseAsset, ReleaseInfo, UpdateCheckResult


def require(condition: bool, detail: str) -> None:
    # PyInstaller optimizes away assert statements.
    if not condition:
        raise RuntimeError(detail)


class SyntheticProvider:
    provider_id = "codex"

    def __init__(self, root: Path):
        now = time.time()
        self.state = ProviderViewState(
            "codex", "Codex", True, True, "Ready", "Fixed reset verified.",
            runtime_identity="synthetic-runtime", runtime_version="fixture",
            reset_at=int(now + 3600), last_verified_at=now, used_percent=18,
            usage_checked_at=now, weekly_used_percent=34,
            weekly_reset_at=int(now + 86400), quota_state="ANCHORED",
            last_action="ANCHOR_NOT_VERIFIED", outcome_category="GUARDED",
        )
        self.history = SafeHistory(root / "history.jsonl")
        self.history.record_error("packaged_acceptance_synthetic")

    def detect(self):
        return self.state

    def __getattr__(self, name):
        raise RuntimeError(f"Acceptance must not perform provider operation: {name}")


class NoStartup:
    def is_enabled(self):
        return False

    def set_enabled(self, _enabled):
        raise RuntimeError("Acceptance must not change startup registration")


class SyntheticUpdater:
    def __init__(self):
        self.checks = 0
        artifact = HostArtifact.for_host(PRODUCT.version)
        self.release = ReleaseInfo(
            PRODUCT.version, ("Synthetic acceptance notes.",),
            PRODUCT.releases_url,
            ReleaseAsset(artifact.payload_name, "https://example.invalid/payload"),
            ReleaseAsset(artifact.checksum_name, "https://example.invalid/checksum"),
            artifact,
        )

    def check(self):
        self.checks += 1
        return UpdateCheckResult("update_available", self.release)

    def __getattr__(self, name):
        raise RuntimeError(f"Acceptance must not download or install: {name}")


def run_ui_acceptance(app: QApplication, output: Path) -> int:
    """Capture real packaged widgets and fail with a report, never an error dialog."""
    output.mkdir(parents=True, exist_ok=True)
    report = {"version": PRODUCT.version, "qt_platform": app.platformName(),
              "tray_available": QSystemTrayIcon.isSystemTrayAvailable(), "languages": []}
    previous_language = i18n.current_language()
    app.setStyle("Fusion")
    try:
        with tempfile.TemporaryDirectory(prefix="usageloop-ui-acceptance-") as directory:
            root = Path(directory)
            # Patch only the language store; every other persistent path is explicit.
            def settings_factory(*_args):
                return QSettings(str(root / "language.ini"), QSettings.Format.IniFormat)
            settings_factory.Status = QSettings.Status
            with patch.object(i18n, "QSettings", settings_factory), \
                    patch.dict(os.environ, {"USAGELOOP_LANGUAGE": ""}):
                for language, _label in i18n.LANGUAGES:
                    if language == "system":
                        continue
                    i18n.save_language(language)
                    i18n.configure_language()
                    require(i18n.current_language() == language, "Saved language did not reload")
                    require(i18n.tr("Unknown acceptance key") == "Unknown acceptance key", "English fallback failed")
                    store = AppStateStore(root / f"{language}.json")
                    store.save(AppSettings(first_run_complete=True, schedule_mode="daily",
                                           daily_start_hour=6, daily_start_minute=45), {})
                    provider = SyntheticProvider(root)
                    controller = ApplicationController([provider], store)
                    controller.start()
                    updater = SyntheticUpdater()
                    window = MainWindow(controller, {"codex": provider}, NoStartup(),
                        updater=updater, confirm_enable=lambda: False,
                        confirm_bootstrap=lambda: False, confirm_install=lambda _v: False)
                    window.clock_timer.stop()
                    window.automation_timer.stop()
                    before = asdict(controller.settings)
                    require(not before["automation_enabled"], "Synthetic automation must be off")
                    target = output / language
                    target.mkdir(exist_ok=True)
                    for width, height in ((720, 560), (1366, 768)):
                        window.resize(width, height)
                        window.show()
                        for page, name in enumerate(("dashboard", "settings", "about")):
                            window.nav_buttons[page].click()
                            for _ in range(4):
                                app.processEvents()
                            require(window.pages.currentIndex() == page, "Navigation failed")
                            require(window.size().width() == width, "Window exceeded supported width")
                            if page == 1:
                                require(window.language_choice.currentData() == language, "Language selection mismatch")
                            if page == 2:
                                require(window.update_panel.isVisible(), "Updates missing from About")
                                require(window.update_panel.action_button.isVisible(), "Update action missing")
                            require(window.grab().save(str(target / f"{name}-{width}x{height}.png")), "Capture failed")
                    require(updater.checks == 0, "Updater checked without a click")
                    window.update_panel.action_button.click()
                    deadline = time.monotonic() + 5
                    while window.update_panel.state == "checking" and time.monotonic() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    require(updater.checks == 1 and window.update_panel.state == "available", "Manual update check failed")
                    require(window.update_panel.action_button.text() == i18n.tr(
                        "Download installer" if window.is_windows else "Download update"), "Untranslated update action")
                    require(window.grab().save(str(target / "update-available.png")), "Update capture failed")
                    # Use the actual connected language control. It must save without
                    # changing this session, scheduling, or automation.
                    next_language = "en" if language != "en" else "ja"
                    window.language_choice.setCurrentIndex(window.language_choice.findData(next_language))
                    require(i18n.language_preference() == next_language, "Language control did not save")
                    require(i18n.current_language() == language, "Language changed mid-session")
                    require(asdict(controller.settings) == before, "Language changed automation or schedule")
                    require(asdict(store.load()) == before, "Language changed saved app settings")
                    captured = []
                    def cancel_confirmation():
                        dialog = app.activeModalWidget()
                        if isinstance(dialog, QMessageBox):
                            captured.append(dialog.grab().save(str(target / "confirmation.png")))
                            dialog.reject()
                    QTimer.singleShot(0, cancel_confirmation)
                    require(not window.update_panel._confirm_install(PRODUCT.version), "Confirmation did not cancel")
                    require(captured == [True], "Actual install confirmation was not captured")
                    glyphs = None
                    if language == "ja" and QFontDatabase.families():
                        layout = QTextLayout("日本語 設定 更新 開始を確認できません", window.font())
                        layout.beginLayout()
                        layout.createLine()
                        layout.endLayout()
                        glyphs = [glyph for run in layout.glyphRuns() for glyph in run.glyphIndexes()]
                        require(bool(glyphs) and all(glyphs), "Japanese glyphs missing")
                    report["languages"].append({"language": language, "pages": 3,
                        "sizes": [[720, 560], [1366, 768]], "manual_update": True,
                        "language_saved_without_settings_changes": True,
                        "japanese_glyphs": len(glyphs) if glyphs else None})
                    window.force_close = True
                    window.close()
                    window.deleteLater()
                    app.processEvents()
        report["passed"] = True
        return 0
    except Exception:
        report["passed"] = False
        report["failure"] = traceback.format_exc()
        return 1
    finally:
        i18n._language = previous_language
        (output / "acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
