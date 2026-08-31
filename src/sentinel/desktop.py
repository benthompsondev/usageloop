"""PySide6 desktop shell, navigation, background work, and tray lifecycle."""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QRunnable,
    QTime,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .app_controller import ApplicationController
from .chain import WEEKLY_PROTECTION_PERCENT
from .branding import make_app_icon, render_mark
from .product import PRODUCT
from .provider_runtime import ProviderOperationResult
from .providers import CompatibilityResult
from .diagnostics import technical_summary
from .ui_components import (
    Disclosure,
    ElidingLabel,
    ProviderCard,
    ScheduleCard,
    StatusPill,
    ToggleSwitch,
    make_surface_card,
    present_provider_state,
)
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
        self.active_operations: dict[str, str] = {}
        self.thread_pool = QThreadPool.globalInstance()
        self.hide_on_close = False
        self.force_close = False

        self.setWindowTitle(PRODUCT.display_name)
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(720, 560)
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
        shell.addWidget(self._build_footer())
        self.setCentralWidget(app_root)

        self.automation_toggle.toggled.connect(self._automation_toggled)
        self.startup_toggle.toggled.connect(self._startup_toggled)
        self.schedule_mode.currentIndexChanged.connect(self._schedule_mode_changed)
        self.daily_time.timeChanged.connect(self._daily_time_changed)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.clock_timer.start(1_000)
        self.automation_timer = QTimer(self)
        self.automation_timer.timeout.connect(self.evaluate_automation)
        self.automation_timer.start(15_000)
        self.refresh_clock()

    #: Windows gives a maximized window an invisible resize border that hangs
    #: past each screen edge. Content flush against the layout edge lands under
    #: it and is clipped, so the header keeps a margin wider than that border.
    HEADER_SIDE_MARGIN = 26

    def _build_header(self) -> QWidget:
        """Brand on the left, navigation and trust chip pinned right.

        The brand block is the only elastic item and it is allowed to shrink to
        nothing, so the navigation and chip can never be pushed off the edge no
        matter how narrow or wide the window gets.
        """
        header = QFrame()
        header.setObjectName("appHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(self.HEADER_SIDE_MARGIN, 12, self.HEADER_SIDE_MARGIN, 12)
        layout.setSpacing(16)

        brand = QWidget()
        brand.setObjectName("brandBlock")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)
        mark = QLabel()
        # The mark supports the wordmark rather than competing with it.
        mark.setPixmap(render_mark(36))
        mark.setFixedSize(36, 36)
        brand_layout.addWidget(mark)

        identity = QVBoxLayout()
        identity.setSpacing(1)
        wordmark = QHBoxLayout()
        wordmark.setSpacing(0)
        first = QLabel("Usage")
        first.setObjectName("wordmarkPrimary")
        second = QLabel("Loop")
        second.setObjectName("wordmarkAccent")
        for_codex = QLabel("  for Codex")
        for_codex.setObjectName("wordmarkQualifier")
        wordmark.addWidget(first)
        wordmark.addWidget(second)
        wordmark.addWidget(for_codex)
        wordmark.addStretch()
        self.tagline_label = ElidingLabel(PRODUCT.tagline)
        self.tagline_label.setObjectName("appPurpose")
        identity.addLayout(wordmark)
        identity.addWidget(self.tagline_label)
        brand_layout.addLayout(identity, 1)
        # Stretch factor 1 gives the brand every spare pixel, and its own
        # minimum is near zero, so it absorbs the slack and yields it back.
        layout.addWidget(brand, 1)

        controls = QWidget()
        controls.setObjectName("headerControls")
        controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        self.nav_buttons: list[QPushButton] = []
        for index, page_name in enumerate(self.PAGE_NAMES):
            button = QPushButton(page_name)
            button.setObjectName("navButton")
            button.setProperty("active", index == 0)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda checked=False, page_index=index: self.show_page(page_index)
            )
            self.nav_buttons.append(button)
            controls_layout.addWidget(button)

        self.trust_chip = QLabel("Local-first")
        self.trust_chip.setObjectName("trustChip")
        self.trust_chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.trust_chip.setToolTip(
            "Every check runs on this PC. Codex keeps its own sign-in."
        )
        controls_layout.addSpacing(6)
        controls_layout.addWidget(self.trust_chip)
        layout.addWidget(controls, 0)

        self.header_widget = header
        self.header_controls = controls
        self.brand_block = brand
        header.installEventFilter(self)
        return header

    def eventFilter(self, watched, event):
        if watched is getattr(self, "header_widget", None) and event.type() == QEvent.Type.Resize:
            self._fit_header()
        return super().eventFilter(watched, event)

    def _fit_header(self) -> None:
        """Drop decorative header parts before anything can be cut off.

        The eliding tagline handles ordinary squeeze. This is the last resort for
        genuinely tiny windows: the tagline, then the trust chip, are hidden
        rather than allowed to clip. Navigation is never hidden.
        """
        header = getattr(self, "header_widget", None)
        if header is None:
            return
        available = header.width() - self.HEADER_SIDE_MARGIN * 2

        # Measure rather than assume. A larger Windows UI font, or text scaling
        # for accessibility, makes the navigation far wider than it looks at the
        # default size, and a guessed floor silently stopped protecting the chip.
        brand_floor = self.brand_block.minimumSizeHint().width()
        spacing = 6
        nav_cost = sum(button.sizeHint().width() for button in self.nav_buttons)
        nav_cost += spacing * max(0, len(self.nav_buttons) - 1)
        chip_cost = self.trust_chip.sizeHint().width() + spacing * 2

        fits_with_chip = available >= brand_floor + nav_cost + chip_cost
        self.trust_chip.setVisible(fits_with_chip)
        used = brand_floor + nav_cost + (chip_cost if fits_with_chip else 0)
        # The tagline is the first thing to go; it is decoration, not navigation.
        self.tagline_label.setVisible(available - used >= 60)

    def _build_footer(self) -> QWidget:
        """A quiet status strip so a tall window ends deliberately.

        It repeats the promises that matter and carries the version, which is
        what people quote in a bug report. Nothing decorative.
        """
        footer = QFrame()
        footer.setObjectName("appFooter")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(self.HEADER_SIDE_MARGIN, 9, self.HEADER_SIDE_MARGIN, 9)
        layout.setSpacing(12)
        self.footer_promise = ElidingLabel(
            "Private by design  \u00b7  Local-first  \u00b7  No telemetry  \u00b7  No cloud upload"
        )
        self.footer_promise.setObjectName("footerPromise")
        layout.addWidget(self.footer_promise, 1)
        version = QLabel(f"{PRODUCT.display_name} v{PRODUCT.version}")
        version.setObjectName("footerVersion")
        version.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout.addWidget(version, 0)
        self.footer_widget = footer
        return footer

    def _page(self, title: str, intro: str) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setMaximumWidth(1140)
        content.setMinimumWidth(560)
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
            "Your Codex reset clock",
            "See the real five-hour clock, choose when the next one starts, and keep weekly usage protected.",
        )
        overall = QFrame()
        overall.setObjectName("overallStatusCard")
        overall_layout = QHBoxLayout(overall)
        overall_layout.setContentsMargins(22, 17, 22, 17)
        overall_layout.setSpacing(16)
        self.overall_icon = QLabel("✓")
        self.overall_icon.setObjectName("overallIcon")
        self.overall_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overall_icon.setFixedSize(38, 38)
        overall_layout.addWidget(self.overall_icon)
        overall_copy = QVBoxLayout()
        overall_copy.setSpacing(3)
        self.overall_title = QLabel()
        self.overall_title.setObjectName("overallTitle")
        self.overall_detail = QLabel()
        self.overall_detail.setProperty("muted", True)
        self.overall_detail.setWordWrap(True)
        overall_copy.addWidget(self.overall_title)
        overall_copy.addWidget(self.overall_detail)
        overall_layout.addLayout(overall_copy, 1)
        self.automation_state_label = StatusPill()
        self.automation_state_label.setFixedHeight(28)
        overall_layout.addWidget(self.automation_state_label)
        root.addWidget(overall)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(16)
        for provider_id, state in self.controller.states.items():
            card = ProviderCard(state)
            card.action_button.clicked.connect(
                lambda checked=False, key=provider_id: self.start_bootstrap(key)
            )
            card.sync_button.clicked.connect(
                lambda checked=False, key=provider_id: self.start_usage_sync(key)
            )
            self.provider_cards[provider_id] = card
            provider_row.addWidget(card, 1)
        self.schedule_card = ScheduleCard()
        self.schedule_card.manage_button.clicked.connect(lambda: self.show_page(1))
        provider_row.addWidget(self.schedule_card, 1)
        root.addLayout(provider_row)

        weekly = QFrame()
        weekly.setObjectName("weeklySafetyCard")
        weekly_layout = QHBoxLayout(weekly)
        weekly_layout.setContentsMargins(20, 14, 20, 14)
        weekly_layout.setSpacing(14)
        weekly_title = QLabel("Weekly allowance")
        weekly_title.setObjectName("assuranceTitle")
        weekly_layout.addWidget(weekly_title)
        self.weekly_detail = QLabel()
        self.weekly_detail.setProperty("muted", True)
        weekly_layout.addWidget(self.weekly_detail, 1)
        self.weekly_status = StatusPill()
        weekly_layout.addWidget(self.weekly_status)
        root.addWidget(weekly)

        self.last_action_label = QLabel()
        self.last_action_label.setObjectName("lastAction")
        self.last_action_label.setWordWrap(True)
        root.addWidget(self.last_action_label)

        root.addWidget(self._build_assurance_strip(), 0)
        # A little slack above and more below reads as deliberate spacing
        # rather than content stranded at the top of a tall window.
        root.addStretch(1)
        return page

    def _build_assurance_strip(self) -> QWidget:
        """The three promises a new user has to understand, kept to one glance."""
        strip = QFrame()
        strip.setObjectName("assuranceStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(22, 15, 22, 15)
        layout.setSpacing(26)
        points = (
            ("Safe and private", "Codex keeps its own sign-in. UsageLoop never reads credentials."),
            ("Local and lightweight", "Countdowns move on this PC with no provider polling."),
            ("Quota protected", "Weekly allowance is checked, and unclear requests are never retried."),
        )
        for title, body in points:
            column = QVBoxLayout()
            column.setSpacing(4)
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
            "Choose how UsageLoop keeps your reset clock running.",
        )

        automation_card, automation_layout = make_surface_card(
            "Automation",
            "UsageLoop sends no window-start request while this is off.",
        )
        automation_row = QFrame()
        automation_row.setObjectName("settingRow")
        automation_row_layout = QHBoxLayout(automation_row)
        automation_row_layout.setContentsMargins(14, 12, 14, 12)
        automation_copy = QVBoxLayout()
        self.automation_title_label = QLabel("Keep my 5-hour windows ready")
        self.automation_title_label.setObjectName("secondaryMetric")
        automation_hint = QLabel(
            "Uses one minimal guarded request only when a new window needs to start."
        )
        automation_hint.setProperty("muted", True)
        automation_hint.setWordWrap(True)
        automation_copy.addWidget(self.automation_title_label)
        automation_copy.addWidget(automation_hint)
        automation_row_layout.addLayout(automation_copy, 1)
        self.automation_toggle = ToggleSwitch()
        self.automation_toggle.setChecked(self.controller.settings.automation_enabled)
        automation_row_layout.addWidget(self.automation_toggle)
        automation_layout.addWidget(automation_row)
        root.addWidget(automation_card)

        schedule_card, schedule_layout = make_surface_card(
            "Schedule",
            "Continuous keeps every five-hour window moving. Daily waits for your chosen local time after a window ends.",
        )
        schedule_row = QFrame()
        schedule_row.setObjectName("settingRow")
        schedule_row_layout = QHBoxLayout(schedule_row)
        schedule_row_layout.setContentsMargins(14, 12, 14, 12)
        mode_copy = QVBoxLayout()
        mode_title = QLabel("Window start mode")
        mode_title.setObjectName("secondaryMetric")
        self.schedule_explanation = QLabel()
        self.schedule_explanation.setProperty("muted", True)
        self.schedule_explanation.setWordWrap(True)
        mode_copy.addWidget(mode_title)
        mode_copy.addWidget(self.schedule_explanation)
        schedule_row_layout.addLayout(mode_copy, 1)
        self.schedule_mode = QComboBox()
        self.schedule_mode.setObjectName("scheduleModePicker")
        self.schedule_mode.addItem("Continuous", "continuous")
        self.schedule_mode.addItem("Daily start time", "daily")
        index = self.schedule_mode.findData(self.controller.settings.schedule_mode)
        self.schedule_mode.setCurrentIndex(max(0, index))
        schedule_row_layout.addWidget(self.schedule_mode)
        schedule_layout.addWidget(schedule_row)

        self.daily_time_row = QFrame()
        self.daily_time_row.setObjectName("settingRow")
        time_layout = QHBoxLayout(self.daily_time_row)
        time_layout.setContentsMargins(14, 12, 14, 12)
        time_copy = QVBoxLayout()
        time_title = QLabel("Daily start time")
        time_title.setObjectName("secondaryMetric")
        time_hint = QLabel("Local time on this PC. Missed starts catch up once after wake or restart.")
        time_hint.setProperty("muted", True)
        time_hint.setWordWrap(True)
        time_copy.addWidget(time_title)
        time_copy.addWidget(time_hint)
        time_layout.addLayout(time_copy, 1)
        self.daily_time = QTimeEdit(
            QTime(
                self.controller.settings.daily_start_hour,
                self.controller.settings.daily_start_minute,
            )
        )
        self.daily_time.setObjectName("dailyStartTime")
        self.daily_time.setDisplayFormat("h:mm AP")
        self.daily_time.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.daily_time.setToolTip("Type a local time or use the keyboard arrow keys")
        time_layout.addWidget(self.daily_time)
        schedule_layout.addWidget(self.daily_time_row)
        root.addWidget(schedule_card)

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
        self.startup_toggle = ToggleSwitch()
        self.startup_toggle.setChecked(bool(self.startup_manager.is_enabled()))
        row_layout.addWidget(self.startup_toggle)
        startup_layout.addWidget(startup_row)
        root.addWidget(startup_card)

        technical_card, technical_layout = make_surface_card(
            "Advanced",
            "Compatibility and diagnostic information for troubleshooting.",
        )
        technical = Disclosure("Technical details")
        self.diagnostic_text = QLabel()
        self.diagnostic_text.setObjectName("diagnosticValue")
        self.diagnostic_text.setWordWrap(True)
        self.diagnostic_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        technical.add_widget(self.diagnostic_text)
        privacy_note = QLabel(
            "Safe to paste into a bug report. No prompts, responses, credentials, or "
            "account identity are ever recorded."
        )
        privacy_note.setProperty("muted", True)
        privacy_note.setWordWrap(True)
        technical.add_widget(privacy_note)
        self.copy_summary_button = QPushButton("Copy this summary")
        self.copy_summary_button.clicked.connect(self._copy_diagnostics)
        technical.add_widget(self.copy_summary_button)
        technical_layout.addWidget(technical)
        root.addWidget(technical_card)

        self.update_panel = UpdatePanel(
            updater, confirm_install=confirm_install, parent=self
        )
        self.update_panel.installer_launched.connect(self._exit_for_update)
        root.insertWidget(root.count() - 1, self.update_panel)
        root.addStretch()
        return page

    def _build_about(self) -> QWidget:
        page, root = self._page(f"About {PRODUCT.display_name}", PRODUCT.tagline)
        about, about_layout = make_surface_card(PRODUCT.display_name)
        version = QLabel(f"Version {PRODUCT.version} \u00b7 Windows \u00b7 MIT licensed")
        version.setObjectName("secondaryMetric")
        about_layout.addWidget(version)
        description = QLabel(
            "Codex subscription windows start when Codex receives a real request. UsageLoop watches "
            "the official local Codex app-server, keeps the countdown on this PC, and can start the "
            "next five-hour clock after rollover with one minimal guarded request. It reports success "
            "only after Codex exposes a fixed new reset time. It does not grant extra quota or change "
            "your subscription limits."
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
            "Codex support",
            "The app-server observation and guarded start path have both been proven on a real subscription account.",
        )
        support_status = QLabel(
            "UsageLoop is an independent open-source project. It is not affiliated with, endorsed by, "
            "or sponsored by OpenAI. Codex behavior can evolve, so compatibility is checked by capability "
            "instead of trusting a version string."
        )
        support_status.setProperty("muted", True)
        support_status.setWordWrap(True)
        support_layout.addWidget(support_status)
        root.addWidget(support)

        privacy, privacy_layout = make_surface_card(
            "Privacy and safety",
            "Codex keeps its own sign-in. UsageLoop never reads tokens, credentials, conversations, "
            "or account identifiers.",
        )
        boundaries = QLabel(
            "\u2022 Codex automation is off until you switch it on\n"
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
                card.update_state(
                    state, now=current, automation_enabled=enabled
                )
                card.action_button.setVisible(
                    provider_id == "codex"
                    and enabled
                    and state.installed
                    and state.automation_supported
                    and state.reset_at is None
                    and state.status != "Starting"
                )
        codex = self.controller.states.get("codex")
        if codex is not None:
            presented = present_provider_state(
                codex, now=current, automation_enabled=enabled
            )
            self.schedule_card.update_schedule(
                self.controller.settings, codex, now=current
            )
            if not codex.installed:
                self.overall_icon.setText("!")
                self.overall_title.setText("Codex needs attention")
                self.overall_detail.setText(
                    "Install and sign in to Codex before UsageLoop can observe a reset clock."
                )
            elif codex.status == "Needs attention":
                self.overall_icon.setText("!")
                self.overall_title.setText("UsageLoop stopped safely")
                self.overall_detail.setText(
                    "No request was retried. Open Technical details for the reason."
                )
            elif codex.reset_at is not None and codex.reset_at > current:
                self.overall_icon.setText("✓")
                self.overall_title.setText("Your Codex reset clock is running")
                self.overall_detail.setText(
                    "The countdown is local. UsageLoop will follow your schedule when this window ends."
                )
            else:
                self.overall_icon.setText("○")
                self.overall_title.setText("Waiting for a verified Codex window")
                self.overall_detail.setText(
                    "Sync usage to read the current state. Starting a first window always needs your approval."
                )

            if codex.weekly_used_percent is None:
                self.weekly_detail.setText("Not available in the last read-only sync")
                self.weekly_status.set_status("NOT CHECKED", "neutral")
            else:
                try:
                    weekly_reset = (
                        time.strftime(
                            "%a %I:%M %p", time.localtime(codex.weekly_reset_at)
                        ).replace(" 0", " ")
                        if codex.weekly_reset_at is not None
                        else "reset time unavailable"
                    )
                except (OSError, OverflowError, ValueError):
                    weekly_reset = "reset time unavailable"
                self.weekly_detail.setText(
                    f"{codex.weekly_used_percent:g}% used · resets {weekly_reset}"
                )
                weekly_safe = codex.weekly_used_percent < WEEKLY_PROTECTION_PERCENT
                self.weekly_status.set_status(
                    "SAFE" if weekly_safe else "PROTECTED",
                    "success" if weekly_safe else "warning",
                )
            self.last_action_label.setText(presented.action)
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

    def start_bootstrap(self, provider_id: str) -> None:
        if provider_id in self.active_operations or not self.confirm_bootstrap():
            return
        self._start_operation(provider_id, "bootstrap")

    def start_usage_sync(self, provider_id: str) -> None:
        if provider_id in self.active_operations:
            return
        provider = self.providers.get(provider_id)
        state = self.controller.states.get(provider_id)
        card = self.provider_cards.get(provider_id)
        if provider is None or state is None or not state.installed:
            if card is not None:
                card.set_sync_state("unavailable")
            return
        self.active_operations[provider_id] = "sync"
        if card is not None:
            card.set_sync_state("syncing")
        worker = OperationWorker(
            provider_id,
            lambda: provider.sync_usage(current_state=state),
        )
        worker.signals.completed.connect(self._operation_completed)
        worker.signals.failed.connect(self._operation_failed)
        self.thread_pool.start(worker)

    def _start_operation(self, provider_id: str, action: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return
        state = self.controller.states[provider_id]
        self.controller.update_provider_state(
            replace(state, status="Starting", detail="Checking with the provider safely.")
        )
        self.active_operations[provider_id] = action
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
        action = self.active_operations.pop(provider_id, None)
        if isinstance(result, CompatibilityResult):
            self.controller.apply_compatibility(provider_id, result)
        elif isinstance(result, ProviderOperationResult):
            self.controller.update_provider_state(result.state)
            if action == "sync":
                self.provider_cards[provider_id].set_sync_state(
                    "updated" if result.outcome == "SYNC_UPDATED" else "inconclusive"
                )
        else:
            self._operation_failed(provider_id, "unsupported_result")
            return
        self.refresh_clock()

    def _operation_failed(self, provider_id: str, category: str) -> None:
        action = self.active_operations.pop(provider_id, None)
        if action == "sync":
            card = self.provider_cards.get(provider_id)
            if card is not None:
                card.set_sync_state("inconclusive")
            self.refresh_clock()
            return
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

    def _schedule_mode_changed(self) -> None:
        mode = self.schedule_mode.currentData()
        self.controller.set_schedule_mode(str(mode))
        self.refresh_clock()

    def _daily_time_changed(self, selected: QTime) -> None:
        self.controller.set_daily_start_time(selected.hour(), selected.minute())
        self.refresh_clock()

    def _update_diagnostics(self, *, now: float) -> None:
        daily = self.controller.settings.schedule_mode == "daily"
        self.daily_time_row.setVisible(daily)
        self.schedule_explanation.setText(
            "Wait for the selected local time after a window ends."
            if daily
            else "Start the next window after the current one ends."
        )
        self.diagnostic_text.setText(
            technical_summary(self.controller.states, self.controller.settings)
        )

    def _copy_diagnostics(self, _checked: bool = False) -> None:
        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("Windows clipboard is unavailable")
            clipboard.setText(self.diagnostic_text.text())
        except Exception:
            self.copy_summary_button.setText("Copy failed")
            QMessageBox.warning(
                self,
                "Summary not copied",
                "Windows did not allow UsageLoop to copy the diagnostic summary. Try again after closing other clipboard tools.",
            )
            return
        self.copy_summary_button.setText("Copied")
        QTimer.singleShot(
            2_000,
            lambda: self.copy_summary_button.setText("Copy this summary"),
        )

    def _exit_for_update(self) -> None:
        application = QApplication.instance()
        if application is None:
            self.update_panel._operation_failed(
                "install",
                "The installer started, but UsageLoop could not close automatically. Close it before continuing setup.",
            )
            return
        try:
            QTimer.singleShot(200, application.quit)
        except Exception:
            self.update_panel._operation_failed(
                "install",
                "The installer started, but UsageLoop could not close automatically. Close it before continuing setup.",
            )
            return
        self.force_close = True

    def _confirm_enable(self) -> bool:
        answer = QMessageBox.question(
            self,
            "Keep your Codex reset clock running?",
            f"{PRODUCT.display_name} will use your signed-in Codex client only after its safety checks "
            "pass. It checks weekly allowance, sends one minimal request, then verifies the reset.\n\n"
            "Starting a window uses a small amount of your plan. An action "
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
