"""PySide6 desktop shell, navigation, background work, and tray lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
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
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .app_controller import ApplicationController
from .activity import RecentStartsDialog
from .app_state import (
    AppSettings,
    ProviderViewState,
    automation_decision,
    format_countdown,
)
from .chain import WEEKLY_PROTECTION_PERCENT
from .branding import make_app_icon, render_mark
from .product import PRODUCT
from .provider_runtime import ProviderOperationResult
from .providers import CompatibilityResult
from .schedule import DAILY, WEEKLY, schedule_summary
from .diagnostics import technical_summary
from .ui_components import (
    Disclosure,
    ElidingLabel,
    ProviderCard,
    ScheduleCard,
    StatusPill,
    TimeEntry,
    ToggleSwitch,
    daily_schedule_example,
    make_surface_card,
    present_provider_state,
    weekly_schedule_preview_details,
    pause_until_text,
    automatic_start_history_copy,
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


def tray_tooltip_text(
    settings: AppSettings,
    state: ProviderViewState | None,
    *,
    now: float,
    persistence_error: str | None = None,
) -> str:
    """Summarize cached dashboard state without exposing implementation labels."""
    prefix = f"{PRODUCT.display_name} · "
    if persistence_error is not None:
        return prefix + "Needs attention"
    if settings.pause_active(now):
        return prefix + f"Paused until {pause_until_text(settings.automation_paused_until)}"
    if state is not None and (not state.installed or state.status == "Needs attention"):
        return prefix + "Needs attention"
    if not settings.automation_enabled:
        return prefix + "Automation off"
    if state is None:
        return prefix + "waiting for Codex status"
    if state.reset_at is not None and state.reset_at > now:
        return prefix + f"{format_countdown(state.reset_at, now)} left"
    if state.status in {"Starting", "Checking"}:
        return prefix + "checking Codex status"
    decision = automation_decision(
        settings.automation_enabled,
        state,
        now=now,
        compatible_runtime_identity=settings.compatible_runtime_identities.get(
            state.provider_id
        ),
        checked_runtime_identity=settings.checked_runtime_identities.get(
            state.provider_id
        ),
        schedule_mode=settings.schedule_mode,
        daily_hour=settings.daily_start_hour,
        daily_minute=settings.daily_start_minute,
        weekly_times=settings.weekly_start_times,
    )
    if decision.action == "ROLLOVER":
        return prefix + "Next window due now"
    if decision.action == "BOOTSTRAP":
        return prefix + "Waiting for first window"
    if settings.schedule_mode in {DAILY, WEEKLY} and state.reset_at is not None:
        try:
            schedule = schedule_summary(
                settings.schedule_mode,
                boundary_reset_at=state.reset_at,
                now=now,
                hour=settings.daily_start_hour,
                minute=settings.daily_start_minute,
                weekly_times=settings.weekly_start_times,
            )
            if schedule.next_action_at is not None and schedule.next_action_at > now:
                target = datetime.fromtimestamp(schedule.next_action_at)
                today = datetime.fromtimestamp(now).date()
                if target.date() == today:
                    day = "today"
                elif target.date() == today + timedelta(days=1):
                    day = "tomorrow"
                else:
                    day = target.strftime("%a")
                clock = target.strftime("%I:%M %p").lstrip("0")
                return prefix + f"next start {day} at {clock}"
        except (OSError, OverflowError, ValueError):
            return prefix + "Status unavailable"
    if state.status == "Waiting":
        return prefix + "waiting for Codex status"
    return prefix + "Status unavailable"


class MainWindow(QMainWindow):
    PAGE_NAMES = ("Dashboard", "Settings", "About")
    tray_tooltip_changed = Signal(str)
    presentation_changed = Signal()

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
        self.current_tray_tooltip = PRODUCT.display_name
        self._last_history_signature = None

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
        self.dashboard_automation_toggle.toggled.connect(
            self._automation_toggled
        )
        self.schedule_card.pause_button.clicked.connect(self.toggle_temporary_pause)
        self.startup_toggle.toggled.connect(self._startup_toggled)
        self.schedule_mode.currentIndexChanged.connect(self._schedule_mode_changed)
        self.daily_time.timeChanged.connect(self._daily_time_changed)
        for day_index, editor in enumerate(self.weekly_day_times):
            editor.timeChanged.connect(
                lambda selected, index=day_index: self._weekly_day_time_changed(
                    index, selected
                )
            )
        self.apply_weekdays.clicked.connect(
            lambda: self._apply_weekly_group(
                range(5),
                self.weekday_quick_time.time(),
                self.apply_weekdays,
                "Apply Mon–Fri",
            )
        )
        self.apply_weekend.clicked.connect(
            lambda: self._apply_weekly_group(
                range(5, 7),
                self.weekend_quick_time.time(),
                self.apply_weekend,
                "Apply Sat–Sun",
            )
        )
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

    def _page(
        self, title: str, intro: str
    ) -> tuple[QScrollArea, QVBoxLayout, QLabel]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setMaximumWidth(1260)
        content.setMinimumWidth(560)
        root = QVBoxLayout(content)
        root.setContentsMargins(34, 24, 34, 28)
        root.setSpacing(14)
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
        return scroll, root, intro_label

    def _build_dashboard(self) -> QWidget:
        page, root, self.dashboard_intro = self._page(
            "Your Codex reset clock",
            "Codex starts a new 5-hour reset clock when you use it. UsageLoop can start the next one "
            "for you while you’re away, so the clock is already counting down when you come back.",
        )
        self.dashboard_clarifier = QLabel(
            "UsageLoop does not add quota or bypass limits. It uses one minimal request to start "
            "your normal next window."
        )
        self.dashboard_clarifier.setProperty("muted", True)
        self.dashboard_clarifier.setWordWrap(True)
        root.addWidget(self.dashboard_clarifier)
        overall = QFrame()
        overall.setObjectName("overallStatusCard")
        overall_layout = QHBoxLayout(overall)
        overall_layout.setContentsMargins(22, 17, 22, 17)
        overall_layout.setSpacing(16)
        self.overall_icon = QLabel("✓")
        self.overall_icon.setObjectName("overallIcon")
        self.overall_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overall_icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.overall_icon.setFixedSize(30, 30)
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
        self.recheck_dashboard_button = QPushButton("Recheck Codex compatibility")
        self.recheck_dashboard_button.setObjectName("secondaryButton")
        self.recheck_dashboard_button.clicked.connect(self.recheck_compatibility)
        overall_copy.addWidget(self.recheck_dashboard_button, 0, Qt.AlignmentFlag.AlignLeft)
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
            card.update_button.clicked.connect(
                lambda checked=False: self._check_updates_for_model_support()
            )
            self.provider_cards[provider_id] = card
            provider_row.addWidget(card, 1)
        self.schedule_card = ScheduleCard()
        self.dashboard_automation_toggle = self.schedule_card.automation_toggle
        self.schedule_card.manage_button.clicked.connect(lambda: self.show_page(1))
        self.last_action_label = self.schedule_card.last_action_label
        self.schedule_card.history_button.clicked.connect(self.show_recent_starts)
        provider_row.addWidget(self.schedule_card, 1)
        root.addLayout(provider_row)

        weekly = QFrame()
        weekly.setObjectName("weeklySafetyCard")
        weekly_layout = QVBoxLayout(weekly)
        weekly_layout.setContentsMargins(20, 14, 20, 14)
        weekly_layout.setSpacing(8)
        weekly_header = QHBoxLayout()
        weekly_title = QLabel("Weekly allowance")
        weekly_title.setObjectName("providerName")
        weekly_header.addWidget(weekly_title)
        weekly_header.addStretch()
        self.weekly_status = StatusPill()
        weekly_header.addWidget(self.weekly_status)
        weekly_layout.addLayout(weekly_header)
        weekly_metrics = QHBoxLayout()
        weekly_metrics.setSpacing(14)
        self.weekly_value = QLabel()
        self.weekly_value.setObjectName("weeklyValue")
        weekly_metrics.addWidget(self.weekly_value)
        self.weekly_detail = QLabel()
        self.weekly_detail.setProperty("muted", True)
        weekly_metrics.addWidget(self.weekly_detail, 1, Qt.AlignmentFlag.AlignVCenter)
        weekly_layout.addLayout(weekly_metrics)
        self.weekly_bar = QProgressBar()
        self.weekly_bar.setObjectName("weeklyBar")
        self.weekly_bar.setRange(0, 100)
        self.weekly_bar.setTextVisible(False)
        self.weekly_bar.setFixedHeight(6)
        weekly_layout.addWidget(self.weekly_bar)
        root.addWidget(weekly)

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
            ("Local and lightweight", "Countdowns move on this PC with no Codex polling."),
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
        page, root, _intro = self._page(
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
            "Sends one tiny request to Codex, only when a new window needs to start."
        )
        automation_hint.setProperty("muted", True)
        automation_hint.setWordWrap(True)
        automation_copy.addWidget(self.automation_title_label)
        automation_copy.addWidget(automation_hint)
        automation_row_layout.addLayout(automation_copy, 1)
        self.automation_toggle = ToggleSwitch()
        self.automation_toggle.setAccessibleName("Keep my 5-hour windows ready")
        self.automation_toggle.setChecked(self.controller.settings.automation_enabled)
        automation_row_layout.addWidget(self.automation_toggle)
        automation_layout.addWidget(automation_row)

        schedule_card, schedule_layout = make_surface_card(
            "Schedule",
            "Choose continuous rollover, one daily start time, or a weekly routine.",
        )
        schedule_row = QFrame()
        schedule_row.setObjectName("settingRow")
        schedule_row_layout = QHBoxLayout(schedule_row)
        schedule_row_layout.setContentsMargins(14, 12, 14, 12)
        mode_copy = QVBoxLayout()
        self.schedule_mode_title = QLabel("When should the next 5-hour window start?")
        self.schedule_mode_title.setObjectName("secondaryMetric")
        self.schedule_explanation = QLabel()
        self.schedule_explanation.setProperty("muted", True)
        self.schedule_explanation.setWordWrap(True)
        mode_copy.addWidget(self.schedule_mode_title)
        mode_copy.addWidget(self.schedule_explanation)
        schedule_row_layout.addLayout(mode_copy, 1)
        self.schedule_mode = QComboBox()
        self.schedule_mode.setObjectName("scheduleModePicker")
        self.schedule_mode.setAccessibleName("When should the next 5-hour window start?")
        self.schedule_mode.addItem("Continuous", "continuous")
        self.schedule_mode.addItem("Once each day", "daily")
        self.schedule_mode.addItem("Weekly routine", "weekly")
        index = self.schedule_mode.findData(self.controller.settings.schedule_mode)
        self.schedule_mode.setCurrentIndex(max(0, index))
        schedule_row_layout.addWidget(self.schedule_mode)
        schedule_layout.addWidget(schedule_row)

        self.daily_time_row = QFrame()
        self.daily_time_row.setObjectName("settingRow")
        time_layout = QHBoxLayout(self.daily_time_row)
        time_layout.setContentsMargins(14, 12, 14, 12)
        time_copy = QVBoxLayout()
        self.daily_time_title = QLabel("Start time")
        self.daily_time_title.setObjectName("secondaryMetric")
        time_hint = QLabel("Local time on this PC. Missed starts catch up once after wake or restart.")
        time_hint.setProperty("muted", True)
        time_hint.setWordWrap(True)
        time_copy.addWidget(self.daily_time_title)
        time_copy.addWidget(time_hint)
        self.daily_schedule_example = QLabel()
        self.daily_schedule_example.setProperty("muted", True)
        self.daily_schedule_example.setWordWrap(True)
        time_copy.addWidget(self.daily_schedule_example)
        time_layout.addLayout(time_copy, 1)
        self.daily_time = self._weekly_time_editor(
            "dailyStartTime",
            QTime(
                self.controller.settings.daily_start_hour,
                self.controller.settings.daily_start_minute,
            ),
        )
        self.daily_time.setAccessibleName("Once each day start time")
        time_layout.addWidget(self.daily_time)
        schedule_layout.addWidget(self.daily_time_row)

        self.weekly_schedule_panel = QFrame()
        self.weekly_schedule_panel.setObjectName("weeklySchedulePanel")
        weekly_layout = QVBoxLayout(self.weekly_schedule_panel)
        weekly_layout.setContentsMargins(18, 16, 18, 16)
        weekly_layout.setSpacing(14)
        weekly_intro = QLabel(
            "Choose when you want your first window to start each day. UsageLoop keeps windows rolling during the day, then pauses overnight so tomorrow's start stays on schedule."
        )
        weekly_intro.setProperty("muted", True)
        weekly_intro.setWordWrap(True)
        weekly_layout.addWidget(weekly_intro)

        self.weekday_quick_time = self._weekly_time_editor("weekdayQuickTime")
        self.weekend_quick_time = self._weekly_time_editor("weekendQuickTime")
        self.weekday_quick_time.setAccessibleName("Weekdays start time")
        self.weekend_quick_time.setAccessibleName("Weekends start time")
        self.apply_weekdays = QPushButton("Apply Mon–Fri")
        self.apply_weekdays.setObjectName("secondaryButton")
        self.apply_weekdays.setToolTip("Apply this time to Monday through Friday.")
        self.apply_weekend = QPushButton("Apply Sat–Sun")
        self.apply_weekend.setObjectName("secondaryButton")
        self.apply_weekend.setToolTip("Apply this time to Saturday and Sunday.")
        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(12)
        self.weekly_group_cards: list[QFrame] = []
        for title, days, editor, button in (
            ("Weekdays", "Monday through Friday", self.weekday_quick_time, self.apply_weekdays),
            ("Weekends", "Saturday and Sunday", self.weekend_quick_time, self.apply_weekend),
        ):
            group = QFrame()
            group.setObjectName("weeklyGroupCard")
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(16, 13, 16, 14)
            group_layout.setSpacing(8)
            title_label = QLabel(title)
            title_label.setObjectName("weeklyGroupTitle")
            subtitle = QLabel(days)
            subtitle.setProperty("muted", True)
            controls = QHBoxLayout()
            controls.setSpacing(8)
            controls.addWidget(editor, 1)
            controls.addWidget(button)
            group_layout.addWidget(title_label)
            group_layout.addWidget(subtitle)
            group_layout.addLayout(controls)
            self.weekly_group_cards.append(group)
            quick_row.addWidget(group, 1)
        weekly_layout.addLayout(quick_row)

        self.weekly_custom_days = Disclosure("Customize individual days")
        day_grid = QGridLayout()
        day_grid.setContentsMargins(0, 2, 0, 0)
        day_grid.setHorizontalSpacing(12)
        day_grid.setVerticalSpacing(8)
        self.weekly_day_times: list[TimeEntry] = []
        self.weekly_day_rows: list[QFrame] = []
        for index, day_name in enumerate(
            ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        ):
            editor = self._weekly_time_editor(f"weekly{day_name}Time")
            editor.setAccessibleName(f"{day_name} start time")
            self.weekly_day_times.append(editor)
            row = QFrame()
            row.setObjectName("weeklyDayRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(10)
            day_label = QLabel(day_name)
            day_label.setObjectName("weeklyDayLabel")
            row_layout.addWidget(day_label, 1)
            row_layout.addWidget(editor)
            self.weekly_day_rows.append(row)
            day_grid.addWidget(row, index // 2, index % 2)
        self.weekly_custom_days.body_layout.addLayout(day_grid)
        weekly_layout.addWidget(self.weekly_custom_days)

        self.weekly_preview_card = QFrame()
        self.weekly_preview_card.setObjectName("weeklyPreviewCard")
        preview_layout = QVBoxLayout(self.weekly_preview_card)
        preview_layout.setContentsMargins(16, 13, 16, 14)
        preview_layout.setSpacing(10)
        preview_header = QHBoxLayout()
        preview_title = QLabel("Next routine")
        preview_title.setObjectName("weeklyGroupTitle")
        self.weekly_preview_day = QLabel()
        self.weekly_preview_day.setObjectName("previewTag")
        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        preview_header.addWidget(self.weekly_preview_day)
        preview_layout.addLayout(preview_header)
        preview_metrics = QHBoxLayout()
        preview_metrics.setSpacing(12)
        (
            self.weekly_preview_first_title,
            self.weekly_preview_first_value,
        ) = self._weekly_preview_metric(preview_metrics, "First start")
        (
            self.weekly_preview_reset_title,
            self.weekly_preview_reset_value,
        ) = self._weekly_preview_metric(preview_metrics, "Next reset")
        (
            self.weekly_preview_pause_title,
            self.weekly_preview_pause_value,
        ) = self._weekly_preview_metric(preview_metrics, "Overnight pause")
        preview_layout.addLayout(preview_metrics)
        preview_note = QLabel("Times are approximate and shown in your local time.")
        preview_note.setProperty("muted", True)
        preview_layout.addWidget(preview_note)
        weekly_layout.addWidget(self.weekly_preview_card)
        schedule_layout.addWidget(self.weekly_schedule_panel)
        self._sync_weekly_editor()

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
        self.startup_toggle.setAccessibleName(f"Start {PRODUCT.display_name} with Windows")
        self.startup_toggle.setChecked(bool(self.startup_manager.is_enabled()))
        row_layout.addWidget(self.startup_toggle)
        startup_layout.addWidget(startup_row)

        technical_card, technical_layout = make_surface_card(
            "Codex connection",
            "After updating or signing back in to Codex, recheck here. This reads usage and supported models without sending a model request.",
        )
        self.compatibility_status = QLabel()
        self.compatibility_status.setWordWrap(True)
        technical_layout.addWidget(self.compatibility_status)
        self.recheck_button = QPushButton("Recheck Codex compatibility")
        self.recheck_button.setObjectName("secondaryButton")
        self.recheck_button.clicked.connect(self.recheck_compatibility)
        technical_layout.addWidget(self.recheck_button)
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

        self.update_panel = UpdatePanel(
            updater, confirm_install=confirm_install, parent=self
        )
        self.update_panel.installer_launched.connect(self._exit_for_update)
        self.update_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        technical_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )

        self.settings_top_row = QWidget()
        top_layout = QHBoxLayout(self.settings_top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(14)
        top_layout.addWidget(automation_card, 1)
        top_layout.addWidget(startup_card, 1)
        root.addWidget(self.settings_top_row)
        root.addWidget(schedule_card)

        self.settings_bottom_row = QWidget()
        bottom_layout = QHBoxLayout(self.settings_bottom_row)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(14)
        bottom_layout.addWidget(self.update_panel, 1, Qt.AlignmentFlag.AlignTop)
        bottom_layout.addWidget(technical_card, 1, Qt.AlignmentFlag.AlignTop)
        root.addWidget(self.settings_bottom_row)
        root.addStretch()
        return page

    def _build_about(self) -> QWidget:
        page, root, _intro = self._page(
            f"About {PRODUCT.display_name}",
            "A small local-first app that keeps your normal Codex reset clock moving.",
        )
        about = QFrame()
        about.setObjectName("aboutHero")
        about_layout = QHBoxLayout(about)
        about_layout.setContentsMargins(24, 22, 24, 22)
        about_layout.setSpacing(26)
        story = QVBoxLayout()
        story.setSpacing(12)
        product_title = QLabel(PRODUCT.display_name)
        product_title.setObjectName("aboutProductTitle")
        story.addWidget(product_title)
        version = QLabel(f"Version {PRODUCT.version} \u00b7 Windows \u00b7 MIT licensed")
        version.setObjectName("secondaryMetric")
        story.addWidget(version)
        self.about_description = QLabel(
            "Codex gives you usage in 5-hour windows. A new window begins when you actually use Codex. "
            "If the previous window ends while you’re away, the next reset clock normally waits until "
            "you come back and use Codex again. UsageLoop can start that next window with one minimal "
            "request, so its reset clock can already be counting down before you return. UsageLoop does "
            "not increase your quota or bypass limits."
        )
        self.about_description.setProperty("muted", True)
        self.about_description.setWordWrap(True)
        self.about_description.setMinimumWidth(320)
        self.about_description.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        story.addWidget(self.about_description)

        self.about_steps: list[QFrame] = []
        for number, title, detail in (
            ("1", "Codex gives you 5-hour windows", "The clock begins when Codex is used."),
            ("2", "UsageLoop starts the next one", "One minimal request can begin it while you’re away."),
            ("3", "No shortcuts or extra quota", "Your existing limits and weekly protection still apply."),
        ):
            step = QFrame()
            step.setObjectName("aboutStep")
            step_layout = QHBoxLayout(step)
            step_layout.setContentsMargins(12, 9, 12, 9)
            step_layout.setSpacing(12)
            badge = QLabel(number)
            badge.setObjectName("aboutStepNumber")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(28, 28)
            step_copy = QVBoxLayout()
            step_copy.setSpacing(2)
            step_title = QLabel(title)
            step_title.setObjectName("aboutStepTitle")
            step_detail = QLabel(detail)
            step_detail.setProperty("muted", True)
            step_detail.setWordWrap(True)
            step_copy.addWidget(step_title)
            step_copy.addWidget(step_detail)
            step_layout.addWidget(badge)
            step_layout.addLayout(step_copy, 1)
            story.addWidget(step)
            self.about_steps.append(step)
        about_layout.addLayout(story, 3)

        action_panel = QFrame()
        action_panel.setObjectName("aboutActions")
        actions = QVBoxLayout(action_panel)
        actions.setContentsMargins(16, 15, 16, 15)
        actions.setSpacing(8)
        self.about_action_title = QLabel("Project links")
        self.about_action_title.setObjectName("weeklyGroupTitle")
        actions.addWidget(self.about_action_title)
        self.about_link_buttons: dict[str, QPushButton] = {}
        for label, url in (
            ("View source", PRODUCT.github_url),
            ("Releases", PRODUCT.releases_url),
            ("Report a problem", PRODUCT.bug_report_url),
            ("Request a feature", PRODUCT.feature_request_url),
        ):
            button = QPushButton(label)
            button.setObjectName("linkButton")
            button.clicked.connect(
                lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target))
            )
            self.about_link_buttons[label] = button
            actions.addWidget(button)

        star_title = QLabel("Finding UsageLoop useful?")
        star_title.setObjectName("secondaryMetric")
        actions.addSpacing(6)
        actions.addWidget(star_title)
        self.star_description = QLabel(
            "A GitHub star helps other Codex users discover the project and lets us know it’s worth "
            "continuing to improve."
        )
        self.star_description.setProperty("muted", True)
        self.star_description.setWordWrap(True)
        actions.addWidget(self.star_description)
        self.star_button = QPushButton("★ Open GitHub to star UsageLoop")
        self.star_button.setObjectName("primaryButton")
        self.star_button.clicked.connect(
            lambda checked=False: QDesktopServices.openUrl(QUrl(PRODUCT.github_url))
        )
        actions.addWidget(self.star_button)
        actions.addStretch()
        about_layout.addWidget(action_panel, 2)
        root.addWidget(about)

        support, support_layout = make_surface_card(
            "Codex support",
            "UsageLoop checks that the installed Codex client still supports every action it needs before automation runs.",
        )
        support_status = QLabel(
            "UsageLoop is an independent open-source project. It is not affiliated with, endorsed by, "
            "or sponsored by OpenAI. Codex behavior can evolve, so compatibility is checked by capability "
            "instead of trusting a version string."
        )
        support_status.setProperty("muted", True)
        support_status.setWordWrap(True)
        support_layout.addWidget(support_status)
        privacy, privacy_layout = make_surface_card(
            "Privacy and safety",
            "Codex keeps its own sign-in. UsageLoop never reads tokens, credentials, conversations, "
            "or account identifiers.",
        )
        boundaries = QLabel(
            "\u2022 Codex automation is off until you switch it on\n"
            "\u2022 Update checks run only when you press the button, and never touch your plan\n"
            "\u2022 Countdowns are calculated locally and cause no Codex traffic\n"
            "\u2022 A request whose outcome is unclear is never retried automatically\n"
            "\u2022 Weekly limits are respected before any action is considered"
        )
        boundaries.setObjectName("secondaryMetric")
        boundaries.setWordWrap(True)
        privacy_layout.addWidget(boundaries)
        support_privacy = QHBoxLayout()
        support_privacy.setSpacing(14)
        support_privacy.addWidget(support, 1)
        support_privacy.addWidget(privacy, 1)
        root.addLayout(support_privacy)
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

    def _check_updates_for_model_support(self) -> None:
        self.show_page(1)
        self.update_panel.start_model_support_check()

    def refresh_clock(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        enabled = self.controller.settings.automation_enabled
        paused = self.controller.settings.pause_active(current)
        self._sync_automation_toggles(enabled)
        self.automation_state_label.set_status(
            "PAUSED · ON" if paused else ("ON" if enabled else "OFF"),
            "info" if paused else ("success" if enabled else "neutral"),
        )
        pause_button = self.schedule_card.pause_button
        pause_button.setVisible(paused or self.controller.settings.schedule_mode in {DAILY, WEEKLY})
        pause_button.setText("Resume automation" if paused else "Pause until tomorrow")
        pause_button.setEnabled(enabled and not self.active_operations)
        target = self.controller.settings.tomorrow_first_start(current)
        pause_button.setToolTip(
            "Wait for the current operation to finish." if self.active_operations else
            ("Return to your saved schedule." if paused else
             f"Pause until tomorrow's first start: {pause_until_text(target)}" if target is not None else "")
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
                    and not paused
                    and state.installed
                    and state.automation_supported
                    and state.reset_at is None
                    and state.status not in {"Starting", "Checking"}
                )
        codex = self.controller.states.get("codex")
        if codex is not None:
            checking = self.active_operations.get("codex") == "probe"
            needs_check = (
                not codex.automation_supported
                or self.controller.settings.compatible_runtime_identities.get("codex") != codex.runtime_identity
            )
            self.recheck_dashboard_button.setVisible(codex.installed and needs_check)
            for button in (self.recheck_button, self.recheck_dashboard_button):
                button.setEnabled(codex.installed and not self.active_operations and self.controller.persistence_error is None)
                button.setText("Checking Codex…" if checking else "Recheck Codex compatibility")
            self.compatibility_status.setText(
                "Checking usage and models. Allow about 30 seconds. No model request is sent."
                if checking else
                "Codex is not detected. Install and open Codex, then reopen UsageLoop."
                if not codex.installed else
                codex.detail if needs_check else
                "Compatibility confirmed. Your saved schedule and safety checks still apply."
            )
            self.schedule_card.update_schedule(
                self.controller.settings, codex, now=current
            )
            if self.controller.persistence_error is not None:
                self._set_overall_icon("!", "warning")
                self.overall_title.setText("UsageLoop needs attention")
                self.overall_detail.setText(
                    "A local setting could not be saved, so automatic starts are paused. See Technical details."
                )
            elif paused:
                self._set_overall_icon("○", "info")
                self.overall_title.setText("Automation is temporarily paused")
                self.overall_detail.setText(
                    f"Still enabled. Resumes {pause_until_text(self.controller.settings.automation_paused_until)}. Your routine is unchanged."
                )
            elif not codex.installed:
                self._set_overall_icon("!", "warning")
                self.overall_title.setText("Codex needs attention")
                self.overall_detail.setText(
                    "Install and sign in to Codex before UsageLoop can observe a reset clock."
                )
            elif checking:
                self._set_overall_icon("○", "info")
                self.overall_title.setText("Checking the Codex connection")
                self.overall_detail.setText("Reading usage and supported models. No model request is sent. Allow about 30 seconds.")
            elif codex.status == "Needs attention" or (
                needs_check
                and self.controller.settings.checked_runtime_identities.get("codex") == codex.runtime_identity
            ):
                self._set_overall_icon("!", "warning")
                self.overall_title.setText("UsageLoop stopped safely")
                self.overall_detail.setText(
                    "Use Recheck Codex compatibility below after fixing the connection. No model request is sent."
                    if needs_check else
                    "No request was retried. Open Settings > Codex connection > Technical details for the reason."
                )
            elif not enabled:
                self._set_overall_icon("○", "info")
                self.overall_title.setText("Automation is off")
                self.overall_detail.setText("No automatic starts. Your saved routine will be used when you enable automation.")
            elif codex.reset_at is not None and codex.reset_at > current:
                self._set_overall_icon("✓", "success")
                self.overall_title.setText("Everything is set")
                self.overall_detail.setText(
                    "The countdown runs locally. UsageLoop will follow your schedule when this window ends."
                )
            else:
                self._set_overall_icon("○", "info")
                self.overall_title.setText("Waiting for a verified Codex window")
                self.overall_detail.setText(
                    "Sync usage to read the current state. Starting a first window always needs your approval."
                )

            if codex.weekly_used_percent is None:
                self.weekly_value.setText("—")
                self.weekly_detail.setText("Not available in the last read-only sync")
                self.weekly_bar.setValue(0)
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
                self.weekly_value.setText(f"{codex.weekly_used_percent:g}%")
                self.weekly_bar.setValue(int(codex.weekly_used_percent))
                weekly_safe = codex.weekly_used_percent < WEEKLY_PROTECTION_PERCENT
                self.weekly_status.set_status(
                    "SAFE" if weekly_safe else "PROTECTED",
                    "success" if weekly_safe else "warning",
                )
            self._refresh_last_automatic_start(now=current)
            self.schedule_card.fit_wrapped_text()
        self._update_diagnostics(now=current)
        tooltip = tray_tooltip_text(
            self.controller.settings,
            codex,
            now=current,
            persistence_error=self.controller.persistence_error,
        )
        if tooltip != self.current_tray_tooltip:
            self.current_tray_tooltip = tooltip
            self.tray_tooltip_changed.emit(tooltip)
        self.presentation_changed.emit()

    def show_recent_starts(self) -> None:
        if not hasattr(self, "recent_starts_dialog"):
            history = getattr(self.providers.get("codex"), "history", None)
            self.recent_starts_dialog = RecentStartsDialog(history, self)
        self.recent_starts_dialog.refresh()
        self.recent_starts_dialog.show()
        self.recent_starts_dialog.raise_()
        self.recent_starts_dialog.activateWindow()

    def _refresh_last_automatic_start(self, *, now: float) -> None:
        history = getattr(self.providers.get("codex"), "history", None)
        if history is None:
            self.last_action_label.setText("Last automatic start: None yet")
            return
        try:
            stat = history.path.stat()
        except FileNotFoundError:
            self._last_history_signature = None
            self.last_action_label.setText("Last automatic start: None yet")
            return
        except OSError:
            self._last_history_signature = None
            self.last_action_label.setText("Last automatic start: History unavailable")
            return
        # Clock ticks need no JSONL reread unless history or the displayed day changed.
        signature = (stat.st_mtime_ns, stat.st_size, datetime.fromtimestamp(now).date())
        if signature != self._last_history_signature:
            self.last_action_label.setText(automatic_start_history_copy(history, now=now))
            self._last_history_signature = signature

    def toggle_temporary_pause(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        # A running worker may already be sending. Never promise a pause mid-flight.
        if self.active_operations or not self.controller.settings.automation_enabled:
            return
        if self.controller.settings.pause_active(current):
            saved = self.controller.resume_automation()
        elif self.controller.settings.tomorrow_first_start(current) is not None:
            saved = self.controller.pause_until_tomorrow(now=current)
        else:
            return
        if not saved:
            self._show_persistence_warning()
        # Resume/expiry only removes the gate. The normal timer evaluates safety.
        self.refresh_clock(now=current)

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
        if self.controller.settings.pause_active(time.time()):
            return
        if provider_id in self.active_operations or not self.confirm_bootstrap():
            return
        if self.controller.settings.pause_active(time.time()):
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

    def recheck_compatibility(self) -> None:
        """One explicit read-only probe; never enable automation or run a turn."""
        if self.active_operations or self.controller.persistence_error is not None:
            return
        self.controller.refresh_local_states()
        state = self.controller.states.get("codex")
        if state is None or not state.installed:
            self.refresh_clock()
            return
        self._start_operation("codex", "probe")

    def _start_operation(self, provider_id: str, action: str) -> None:
        provider = self.providers.get(provider_id)
        if provider is None:
            return
        state = self.controller.states[provider_id]
        saved = self.controller.update_provider_state(
            replace(state, status="Checking" if action == "probe" else "Starting", detail="Checking Codex safely.")
        )
        if not saved:
            self.refresh_clock()
            return
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
            if action == "sync":
                saved = self.controller.update_provider_state(result.state)
            else:
                self.controller.apply_operation_result(
                    result.outcome, result.state, now=time.time()
                )
            if action == "sync":
                self.provider_cards[provider_id].set_sync_state(
                    "updated"
                    if saved and result.outcome == "SYNC_UPDATED"
                    else "inconclusive"
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
            self._sync_automation_toggles(False)
            return
        if not self.controller.set_automation_enabled(enabled):
            self._sync_automation_toggles(
                self.controller.settings.automation_enabled
            )
            self._show_persistence_warning()
            self.refresh_clock()
            return
        self.refresh_clock()
        if enabled:
            self.evaluate_automation()

    def _sync_automation_toggles(self, enabled: bool) -> None:
        for toggle in (
            self.automation_toggle,
            self.dashboard_automation_toggle,
        ):
            toggle.blockSignals(True)
            toggle.setChecked(enabled)
            toggle.blockSignals(False)

    def _set_overall_icon(self, glyph: str, tone: str) -> None:
        self.overall_icon.setText(glyph)
        self.overall_icon.setProperty("tone", tone)
        self.overall_icon.style().unpolish(self.overall_icon)
        self.overall_icon.style().polish(self.overall_icon)

    def _startup_toggled(self, enabled: bool) -> None:
        previous = self.controller.settings.start_with_windows
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
        if not self.controller.set_start_with_windows(enabled):
            try:
                self.startup_manager.set_enabled(previous)
            except OSError:
                pass
            self.startup_toggle.blockSignals(True)
            self.startup_toggle.setChecked(previous)
            self.startup_toggle.blockSignals(False)
            self._show_persistence_warning()
            self.refresh_clock()

    def _schedule_mode_changed(self) -> None:
        mode = self.schedule_mode.currentData()
        if not self.controller.set_schedule_mode(str(mode)):
            self.schedule_mode.blockSignals(True)
            index = self.schedule_mode.findData(
                self.controller.settings.schedule_mode
            )
            if index >= 0:
                self.schedule_mode.setCurrentIndex(index)
            self.schedule_mode.blockSignals(False)
            self._show_persistence_warning()
        self._sync_weekly_editor()
        self.refresh_clock()

    def _daily_time_changed(self, selected: QTime) -> None:
        if not self.controller.set_daily_start_time(
            selected.hour(), selected.minute()
        ):
            self.daily_time.blockSignals(True)
            self.daily_time.setTime(
                QTime(
                    self.controller.settings.daily_start_hour,
                    self.controller.settings.daily_start_minute,
                )
            )
            self.daily_time.blockSignals(False)
            self._show_persistence_warning()
        self.refresh_clock()

    @staticmethod
    def _weekly_time_editor(
        object_name: str, initial_time: QTime | None = None
    ) -> TimeEntry:
        editor = TimeEntry(initial_time)
        editor.setObjectName(object_name)
        return editor

    @staticmethod
    def _weekly_preview_metric(
        layout: QHBoxLayout, title: str
    ) -> tuple[QLabel, QLabel]:
        metric = QFrame()
        metric.setObjectName("weeklyPreviewMetric")
        metric.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        metric_layout = QVBoxLayout(metric)
        metric_layout.setContentsMargins(12, 9, 12, 10)
        metric_layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("weeklyPreviewLabel")
        value_label = QLabel()
        value_label.setObjectName("weeklyPreviewValue")
        metric_layout.addWidget(title_label)
        metric_layout.addWidget(value_label)
        layout.addWidget(metric, 1)
        return title_label, value_label

    def _weekly_values(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (editor.time().hour(), editor.time().minute())
            for editor in self.weekly_day_times
        )

    def _sync_weekly_editor(self) -> None:
        values = self.controller.settings.weekly_start_times or (
            (
                self.controller.settings.daily_start_hour,
                self.controller.settings.daily_start_minute,
            ),
        ) * 7
        for editor, (hour, minute) in zip(self.weekly_day_times, values):
            editor.blockSignals(True)
            editor.setTime(QTime(hour, minute))
            editor.blockSignals(False)
        self.weekday_quick_time.setTime(QTime(*values[0]))
        self.weekend_quick_time.setTime(QTime(*values[5]))

    def _weekly_day_time_changed(self, index: int, selected: QTime) -> None:
        values = list(self._weekly_values())
        values[index] = (selected.hour(), selected.minute())
        if not self.controller.set_weekly_start_times(values):
            self._sync_weekly_editor()
            self._show_persistence_warning()
        else:
            self._sync_weekly_editor()
        self.refresh_clock()

    def _apply_weekly_group(
        self,
        indices: range,
        selected: QTime,
        button: QPushButton,
        normal_text: str,
    ) -> None:
        values = list(self._weekly_values())
        for index in indices:
            values[index] = (selected.hour(), selected.minute())
        if not self.controller.set_weekly_start_times(values):
            self._sync_weekly_editor()
            self._show_persistence_warning()
        else:
            self._sync_weekly_editor()
            button.setText("Applied")
            QTimer.singleShot(
                1_500,
                lambda: button.setText(normal_text),
            )
        self.refresh_clock()

    def _show_persistence_warning(self) -> None:
        QMessageBox.warning(
            self,
            "Setting not saved",
            "UsageLoop could not save that setting, so it was changed back. Automatic starts are paused until local state can be written again.",
        )

    def _update_diagnostics(self, *, now: float) -> None:
        daily = self.controller.settings.schedule_mode == "daily"
        weekly = self.controller.settings.schedule_mode == WEEKLY
        self.daily_time_row.setVisible(daily)
        self.weekly_schedule_panel.setVisible(weekly)
        self.schedule_explanation.setText(
            "Wait for the selected local time after the current window ends."
            if daily
            else (
                "Use a first-start time for each day, then keep windows rolling until it is time to pause for tomorrow."
                if weekly
                else "Start the next window after the current reset and safety check."
            )
        )
        codex = self.controller.states.get("codex")
        self.daily_schedule_example.setText(
            daily_schedule_example(
                codex.reset_at if codex is not None else None,
                hour=self.controller.settings.daily_start_hour,
                minute=self.controller.settings.daily_start_minute,
                now=now,
            )
        )
        if self.controller.settings.weekly_start_times is not None:
            preview = weekly_schedule_preview_details(
                self.controller.settings.weekly_start_times,
                now=now,
            )
            self.weekly_preview_day.setText(preview.day.upper())
            self.weekly_preview_first_value.setText(preview.first_start)
            self.weekly_preview_reset_value.setText(preview.next_reset)
            self.weekly_preview_pause_value.setText(preview.pause_start)
        self.diagnostic_text.setText(
            technical_summary(
                self.controller.states,
                self.controller.settings,
                persistence_error=self.controller.persistence_error is not None,
            )
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
        self.tray.setToolTip(window.current_tray_tooltip)
        self.window.tray_tooltip_changed.connect(self.tray.setToolTip)
        menu = QMenu()
        self.status_action = menu.addAction(window.current_tray_tooltip)
        self.status_action.setEnabled(False)
        self.pause_action = menu.addAction("Pause until tomorrow's first start")
        self.pause_action.triggered.connect(window.toggle_temporary_pause)
        menu.addSeparator()
        open_action = menu.addAction(f"Open {PRODUCT.display_name}")
        open_action.triggered.connect(self.restore_window)
        self.history_action = menu.addAction("Recent starts")
        self.history_action.triggered.connect(window.show_recent_starts)
        menu.addSeparator()
        quit_action = menu.addAction(f"Quit {PRODUCT.display_name}")
        quit_action.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        menu.aboutToShow.connect(self.refresh_menu)
        self.window.presentation_changed.connect(self.refresh_menu)
        self.refresh_menu()
        self.tray.activated.connect(self._tray_activated)
        self.window.hide_on_close = QSystemTrayIcon.isSystemTrayAvailable()
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.tray.hide)
        self.tray.show()

    def refresh_menu(self, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        settings = self.window.controller.settings
        paused = settings.pause_active(current)
        self.status_action.setText(tray_tooltip_text(
            settings, self.window.controller.states.get("codex"), now=current,
            persistence_error=self.window.controller.persistence_error,
        ))
        target = settings.tomorrow_first_start(current)
        self.pause_action.setVisible(paused or target is not None)
        self.pause_action.setText(
            "Resume automation" if paused else
            f"Pause until {pause_until_text(target)}" if target is not None else
            "Pause until tomorrow's first start"
        )
        self.pause_action.setEnabled(settings.automation_enabled and not self.window.active_operations)

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
