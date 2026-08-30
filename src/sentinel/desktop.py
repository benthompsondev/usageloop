"""PySide6 desktop shell, navigation, background work, and tray lifecycle."""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon
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
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .app_controller import ApplicationController
from .branding import make_app_icon, render_mark
from .product import PRODUCT
from .provider_runtime import ProviderOperationResult
from .providers import CompatibilityResult
from .ui_components import ProviderCard, StatusPill, make_surface_card, present_provider_state
from .ui_theme import desktop_stylesheet
from .update_ui import UpdatePanel
from .updates import GitHubReleaseUpdater


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
    PAGE_NAMES = ("Dashboard", "Settings", "About")

    def __init__(
        self,
        controller: ApplicationController,
        providers: dict[str, object],
        startup_manager: object,
        *,
        updater: GitHubReleaseUpdater | None = None,
        confirm_enable: Callable[[], bool] | None = None,
        confirm_bootstrap: Callable[[], bool] | None = None,
        confirm_install: Callable[[str], bool] | None = None,
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

        self.setWindowTitle(PRODUCT.display_name)
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(820, 640)
        self.resize(1040, 720)
        self.setStyleSheet(desktop_stylesheet())

        app_root = QWidget()
        app_root.setObjectName("appRoot")
        shell = QVBoxLayout(app_root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_header())

        self.pages = QStackedWidget()
        self.provider_cards: dict[str, ProviderCard] = {}
        self.pages.addWidget(self._build_dashboard())
        self.pages.addWidget(
            self._build_settings(updater or GitHubReleaseUpdater(), confirm_install)
        )
        self.pages.addWidget(self._build_about())
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(app_root)

        self.automation_toggle.toggled.connect(self._automation_toggled)
        self.startup_toggle.toggled.connect(self._startup_toggled)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.clock_timer.start(1_000)
        self.automation_timer = QTimer(self)
        self.automation_timer.timeout.connect(self.evaluate_automation)
        self.automation_timer.start(15_000)
        self.refresh_clock()

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("appHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 15, 28, 15)
        layout.setSpacing(13)

        mark = QLabel()
        mark.setPixmap(render_mark(38))
        mark.setFixedSize(38, 38)
        layout.addWidget(mark)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        wordmark = QHBoxLayout()
        wordmark.setSpacing(0)
        first = QLabel("Usage")
        first.setObjectName("wordmarkPrimary")
        second = QLabel("Loop")
        second.setObjectName("wordmarkAccent")
        wordmark.addWidget(first)
        wordmark.addWidget(second)
        wordmark.addStretch()
        purpose = QLabel(PRODUCT.tagline)
        purpose.setObjectName("appPurpose")
        # The header must survive a 1366 pixel display, so the tagline yields
        # its width instead of forcing the whole window wider.
        purpose.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        identity.addLayout(wordmark)
        identity.addWidget(purpose)
        layout.addLayout(identity)
        layout.addStretch()

        self.nav_buttons: list[QPushButton] = []
        for index, page_name in enumerate(self.PAGE_NAMES):
            button = QPushButton(page_name)
            button.setObjectName("navButton")
            button.setProperty("active", index == 0)
            button.clicked.connect(
                lambda checked=False, page_index=index: self.show_page(page_index)
            )
            self.nav_buttons.append(button)
            layout.addWidget(button)
        chip = QLabel("Local-first")
        chip.setObjectName("trustChip")
        chip.setToolTip(
            "Every check runs on this PC. Codex and Claude Code keep their own sign-in."
        )
        # The chip keeps its natural width; the tagline above absorbs any
        # squeeze instead, so nothing here clips at 1366 pixels.
        layout.addSpacing(10)
        layout.addWidget(chip)
        return header

    def _page(self, title: str, intro: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setMaximumWidth(1080)
        content.setMinimumWidth(820)
        root = QVBoxLayout(content)
        root.setContentsMargins(34, 27, 34, 30)
        root.setSpacing(16)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        intro_label = QLabel(intro)
        intro_label.setObjectName("pageIntro")
        intro_label.setWordWrap(True)
        root.addWidget(title_label)
        root.addWidget(intro_label)
        wrapper = QWidget()
        wrapper_layout = QHBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addStretch()
        wrapper_layout.addWidget(content, 1)
        wrapper_layout.addStretch()
        scroll.setWidget(wrapper)
        return scroll, root

    def _build_dashboard(self) -> QWidget:
        page, root = self._page(
            "Your coding windows",
            f"{PRODUCT.display_name} watches your Codex and Claude Code five-hour windows and can "
            "start a fresh one for you the moment the old one runs out.",
        )
        primary = QFrame()
        primary.setObjectName("primaryControl")
        primary_layout = QHBoxLayout(primary)
        primary_layout.setContentsMargins(22, 18, 22, 18)
        primary_layout.setSpacing(20)
        primary_copy = QVBoxLayout()
        primary_copy.setSpacing(4)
        eyebrow = QLabel("MAIN SWITCH")
        eyebrow.setObjectName("eyebrow")
        self.automation_toggle = QCheckBox("Keep my 5-hour windows ready")
        self.automation_toggle.setObjectName("automationToggle")
        self.automation_toggle.setChecked(self.controller.settings.automation_enabled)
        explanation = QLabel(
            "While this is off, nothing is ever sent to a provider. Turn it on and "
            f"{PRODUCT.display_name} uses the smallest possible request, and only after every safety check passes."
        )
        explanation.setProperty("muted", True)
        explanation.setWordWrap(True)
        primary_copy.addWidget(eyebrow)
        primary_copy.addWidget(self.automation_toggle)
        primary_copy.addWidget(explanation)
        primary_layout.addLayout(primary_copy, 1)
        self.automation_state_label = StatusPill()
        self.automation_state_label.setFixedHeight(28)
        self.automation_state_label.setMinimumWidth(44)
        primary_layout.addWidget(self.automation_state_label)
        root.addWidget(primary)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(16)
        for provider_id, state in self.controller.states.items():
            card = ProviderCard(state)
            card.action_button.clicked.connect(
                lambda checked=False, key=provider_id: self.start_bootstrap(key)
            )
            self.provider_cards[provider_id] = card
            provider_row.addWidget(card, 1)
        root.addLayout(provider_row)

        root.addWidget(self._build_assurance_strip())
        root.addStretch()
        return page

    def _build_assurance_strip(self) -> QWidget:
        """The four things a new user has to understand, kept to one glance."""
        strip = QFrame()
        strip.setObjectName("assuranceStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(22)
        points = (
            ("Everything stays local", "Checks and countdowns run on this PC. Nothing is uploaded."),
            ("No passwords or tokens", "Codex and Claude Code keep their own sign-in. We never read it."),
            ("Counting down is free", "The timers cost you nothing. Only starting a window uses your plan."),
            ("One small action, once", "When a window needs starting we send the smallest request, and never retry it."),
        )
        for title, body in points:
            column = QVBoxLayout()
            column.setSpacing(3)
            heading = QLabel(title)
            heading.setObjectName("assuranceTitle")
            heading.setWordWrap(True)
            detail = QLabel(body)
            detail.setObjectName("assuranceBody")
            detail.setWordWrap(True)
            # A wrapped label otherwise reports its full single-line width as a
            # minimum, which would push the window past a 1366 pixel display.
            for label in (heading, detail):
                label.setMinimumWidth(140)
                label.setSizePolicy(
                    QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
                )
            column.addWidget(heading)
            column.addWidget(detail)
            column.addStretch()
            layout.addLayout(column, 1)
        return strip

    def _build_settings(
        self,
        updater: GitHubReleaseUpdater,
        confirm_install: Callable[[str], bool] | None,
    ) -> QWidget:
        page, root = self._page(
            "Settings",
            "Startup, updates, and the technical detail that stays out of your way on the dashboard.",
        )

        startup_card, startup_layout = make_surface_card(
            "Windows startup",
            "Start in the tray when you sign in to Windows. Applies to your account only, needs no "
            "administrator rights, and is off until you turn it on.",
        )
        startup_row = QFrame()
        startup_row.setObjectName("settingRow")
        row_layout = QHBoxLayout(startup_row)
        row_layout.setContentsMargins(14, 12, 14, 12)
        row_copy = QVBoxLayout()
        row_copy.setSpacing(2)
        row_title = QLabel(f"Start {PRODUCT.display_name} with Windows")
        row_title.setObjectName("secondaryMetric")
        row_hint = QLabel("Opens quietly in the tray, not on screen")
        row_hint.setProperty("muted", True)
        row_copy.addWidget(row_title)
        row_copy.addWidget(row_hint)
        row_layout.addLayout(row_copy)
        row_layout.addStretch()
        self.startup_toggle = QCheckBox()
        self.startup_toggle.setChecked(bool(self.startup_manager.is_enabled()))
        row_layout.addWidget(self.startup_toggle)
        startup_layout.addWidget(startup_row)
        root.addWidget(startup_card)

        diagnostic_card, diagnostic_layout = make_surface_card(
            "Diagnostics",
            "For when something looks wrong. Provider versions, compatibility, cached state, and the exact "
            "reason a check stopped. No prompts, responses, credentials, or account identity are ever recorded.",
        )
        self.diagnostic_text = QLabel()
        self.diagnostic_text.setObjectName("diagnosticValue")
        self.diagnostic_text.setWordWrap(True)
        self.diagnostic_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        diagnostic_layout.addWidget(self.diagnostic_text)
        root.addWidget(diagnostic_card)

        self.update_panel = UpdatePanel(
            updater, confirm_install=confirm_install, parent=self
        )
        self.update_panel.installer_launched.connect(self._exit_for_update)
        root.addWidget(self.update_panel)
        root.addStretch()
        return page

    def _build_about(self) -> QWidget:
        page, root = self._page(f"About {PRODUCT.display_name}", PRODUCT.tagline)
        about, about_layout = make_surface_card(PRODUCT.display_name)
        version = QLabel(f"Version {PRODUCT.version} \u00b7 Windows \u00b7 MIT licensed")
        version.setObjectName("secondaryMetric")
        about_layout.addWidget(version)
        description = QLabel(
            "Your Codex and Claude Code plans work in five-hour windows. A window only starts when you "
            "actually use the tool, so a window you never opened is a window you never got. "
            f"{PRODUCT.display_name} watches for that and, once you allow it, starts the next one using "
            "the smallest request the provider accepts."
        )
        description.setProperty("muted", True)
        description.setWordWrap(True)
        about_layout.addWidget(description)
        links = QHBoxLayout()
        for label, url in (
            ("View source", PRODUCT.github_url),
            ("Releases", PRODUCT.releases_url),
            ("Report an issue", PRODUCT.issues_url),
        ):
            button = QPushButton(label)
            button.setObjectName("linkButton")
            button.clicked.connect(
                lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target))
            )
            links.addWidget(button)
        links.addStretch()
        about_layout.addLayout(links)
        root.addWidget(about)

        support, support_layout = make_surface_card(
            "What each provider does today",
            "The two providers are at different stages, and the app does not pretend otherwise.",
        )
        codex_row = QLabel(
            "<b>Codex</b> is verified. A measured live test proved that one small request through the "
            "local Codex app-server starts a fresh five-hour window, and that is the path used here."
        )
        codex_row.setObjectName("secondaryMetric")
        codex_row.setWordWrap(True)
        support_layout.addWidget(codex_row)
        claude_header = QHBoxLayout()
        claude_label = QLabel("<b>Claude Code</b>")
        claude_label.setObjectName("secondaryMetric")
        preview = QLabel("PREVIEW")
        preview.setObjectName("previewTag")
        claude_header.addWidget(claude_label)
        claude_header.addWidget(preview)
        claude_header.addStretch()
        support_layout.addLayout(claude_header)
        claude_row = QLabel(
            "Claude Code offers no free way to read your window state, so this provider reads the status "
            "line Claude Code already writes and runs one prompt-free initialization. Whether that "
            "initialization starts a five-hour window is not yet proven, so treat the Claude card as "
            "information rather than a guarantee. It stays guarded either way: one attempt, never repeated."
        )
        claude_row.setObjectName("secondaryMetric")
        claude_row.setWordWrap(True)
        support_layout.addWidget(claude_row)
        root.addWidget(support)

        privacy, privacy_layout = make_surface_card(
            "Privacy and safety",
            "Codex and Claude Code keep their own sign-in. This app never reads tokens, credentials, "
            "conversations, or account identifiers.",
        )
        boundaries = QLabel(
            "\u2022 Provider automation is off until you switch it on\n"
            "\u2022 Update checks run only when you press the button, and never touch your plan\n"
            "\u2022 Countdowns are calculated locally and cause no provider traffic\n"
            "\u2022 A request whose outcome is unclear is never retried automatically\n"
            "\u2022 Weekly limits are respected before any action is considered"
        )
        boundaries.setObjectName("secondaryMetric")
        boundaries.setWordWrap(True)
        privacy_layout.addWidget(boundaries)
        root.addWidget(privacy)
        root.addStretch()
        return page

    def show_page(self, index: int) -> None:
        if not 0 <= index < self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setProperty("active", button_index == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def refresh_clock(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        enabled = self.controller.settings.automation_enabled
        self.automation_state_label.set_status(
            "ON" if enabled else "OFF", "success" if enabled else "neutral"
        )
        for provider_id, state in self.controller.states.items():
            card = self.provider_cards.get(provider_id)
            if card is not None:
                card.update_state(state, now=current)
                card.action_button.setVisible(
                    provider_id == "codex"
                    and enabled
                    and state.installed
                    and state.automation_supported
                    and state.reset_at is None
                    and state.status != "Starting"
                )
        self._update_diagnostics(now=current)

    def evaluate_automation(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        self.controller.refresh_local_states(exclude=self.active_operations)
        for provider_id, decision in self.controller.decisions(now=current).items():
            if provider_id in self.active_operations:
                continue
            if decision.action == "PROBE":
                self._start_operation(provider_id, "probe")
            elif decision.action == "ROLLOVER":
                self._start_operation(provider_id, "rollover")
            elif decision.action == "BOOTSTRAP" and provider_id == "claude":
                self._start_operation(provider_id, "bootstrap")

    def start_bootstrap(self, provider_id: str) -> None:
        if provider_id in self.active_operations or not self.confirm_bootstrap():
            return
        self._start_operation(provider_id, "bootstrap")

    def _start_operation(self, provider_id: str, action: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return
        state = self.controller.states[provider_id]
        self.controller.update_provider_state(
            replace(state, status="Starting", detail="Checking with the provider safely.")
        )
        self.active_operations.add(provider_id)
        operation = (
            provider.probe
            if action == "probe"
            else lambda: provider.run_action(action, current_state=state)
        )
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
        self.controller.update_provider_state(
            replace(
                state,
                status="Needs attention",
                detail=f"The check stopped safely ({category}). Nothing was retried. See Settings for detail.",
            )
        )
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
            QMessageBox.warning(
                self,
                "Startup setting not changed",
                "Windows did not allow the per-user startup setting to be changed.",
            )
            return
        self.controller.set_start_with_windows(enabled)

    def _update_diagnostics(self, *, now: float) -> None:
        rows = []
        compatible = self.controller.settings.compatible_runtime_identities or {}
        for provider_id, state in self.controller.states.items():
            version = state.runtime_version or "version unavailable"
            compatibility = (
                "passed"
                if compatible.get(provider_id) == state.runtime_identity
                else ("not applicable" if not state.automation_supported else "not confirmed")
            )
            rows.append(
                f"{state.display_name}\n"
                f"  Installed: {'yes' if state.installed else 'no'}\n"
                f"  Version: {version}\n"
                f"  Compatibility: {compatibility}\n"
                f"  Raw state: {state.status}\n"
                f"  Detail: {state.detail}"
            )
        automation = "enabled" if self.controller.settings.automation_enabled else "off"
        rows.append(f"Automation\n  Global control: {automation}\n  Provider-triggering activity while off: none")
        self.diagnostic_text.setText("\n\n".join(rows))

    def _exit_for_update(self) -> None:
        self.force_close = True
        application = QApplication.instance()
        if application is not None:
            QTimer.singleShot(200, application.quit)

    def _confirm_enable(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Keep your 5-hour windows ready?",
            f"{PRODUCT.display_name} will use each provider's own signed-in client, and only after its "
            "safety checks pass. For Claude Code it may add a local status-line helper, and only when you "
            "have not set one yourself.\n\nStarting a window uses a small amount of your plan. An action "
            "whose outcome is unclear is never retried.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_bootstrap(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Start your first Codex window now?",
            "This sends one small request through your signed-in Codex client, and only if repeated checks "
            "show no window is running and your weekly limit is safe. It runs once and is not retried.",
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
        self.tray.setToolTip(PRODUCT.display_name)
        menu = QMenu()
        open_action = menu.addAction(f"Open {PRODUCT.display_name}")
        open_action.triggered.connect(self.restore_window)
        menu.addSeparator()
        quit_action = menu.addAction(f"Quit {PRODUCT.display_name}")
        quit_action.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.window.hide_on_close = QSystemTrayIcon.isSystemTrayAvailable()
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.tray.hide)
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
