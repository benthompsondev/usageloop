"""Reusable presentation widgets for the UsageLoop shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .app_state import ProviderViewState, format_countdown


STALE_AFTER_SECONDS = 6 * 60 * 60


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
    action = (
        f"Last action  {state.last_action}"
        if state.last_action else "Last action  No automatic window start recorded"
    )
    if not state.installed:
        return ProviderPresentation(
            "NEEDS ATTENTION", "error", "Codex is not installed", "No reset information",
            "Install and sign in to Codex, then reopen UsageLoop.", "No local status yet",
            usage, weekly, action,
        )

    reset = _reset_copy(state.reset_at, now=now)
    if state.last_verified_at is not None:
        verified = f"Last verified {_friendly_time(state.last_verified_at, now=now)}"
    elif state.usage_checked_at is not None:
        verified = f"Last read {_friendly_time(state.usage_checked_at, now=now)}"
    else:
        verified = "Not checked yet"

    if state.status == "Needs attention":
        return ProviderPresentation(
            "NEEDS ATTENTION", "error", "A safe check needs attention", reset,
            "Nothing was retried. Diagnostics has the technical reason.",
            verified, usage, weekly, action,
        )
    if state.status == "Starting":
        return ProviderPresentation(
            "STARTING WINDOW", "info", "Starting the next reset clock", reset,
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
            "Codex reports a fixed five-hour reset. The countdown runs locally with no provider traffic.",
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
        self.setMinimumHeight(330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 21, 24, 21)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.name_label = QLabel("Codex reset clock")
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
        self.weekly_label = QLabel()
        self.weekly_label.setObjectName("metricLabel")
        layout.addWidget(self.weekly_label)
        self.weekly_bar = _metric_bar("weeklyBar")
        layout.addWidget(self.weekly_bar)
        self.action_label = QLabel()
        self.action_label.setProperty("muted", True)
        self.action_label.setWordWrap(True)
        layout.addWidget(self.action_label)
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
        self.metadata_label.setText(presented.verified)
        self.usage_label.setText(presented.usage)
        self.weekly_label.setText(presented.weekly)
        self.action_label.setText(presented.action)
        self.usage_bar.setValue(int(state.used_percent or 0))
        self.weekly_bar.setValue(int(state.weekly_used_percent or 0))


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
