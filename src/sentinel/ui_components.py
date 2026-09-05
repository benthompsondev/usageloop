"""Reusable presentation widgets for the UsageLoop shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
import re
import time

from PySide6.QtCore import QPointF, QSize, QTime, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QStyle, QStyleOptionSpinBox, QTimeEdit, QVBoxLayout,
    QWidget,
)

from .app_state import AppSettings, ProviderViewState, format_countdown
from .history import SafeHistory, HistoryStateError
from .providers import LIGHTWEIGHT_MODEL_UNAVAILABLE_DETAIL
from .schedule import (
    DAILY,
    FIVE_HOUR_WINDOW_SECONDS,
    WEEKLY,
    next_weekly_start_after,
    schedule_summary,
)
from .ui_theme import TOKENS


STALE_AFTER_SECONDS = 6 * 60 * 60
CHAIN_OUTCOME_COPY = {
    "ALREADY_ANCHORED": "Current window was already running",
    "ANCHOR_VERIFIED": "Successful",
    "EVIDENCE_TOO_WEAK": "Waiting for clearer Codex status",
    "ROLLOVER_BOUNDARY_UNKNOWN": "Waiting for a confirmed reset time",
    "TRIGGER_NOT_SENT": "No request was sent",
    "RESET_BUFFER": "Waiting briefly after the reset",
    "NOT_ELIGIBLE": "No automatic start needed yet",
    "WEEKLY_UNAVAILABLE": "Weekly limit could not be checked · No request sent",
    "WEEKLY_EXHAUSTED": "Weekly limit protected your quota · No request sent",
    "BOOTSTRAP_USAGE_UNSUITABLE": "First window was not safe to start",
    "ATTEMPT_ALREADY_RECORDED": "Start already handled safely",
    "BOOTSTRAP_COOLDOWN": "Waiting before another check",
    "VERIFICATION_UNAVAILABLE": "Start could not be confirmed · No retry",
    "ANCHOR_NOT_VERIFIED": "Start outcome unclear · No retry",
    "CONSENT_REQUIRED": "Waiting for your approval",
    "DRY_RUN": "Dry run only · No request sent",
}


class ToggleSwitch(QCheckBox):
    """A compact, keyboard-accessible switch with no platform-default chrome."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("toggleSwitch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = self.rect().adjusted(1, 1, -1, -1)
        border = TOKENS.accent if self.isChecked() or self.hasFocus() else TOKENS.border_strong
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(
            QColor(TOKENS.accent_deep if self.isChecked() else TOKENS.surface_sunken)
        )
        painter.drawRoundedRect(track, 11, 11)
        diameter = 16
        x = self.width() - diameter - 4 if self.isChecked() else 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F4FFFB" if self.isChecked() else TOKENS.text_muted))
        painter.drawEllipse(x, 4, diameter, diameter)


class TimeEntry(QTimeEdit):
    """A themed QTimeEdit that keeps Qt's native section-aware interaction."""

    def __init__(
        self,
        value: QTime | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(value or QTime(0, 0), parent)
        self.setDisplayFormat("h:mm AP")
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.setAccelerated(True)
        self.setWrapping(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCorrectionMode(
            QAbstractSpinBox.CorrectionMode.CorrectToNearestValue
        )
        self.setToolTip(
            "Select the hour, minute, or AM/PM. Type a value or use the arrow controls."
        )
        self.setAccessibleDescription(
            "Local time. Select the hour, minute, or AM/PM, then type or use arrow keys."
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        color = (
            TOKENS.text_faint
            if not self.isEnabled()
            else TOKENS.accent
            if self.hasFocus()
            else TOKENS.text_muted
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(color), 1.5))
        for control, points_up in (
            (QStyle.SubControl.SC_SpinBoxUp, True),
            (QStyle.SubControl.SC_SpinBoxDown, False),
        ):
            rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                control,
                self,
            )
            center = rect.center()
            direction = 1 if points_up else -1
            painter.drawLine(
                QPointF(center.x() - 3.5, center.y() + 2 * direction),
                QPointF(center.x(), center.y() - 2 * direction),
            )
            painter.drawLine(
                QPointF(center.x(), center.y() - 2 * direction),
                QPointF(center.x() + 3.5, center.y() + 2 * direction),
            )


@dataclass(frozen=True)
class ProviderPresentation:
    status: str
    tone: str
    headline: str
    reset: str
    detail: str
    verified: str
    usage: str
    weekly: str
    action: str


@dataclass(frozen=True)
class WeeklySchedulePreview:
    """Scannable local-time values for the Weekly routine preview."""

    day: str
    first_start: str
    next_reset: str
    pause_start: str


def present_provider_state(
    state: ProviderViewState, *, now: float, automation_enabled: bool = False
) -> ProviderPresentation:
    """Map internal Codex evidence onto the five consumer-facing clock states."""
    usage = (
        f"5-hour window  {state.used_percent:g}% used"
        if state.used_percent is not None else "5-hour window  Not checked"
    )
    weekly = (
        f"Weekly allowance  {state.weekly_used_percent:g}% used"
        if state.weekly_used_percent is not None else "Weekly allowance  Not available"
    )
    if state.weekly_reset_at is not None:
        weekly += f"  ·  {_reset_copy(state.weekly_reset_at, now=now)}"
    action = _automatic_action_copy(state, now=now)
    if not state.installed:
        return ProviderPresentation(
            "NEEDS ATTENTION", "error", "Codex is not installed", "No reset information",
            "Install and sign in to Codex, then reopen UsageLoop.", "No local status yet",
            usage, weekly, action,
        )

    reset = _reset_copy(state.reset_at, now=now)
    if state.last_verified_at is not None:
        verified = f"Last synced {_friendly_time(state.last_verified_at, now=now)}"
    elif state.usage_checked_at is not None:
        verified = f"Last synced {_friendly_time(state.usage_checked_at, now=now)}"
    else:
        verified = "Not checked yet"

    if state.status == "Needs attention":
        if state.detail == LIGHTWEIGHT_MODEL_UNAVAILABLE_DETAIL:
            return ProviderPresentation(
                "AUTOMATIC STARTS PAUSED", "error",
                "No supported lightweight Codex model", reset,
                state.detail, verified, usage, weekly, action,
            )
        return ProviderPresentation(
            "NEEDS ATTENTION", "error", "A safe check needs attention", reset,
            "Nothing was retried. Diagnostics has the technical reason.",
            verified, usage, weekly, action,
        )
    if state.status == "Starting":
        return ProviderPresentation(
            "STARTING NEXT WINDOW", "info", "Starting the next reset clock", reset,
            "One minimal Codex request is in progress. It will not be retried automatically.",
            verified, usage, weekly, action,
        )
    if state.last_verified_at is not None and now - state.last_verified_at > STALE_AFTER_SECONDS:
        return ProviderPresentation(
            "NEEDS ATTENTION", "warning", format_countdown(state.reset_at, now), reset,
            "This cached reading is older than usual. Diagnostics has more detail.",
            verified, usage, weekly, action,
        )
    if state.reset_at is None:
        return ProviderPresentation(
            "WAITING FOR RESET" if automation_enabled else "AUTOMATION OFF",
            "info" if automation_enabled else "neutral",
            "No reset clock verified yet", reset,
            (
                "UsageLoop will check Codex and start the first window only after every safety gate passes."
                if automation_enabled
                else "Turn on UsageLoop when you want it to start and maintain the Codex reset clock."
            ),
            verified, usage, weekly, action,
        )
    if state.status == "Ready":
        return ProviderPresentation(
            "CLOCK RUNNING" if automation_enabled else "AUTOMATION OFF",
            "success" if automation_enabled else "neutral",
            format_countdown(state.reset_at, now), reset,
            "Codex confirms this reset time. The countdown runs locally with no Codex traffic.",
            verified, usage, weekly, action,
        )
    return ProviderPresentation(
        "WAITING FOR RESET" if automation_enabled else "AUTOMATION OFF",
        "info" if automation_enabled else "neutral", format_countdown(state.reset_at, now), reset,
        (
            "The previous clock ended. UsageLoop will start the next one after the reset buffer and safety checks."
            if automation_enabled else
            "The previous clock ended. Turn UsageLoop on to keep the next one ready."
        ),
        verified, usage, weekly, action,
    )


class StatusPill(QLabel):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("statusPill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_status(self, text: str, tone: str) -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.style().unpolish(self)
        self.style().polish(self)


class ProviderCard(QFrame):
    def __init__(self, state: ProviderViewState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("providerCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 21, 24, 21)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.name_label = QLabel("Current 5-hour window")
        self.name_label.setObjectName("providerName")
        self.status_label = StatusPill()
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.status_label)
        layout.addLayout(header)
        self.countdown_label = QLabel()
        self.countdown_label.setObjectName("countdown")
        layout.addWidget(self.countdown_label)
        self.reset_label = QLabel()
        self.reset_label.setObjectName("secondaryMetric")
        layout.addWidget(self.reset_label)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("detail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        self.update_button = QPushButton("Check for updates")
        self.update_button.setObjectName("secondaryButton")
        self.update_button.setVisible(False)
        layout.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignLeft)

        rule = QFrame()
        rule.setObjectName("cardRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setFixedHeight(1)
        layout.addSpacing(3)
        layout.addWidget(rule)
        self.metadata_label = QLabel()
        self.metadata_label.setProperty("muted", True)
        layout.addWidget(self.metadata_label)
        self.usage_label = QLabel()
        self.usage_label.setObjectName("metricLabel")
        layout.addWidget(self.usage_label)
        self.usage_bar = _metric_bar("usageBar")
        layout.addWidget(self.usage_bar)
        layout.addSpacing(2)
        sync_row = QHBoxLayout()
        self.sync_button = QPushButton("Sync usage")
        self.sync_button.setObjectName("secondaryButton")
        self.sync_status = QLabel("")
        self.sync_status.setProperty("muted", True)
        sync_row.addWidget(self.sync_button, 0)
        sync_row.addWidget(self.sync_status, 1)
        layout.addLayout(sync_row)
        self.action_button = QPushButton("Start my first window now")
        self.action_button.setObjectName("primaryButton")
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.update_state(state, now=time.time(), automation_enabled=False)

    def update_state(
        self, state: ProviderViewState, *, now: float, automation_enabled: bool = False
    ) -> None:
        presented = present_provider_state(state, now=now, automation_enabled=automation_enabled)
        self.status_label.set_status(presented.status, presented.tone)
        if self.property("tone") != presented.tone:
            self.setProperty("tone", presented.tone)
            self.style().unpolish(self)
            self.style().polish(self)
        self.countdown_label.setText(presented.headline)
        self.reset_label.setText(presented.reset)
        self.detail_label.setText(presented.detail)
        self.update_button.setVisible(
            state.detail == LIGHTWEIGHT_MODEL_UNAVAILABLE_DETAIL
        )
        self.metadata_label.setText(presented.verified)
        self.usage_label.setText(presented.usage)
        self.usage_bar.setValue(int(state.used_percent or 0))

    def set_sync_state(self, state: str) -> None:
        messages = {
            "idle": "",
            "syncing": "Syncing…",
            "updated": "Updated just now",
            "inconclusive": "Couldn’t confirm Codex usage",
            "unavailable": "Codex not available",
        }
        self.sync_status.setText(messages[state])
        self.sync_button.setEnabled(state != "syncing")


class ScheduleCard(QFrame):
    """Consumer-facing summary of when the next window can start."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("scheduleCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(280)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 21, 24, 21)
        layout.setSpacing(9)

        header = QHBoxLayout()
        name = QLabel("Automation")
        name.setObjectName("providerName")
        self.automation_toggle = ToggleSwitch()
        self.automation_toggle.setAccessibleName("Keep my 5-hour windows ready")
        self.automation_toggle.setToolTip("Turn UsageLoop automation on or off")
        header.addWidget(name)
        header.addStretch()
        header.addWidget(self.automation_toggle)
        layout.addLayout(header)

        self.mode_label = QLabel()
        self.mode_label.setObjectName("scheduleMode")
        layout.addWidget(self.mode_label)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("detail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        layout.addStretch(1)

        next_title = QLabel("NEXT ACTION")
        next_title.setObjectName("eyebrow")
        layout.addWidget(next_title)
        self.next_label = QLabel()
        self.next_label.setObjectName("scheduleNext")
        self.next_label.setWordWrap(True)
        layout.addWidget(self.next_label)
        self.pause_button = QPushButton("Pause until tomorrow")
        self.pause_button.setObjectName("secondaryButton")
        layout.addWidget(self.pause_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.manage_button = QPushButton("Manage schedule")
        self.manage_button.setObjectName("secondaryButton")
        actions = QHBoxLayout()
        actions.addWidget(self.manage_button)
        self.history_button = QPushButton("Recent starts")
        self.history_button.setObjectName("secondaryButton")
        self.history_button.setToolTip("See which window starts were confirmed, not sent, or left uncertain.")
        actions.addWidget(self.history_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.last_action_label = QLabel()
        self.last_action_label.setObjectName("lastAction")
        self.last_action_label.setWordWrap(True)
        layout.addWidget(self.last_action_label)

    def fit_wrapped_text(self) -> None:
        margins = self.layout().contentsMargins()
        width = max(1, self.width() - margins.left() - margins.right())
        for label in (self.detail_label, self.next_label, self.last_action_label):
            label.setMinimumHeight(max(0, label.heightForWidth(width)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit_wrapped_text()

    def update_schedule(
        self,
        settings: AppSettings,
        state: ProviderViewState,
        *,
        now: float,
    ) -> None:
        enabled = settings.automation_enabled
        if settings.schedule_mode == DAILY:
            time_text = datetime(2000, 1, 1, settings.daily_start_hour, settings.daily_start_minute).strftime(
                "%I:%M %p"
            ).lstrip("0")
            self.mode_label.setText(f"At {time_text} each day")
            self.detail_label.setText(
                "Starts only after the current window ends and this local time arrives."
            )
        elif settings.schedule_mode == WEEKLY:
            self.mode_label.setText("Weekly routine")
            self.detail_label.setText(
                "Keeps windows rolling during the day, then pauses so tomorrow's first start stays on schedule."
            )
        else:
            self.mode_label.setText("As soon as reset passes")
            self.detail_label.setText(
                "Starts the next window after the current reset and safety buffer."
            )

        if not enabled:
            self.next_label.setText("No automatic requests while automation is off")
            return

        if settings.pause_active(now):
            self.detail_label.setText("Automation stays on.\nYour routine is unchanged.")
            self.next_label.setText(f"Resumes {pause_until_text(settings.automation_paused_until)}")
            return

        if state.reset_at is None:
            self.next_label.setText("First window starts only when you ask")
            return
        summary = schedule_summary(
            settings.schedule_mode,
            boundary_reset_at=state.reset_at,
            now=now,
            hour=settings.daily_start_hour,
            minute=settings.daily_start_minute,
            weekly_times=settings.weekly_start_times,
        )
        if settings.schedule_mode == WEEKLY and summary.phase == "overnight_pause":
            target = _schedule_time(summary.next_action_at, now=now)
            target = target[0].lower() + target[1:]
            self.next_label.setText(f"Overnight pause · first start {target}")
        elif settings.schedule_mode == WEEKLY and summary.phase == "active_window":
            self.next_label.setText(
                "Current window stays active until it resets"
            )
        elif summary.due and summary.phase == "scheduled_first_start":
            self.next_label.setText("Scheduled first start is due now")
        elif summary.due and summary.phase == "continuous_rollover":
            self.next_label.setText("Daytime rollover safety checks are due now")
        elif summary.due:
            self.next_label.setText("Safety checks are due now")
        elif summary.next_action_at is not None:
            self.next_label.setText(_schedule_time(summary.next_action_at, now=now))


def pause_until_text(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")


def automatic_start_history_copy(history: SafeHistory | None, *, now: float) -> str:
    """Use existing rollover records, never a quota-observation timestamp."""
    if history is None:
        return "Last automatic start: None yet"
    try:
        attempts = [item for item in history.trigger_attempts() if item.mode == "rollover"]
    except (OSError, HistoryStateError):
        return "Last automatic start: History unavailable"
    if not attempts:
        return "Last automatic start: None yet"
    attempt = attempts[-1]
    outcome = {
        "reserved": "Preparing",
        "launch_attempted": "Starting",
        "request_possibly_sent": "Outcome unclear · No retry",
        "verified": "Successful",
        "failed_recoverable": "Stopped before sending",
        "failed_guarded": "Not verified · No retry",
    }[attempt.state]
    when = _schedule_time(attempt.updated_at, now=now)
    return f"Last automatic start: {when} · {outcome}"


def _schedule_time(
    timestamp: float, *, now: float, timezone: tzinfo | None = None
) -> str:
    try:
        local = _local_datetime(timestamp, timezone)
        current = _local_datetime(now, timezone)
    except (OSError, OverflowError, ValueError):
        return "After the current reset"
    time_text = local.strftime("%I:%M %p").lstrip("0")
    if local.date() == current.date():
        return f"Today at {time_text}"
    if local.date() == current.date() + timedelta(days=1):
        return f"Tomorrow at {time_text}"
    return local.strftime("%a, %b %d at %I:%M %p").replace(" 0", " ")


def daily_schedule_example(
    reset_at: int | float | None,
    *,
    hour: int,
    minute: int,
    now: float | None = None,
    timezone: tzinfo | None = None,
) -> str:
    """Explain the selected daily schedule using the current cached reset."""
    current = time.time() if now is None else float(now)
    selected = (
        datetime(2000, 1, 1, hour, minute)
        .strftime("%I:%M %p")
        .lstrip("0")
    )
    if reset_at is None:
        return (
            "After the current window ends, UsageLoop will start the next one "
            f"at {selected}."
        )
    try:
        summary = schedule_summary(
            DAILY,
            boundary_reset_at=float(reset_at),
            now=current,
            hour=hour,
            minute=minute,
            timezone=timezone,
        )
    except (OSError, OverflowError, ValueError):
        return (
            "After the current window ends, UsageLoop will start the next one "
            f"at {selected}."
        )
    reset = _inline_schedule_time(float(reset_at), now=current, timezone=timezone)
    if reset is None:
        return (
            "After the current window ends, UsageLoop will start the next one "
            f"at {selected}."
        )
    tense = "previous window ended" if reset_at <= current else "current window ends"
    if summary.due:
        next_action = "UsageLoop is due to start the next one now."
    else:
        scheduled = _inline_schedule_time(
            summary.next_action_at, now=current, timezone=timezone
        )
        if scheduled is None:
            return (
                "After the current window ends, UsageLoop will start the next one "
                f"at {selected}."
            )
        next_action = f"UsageLoop will start the next one {scheduled}."
    return (
        f"Your {tense} {reset}. "
        f"{next_action}"
    )


def weekly_schedule_preview(
    weekly_times: tuple[tuple[int, int], ...],
    *,
    now: float | None = None,
    timezone: tzinfo | None = None,
) -> str:
    """Describe the next weekly target and its derived overnight pause."""
    details = weekly_schedule_preview_details(
        weekly_times, now=now, timezone=timezone
    )
    return (
        f"{details.day}: first start around {details.first_start} · "
        f"next reset around {details.next_reset}\n"
        f"Overnight pause begins around {details.pause_start}."
    )


def weekly_schedule_preview_details(
    weekly_times: tuple[tuple[int, int], ...],
    *,
    now: float | None = None,
    timezone: tzinfo | None = None,
) -> WeeklySchedulePreview:
    """Return the same Weekly preview as separate presentation fields."""
    current = time.time() if now is None else float(now)
    target = next_weekly_start_after(current, weekly_times, timezone=timezone)

    def clock(timestamp: float) -> str:
        return _local_datetime(timestamp, timezone).strftime("%I:%M %p").lstrip("0")

    target_local = _local_datetime(target, timezone)
    current_local = _local_datetime(current, timezone)
    if target_local.date() == current_local.date():
        day = "Today"
    elif target_local.date() == current_local.date() + timedelta(days=1):
        day = "Tomorrow"
    else:
        day = target_local.strftime("%A")
    return WeeklySchedulePreview(
        day=day,
        first_start=clock(target),
        next_reset=clock(target + FIVE_HOUR_WINDOW_SECONDS),
        pause_start=clock(target - FIVE_HOUR_WINDOW_SECONDS),
    )


def _local_datetime(timestamp: float, timezone: tzinfo | None) -> datetime:
    if timezone is None:
        return datetime.fromtimestamp(timestamp).astimezone()
    return datetime.fromtimestamp(timestamp, timezone)


def _inline_schedule_time(
    timestamp: float | None, *, now: float, timezone: tzinfo | None
) -> str | None:
    if timestamp is None:
        return None
    label = _schedule_time(timestamp, now=now, timezone=timezone)
    if label == "After the current reset":
        return None
    if label.startswith(("Today", "Tomorrow")):
        return label[0].lower() + label[1:]
    return label


def _automatic_action_copy(state: ProviderViewState, *, now: float) -> str:
    action = state.last_action
    if not action:
        return "Last automatic start: None yet"
    outcome = re.sub(r"[^A-Z0-9]+", "_", action.upper()).strip("_")
    if outcome == "ANCHOR_VERIFIED":
        return "Last automatic start: Successful"
    if outcome in CHAIN_OUTCOME_COPY:
        return f"Last automatic start: {CHAIN_OUTCOME_COPY[outcome]}"
    normalized = action.casefold()
    if "unclear" in normalized or "not verified" in normalized:
        return "Last automatic start: Outcome unclear · No retry"
    if "before" in normalized and "sent" in normalized:
        return "Last automatic start: Stopped before sending"
    if "preparing" in normalized or "starting" in normalized:
        return "Automatic start in progress"
    if "verified" in normalized or "successful" in normalized:
        return "Last automatic start: Successful"
    return "Last automatic start: Action recorded · See Technical details"


def _metric_bar(name: str) -> QProgressBar:
    bar = QProgressBar()
    bar.setObjectName(name)
    bar.setRange(0, 100)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    return bar


def make_surface_card(
    title: str, description: str | None = None, *, parent: QWidget | None = None
) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(parent)
    card.setObjectName("surfaceCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("sectionTitle")
    layout.addWidget(title_label)
    if description:
        description_label = QLabel(description)
        description_label.setProperty("muted", True)
        description_label.setWordWrap(True)
        layout.addWidget(description_label)
    return card, layout


def _reset_copy(reset_at: int | None, *, now: float) -> str:
    if reset_at is None:
        return "Reset time not verified"
    try:
        local = datetime.fromtimestamp(reset_at).astimezone()
        current = datetime.fromtimestamp(now).astimezone()
    except (OSError, OverflowError, ValueError):
        return "Reset time unavailable"
    if local.date() == current.date():
        return f"Resets at {local.strftime('%I:%M %p').lstrip('0')}"
    return f"Resets {local.strftime('%a at %I:%M %p').replace(' 0', ' ')}"


def _friendly_time(timestamp: float | None, *, now: float) -> str:
    if timestamp is None:
        return "at an unknown time"
    try:
        local = datetime.fromtimestamp(timestamp).astimezone()
        current = datetime.fromtimestamp(now).astimezone()
    except (OSError, OverflowError, ValueError):
        return "at an unknown time"
    if local.date() == current.date():
        return local.strftime("%I:%M %p").lstrip("0")
    return local.strftime("%b %d at %I:%M %p").replace(" 0", " ")


class ElidingLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self._apply_elision()

    def sizeHint(self):
        return QSize(QFontMetrics(self.font()).horizontalAdvance(self._full_text), super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        elided = QFontMetrics(self.font()).elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())
        )
        if elided != super().text():
            super().setText(elided)


class HealthRowWidget(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("healthRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.label = QLabel()
        self.label.setObjectName("healthLabel")
        self.detail = QLabel()
        self.detail.setObjectName("healthDetail")
        self.detail.setWordWrap(True)
        self.detail.setMinimumWidth(160)
        self.detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.label)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        self.badge = StatusPill()
        self.badge.setFixedHeight(24)
        self.badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

    def update_row(self, row) -> None:
        self.label.setText(row.label)
        self.detail.setText(row.detail)
        self.badge.set_status(row.status.upper(), row.tone)


class Disclosure(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = QPushButton(f"▸  {title}")
        self.toggle.setObjectName("disclosureToggle")
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title = title
        self.body = QWidget()
        self.body.setVisible(False)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self.toggle.setText(f"{'▾' if checked else '▸'}  {self._title}")

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)
