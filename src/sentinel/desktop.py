"""PySide6 desktop window, provider cards, and system-tray lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import time
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .app_controller import ApplicationController
from .app_state import ProviderViewState, format_countdown
from .provider_runtime import ProviderOperationResult
from .providers import CompatibilityResult


_STYLE = """
QWidget { background: #f4f6f8; color: #18212b; font-family: "Segoe UI"; font-size: 14px; }
QMainWindow { background: #f4f6f8; }
QLabel#title { font-size: 25px; font-weight: 700; color: #102a36; }
QLabel#subtitle { color: #566773; font-size: 14px; }
QFrame#primaryControl { background: #103b4a; border-radius: 14px; }
QFrame#primaryControl QLabel { background: transparent; color: white; }
QFrame#primaryControl QCheckBox { background: transparent; color: white; font-size: 17px; font-weight: 650; spacing: 12px; }
QCheckBox::indicator { width: 34px; height: 20px; border-radius: 10px; border: 1px solid #89969d; background: #d6dde1; }
QCheckBox::indicator:checked { border: 1px solid #54c4b0; background: #54c4b0; }
QFrame#providerCard { background: white; border: 1px solid #dce3e7; border-radius: 12px; }
QLabel#providerName { font-size: 18px; font-weight: 700; color: #142c36; }
QLabel#statusReady { color: #16735f; font-weight: 700; }
QLabel#statusWaiting { color: #5d6b75; font-weight: 700; }
QLabel#statusStarting { color: #1e6583; font-weight: 700; }
QLabel#statusAttention { color: #a54832; font-weight: 700; }
QLabel#countdown { font-size: 26px; font-weight: 700; color: #103b4a; }
QLabel#muted { color: #64737d; }
QPushButton { background: #e7eef1; border: 1px solid #ccd8dd; border-radius: 8px; padding: 8px 13px; font-weight: 600; }
QPushButton:hover { background: #dce8ec; }
QPushButton:focus { border: 2px solid #227c95; }
QPushButton#primaryButton { background: #176f7f; color: white; border: none; }
QPushButton#primaryButton:hover { background: #125e6c; }
QToolButton { color: #40545f; border: none; padding: 5px; font-weight: 600; }
QFrame#diagnostics { background: #e9eef1; border-radius: 9px; }
"""


class ProviderCard(QFrame):
    def __init__(self, state: ProviderViewState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("providerCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.setSpacing(7)

        header = QHBoxLayout()
        self.name_label = QLabel(state.display_name)
        self.name_label.setObjectName("providerName")
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("countdown")
        layout.addWidget(self.countdown_label)
        self.reset_label = QLabel()
        self.reset_label.setObjectName("muted")
        layout.addWidget(self.reset_label)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        self.metadata_label = QLabel()
        self.metadata_label.setObjectName("muted")
        self.metadata_label.setWordWrap(True)
        layout.addWidget(self.metadata_label)

        self.action_button = QPushButton("Start my first window now")
        self.action_button.setObjectName("primaryButton")
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.update_state(state, now=time.time())

    def update_state(self, state: ProviderViewState, *, now: float) -> None:
        self.status_label.setText(state.status)
        status_object = {
            "Ready": "statusReady",
            "Waiting": "statusWaiting",
            "Starting": "statusStarting",
            "Needs attention": "statusAttention",
        }.get(state.status, "statusAttention")
        self.status_label.setObjectName(status_object)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.countdown_label.setText(format_countdown(state.reset_at, now))
        self.reset_label.setText(_reset_copy(state.reset_at))
        self.detail_label.setText(state.detail)
        parts = []
        if state.last_verified_at is not None:
            parts.append(f"Last verified {_friendly_time(state.last_verified_at)}")
        if state.last_action:
            parts.append(f"Last action: {state.last_action}")
        if state.used_percent is not None and state.usage_checked_at is not None:
            parts.append(
                f"Last-known usage {state.used_percent}% ({_friendly_time(state.usage_checked_at)})"
            )
        self.metadata_label.setText("  •  ".join(parts) if parts else "No verified activity yet")


class WorkerSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class OperationWorker(QRunnable):
    def __init__(self, provider_id: str, operation: Callable[[], object]):
        super().__init__()
        self.provider_id = provider_id
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as exc:
            category = getattr(exc, "category", "unexpected_error")
            self.signals.failed.emit(self.provider_id, str(category))
            return
        self.signals.completed.emit(self.provider_id, result)


class MainWindow(QMainWindow):
    def __init__(
        self,
        controller: ApplicationController,
        providers: dict[str, object],
        startup_manager: object,
        *,
        confirm_enable: Callable[[], bool] | None = None,
        confirm_bootstrap: Callable[[], bool] | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.providers = providers
        self.startup_manager = startup_manager
        self.confirm_enable = confirm_enable or self._confirm_enable
        self.confirm_bootstrap = confirm_bootstrap or self._confirm_bootstrap
        self.active_operations: set[str] = set()
        self.thread_pool = QThreadPool.globalInstance()
        self.hide_on_close = False
        self.force_close = False

        self.setWindowTitle("Window Sentinel")
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(700, 600)
        self.resize(820, 700)
        self.setStyleSheet(_STYLE)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(34, 28, 34, 28)
        root.setSpacing(17)

        title = QLabel("Window Sentinel")
        title.setObjectName("title")
        subtitle = QLabel("Keep your five-hour coding windows ready, without babysitting reset times.")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        primary = QFrame()
        primary.setObjectName("primaryControl")
        primary_layout = QVBoxLayout(primary)
        primary_layout.setContentsMargins(22, 18, 22, 18)
        self.automation_toggle = QCheckBox("Keep my 5-hour windows ready")
        self.automation_toggle.setChecked(controller.settings.automation_enabled)
        explanation = QLabel(
            "When enabled, Sentinel acts only at a verified boundary or after you approve the first window."
        )
        explanation.setWordWrap(True)
        primary_layout.addWidget(self.automation_toggle)
        primary_layout.addWidget(explanation)
        root.addWidget(primary)

        self.provider_cards: dict[str, ProviderCard] = {}
        for provider_id, state in controller.states.items():
            card = ProviderCard(state)
            card.action_button.clicked.connect(
                lambda checked=False, key=provider_id: self.start_bootstrap(key)
            )
            self.provider_cards[provider_id] = card
            root.addWidget(card)

        self.advanced_button = QToolButton()
        self.advanced_button.setText("Advanced / Diagnostics")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setArrowType(Qt.ArrowType.RightArrow)
        root.addWidget(self.advanced_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostics = QFrame()
        self.diagnostics.setObjectName("diagnostics")
        diagnostic_layout = QVBoxLayout(self.diagnostics)
        diagnostic_layout.setContentsMargins(16, 13, 16, 13)
        self.startup_toggle = QCheckBox("Start Window Sentinel with Windows")
        self.startup_toggle.setChecked(bool(startup_manager.is_enabled()))
        self.diagnostic_text = QLabel()
        self.diagnostic_text.setWordWrap(True)
        diagnostic_layout.addWidget(self.startup_toggle)
        diagnostic_layout.addWidget(self.diagnostic_text)
        self.diagnostics.setVisible(False)
        root.addWidget(self.diagnostics)
        privacy = QLabel(
            "Local-first. Providers keep their own sign-in. Sentinel stores only safe window state and diagnostics."
        )
        privacy.setObjectName("muted")
        privacy.setWordWrap(True)
        root.addWidget(privacy)
        root.addStretch()

        scroll.setWidget(content)
        self.setCentralWidget(scroll)
        self.automation_toggle.toggled.connect(self._automation_toggled)
        self.startup_toggle.toggled.connect(self._startup_toggled)
        self.advanced_button.toggled.connect(self._advanced_toggled)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.clock_timer.start(1_000)
        self.automation_timer = QTimer(self)
        self.automation_timer.timeout.connect(self.evaluate_automation)
        self.automation_timer.start(15_000)
        self.refresh_clock()

    def refresh_clock(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        for provider_id, state in self.controller.states.items():
            card = self.provider_cards.get(provider_id)
            if card is not None:
                card.update_state(state, now=current)
                card.action_button.setVisible(
                    provider_id == "codex"
                    and self.controller.settings.automation_enabled
                    and state.installed
                    and state.automation_supported
                    and state.reset_at is None
                    and state.status != "Starting"
                )
        self._update_diagnostics()

    def evaluate_automation(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        for provider_id, decision in self.controller.decisions(now=current).items():
            if provider_id in self.active_operations:
                continue
            if decision.action == "PROBE":
                self._start_operation(provider_id, "probe")
            elif decision.action == "ROLLOVER":
                self._start_operation(provider_id, "rollover")

    def start_bootstrap(self, provider_id: str) -> None:
        if provider_id in self.active_operations or not self.confirm_bootstrap():
            return
        self._start_operation(provider_id, "bootstrap")

    def _start_operation(self, provider_id: str, action: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return
        state = self.controller.states[provider_id]
        self.controller.update_provider_state(replace(
            state, status="Starting", detail="Sentinel is checking the provider safely."
        ))
        self.active_operations.add(provider_id)
        operation = provider.probe if action == "probe" else lambda: provider.run_action(action)
        worker = OperationWorker(provider_id, operation)
        worker.signals.completed.connect(self._operation_completed)
        worker.signals.failed.connect(self._operation_failed)
        self.thread_pool.start(worker)
        self.refresh_clock()

    def _operation_completed(self, provider_id: str, result: object) -> None:
        self.active_operations.discard(provider_id)
        if isinstance(result, CompatibilityResult):
            self.controller.apply_compatibility(provider_id, result)
        elif isinstance(result, ProviderOperationResult):
            self.controller.update_provider_state(result.state)
        else:
            self._operation_failed(provider_id, "unsupported_result")
            return
        self.refresh_clock()

    def _operation_failed(self, provider_id: str, category: str) -> None:
        self.active_operations.discard(provider_id)
        state = self.controller.states[provider_id]
        self.controller.update_provider_state(replace(
            state,
            status="Needs attention",
            detail=f"The provider check stopped safely ({category}). No automatic retry will run.",
        ))
        self.refresh_clock()

    def _automation_toggled(self, enabled: bool) -> None:
        if enabled and not self.confirm_enable():
            self.automation_toggle.blockSignals(True)
            self.automation_toggle.setChecked(False)
            self.automation_toggle.blockSignals(False)
            return
        self.controller.set_automation_enabled(enabled)
        self.refresh_clock()
        if enabled:
            self.evaluate_automation()

    def _startup_toggled(self, enabled: bool) -> None:
        try:
            self.startup_manager.set_enabled(enabled)
        except OSError:
            self.startup_toggle.blockSignals(True)
            self.startup_toggle.setChecked(not enabled)
            self.startup_toggle.blockSignals(False)
            return
        self.controller.set_start_with_windows(enabled)

    def _advanced_toggled(self, visible: bool) -> None:
        self.diagnostics.setVisible(visible)
        self.advanced_button.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _update_diagnostics(self) -> None:
        rows = []
        for state in self.controller.states.values():
            version = state.runtime_version or "version unavailable"
            rows.append(f"{state.display_name}: {version} • {state.status}")
        self.diagnostic_text.setText("\n".join(rows))

    def _confirm_enable(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Enable Window Sentinel?",
            "Sentinel will use each provider's normal signed-in client only when a window needs attention. "
            "A provider check can start a five-hour window. It will never retry an ambiguous request.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_bootstrap(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Start the first Codex window?",
            "Sentinel will run the guarded Codex bootstrap once. It may submit one minimal request only if "
            "several observations prove the window is inactive and weekly protection passes.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.hide_on_close and not self.force_close:
            self.hide()
            event.ignore()
            return
        event.accept()


class DesktopShell:
    def __init__(self, window: MainWindow):
        self.window = window
        self.tray = QSystemTrayIcon(make_app_icon(), window)
        self.tray.setToolTip("Window Sentinel")
        menu = QMenu()
        open_action = menu.addAction("Open Window Sentinel")
        open_action.triggered.connect(self.restore_window)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Window Sentinel")
        quit_action.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.window.hide_on_close = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray.show()

    def hide_window(self) -> None:
        self.window.hide()

    def restore_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        self.window.force_close = True
        self.tray.hide()
        self.window.close()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.restore_window()


def make_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#103b4a"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 15, 15)
    painter.setBrush(QColor("#54c4b0"))
    painter.drawEllipse(17, 17, 30, 30)
    painter.setPen(QColor("#103b4a"))
    painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "5h")
    painter.end()
    return QIcon(pixmap)


def _reset_copy(reset_at: int | None) -> str:
    if reset_at is None:
        return "Reset time not verified"
    try:
        local = datetime.fromtimestamp(reset_at).astimezone()
    except (OSError, OverflowError, ValueError):
        return "Reset time unavailable"
    return f"Resets {local.strftime('%a %I:%M %p').replace(' 0', ' ')}"


def _friendly_time(timestamp: float) -> str:
    try:
        local = datetime.fromtimestamp(timestamp).astimezone()
    except (OSError, OverflowError, ValueError):
        return "at an unknown time"
    return local.strftime("%b %d at %I:%M %p").replace(" 0", " ")
