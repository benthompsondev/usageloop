"""Reusable presentation widgets for the UsageLoop shell."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
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


def present_provider_state(state: ProviderViewState, *, now: float) -> ProviderPresentation:
    if not state.installed:
        return ProviderPresentation(
            "NOT DETECTED",
            "neutral",
            "Not installed",
            "No reset information",
            f"Install {state.display_name} and it will show up here.",
            "No local status yet",
            "Usage not checked",
        )

    reset = _reset_copy(state.reset_at, now=now)
    verified = (
        f"Last verified {_friendly_time(state.last_verified_at, now=now)}"
        if state.last_verified_at is not None
        else "No verified check yet"
    )
    usage = (
        f"Last-known usage {state.used_percent}% · {_friendly_time(state.usage_checked_at, now=now)}"
        if state.used_percent is not None and state.usage_checked_at is not None
        else "Usage not checked"
    )

    if state.provider_id == "claude" and not state.automation_supported:
        return ProviderPresentation(
            "AUTOMATION PAUSED",
            "warning",
            "Compatibility check needed",
            reset,
            "Claude automation is paused. Settings has the technical reason.",
            verified,
            usage,
        )

    if (
        state.last_verified_at is not None
        and now - state.last_verified_at > STALE_AFTER_SECONDS
    ):
        return ProviderPresentation(
            "STALE",
            "warning",
            format_countdown(state.reset_at, now),
            reset,
            "This is cached information. Check Settings before relying on it.",
            verified,
            usage,
        )

    status, tone = {
        "Ready": ("READY", "success"),
        "Waiting": ("WAITING", "neutral"),
        "Starting": (("STARTING" if state.provider_id == "claude" else "CHECKING"), "info"),
        "Needs attention": ("CHECK NEEDED", "error"),
    }.get(state.status, ("UNKNOWN", "warning"))
    headline = (
        format_countdown(state.reset_at, now)
        if state.reset_at is not None
        else (
            "Starting next window"
            if state.status == "Starting" and state.provider_id == "claude"
            else ("Checking safely" if state.status == "Starting" else "Waiting for reset")
        )
    )
    detail = {
        "Ready": "This five-hour window is anchored and counting down.",
        "Waiting": (
            "Claude will be initialized once, when a safe boundary is known."
            if state.provider_id == "claude"
            else "Waiting for enough evidence to act safely."
        ),
        "Starting": (
            "Running one prompt-free Claude initialization."
            if state.provider_id == "claude"
            else "Running a bounded provider check."
        ),
        "Needs attention": "Settings has the technical reason. Nothing is retried automatically.",
    }.get(state.status, "The latest provider state was inconclusive.")
    return ProviderPresentation(status, tone, headline, reset, detail, verified, usage)


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
        # Cards hug their content. Growing them to fill a tall window just
        # produced hollow cards, which reads worse than honest empty space.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(236)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.name_label = QLabel(state.display_name)
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
        layout.addSpacing(4)
        self.separator = QFrame()
        self.separator.setObjectName("cardRule")
        self.separator.setFrameShape(QFrame.Shape.HLine)
        self.separator.setFixedHeight(1)
        layout.addWidget(self.separator)
        layout.addSpacing(2)
        self.metadata_label = QLabel()
        self.metadata_label.setProperty("muted", True)
        self.metadata_label.setWordWrap(True)
        layout.addWidget(self.metadata_label)
        self.usage_label = QLabel()
        self.usage_label.setProperty("muted", True)
        layout.addWidget(self.usage_label)

        layout.addStretch(1)
        self.action_button = QPushButton("Start my first window now")
        self.action_button.setObjectName("primaryButton")
        self.action_button.setVisible(False)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.update_state(state, now=time.time())

    def update_state(self, state: ProviderViewState, *, now: float) -> None:
        presented = present_provider_state(state, now=now)
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


def make_surface_card(
    title: str,
    description: str | None = None,
    *,
    parent: QWidget | None = None,
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
    """A label that shortens itself instead of forcing its container wider.

    `QSizePolicy.Ignored` looks like the right tool for "let this shrink", but it
    is a growing policy: Qt disregards the size hint and hands the widget as much
    room as it can take. Using it on the header tagline let the brand block
    expand with the window and push the navigation and trust chip off the right
    edge. This reports a zero minimum width and elides its own text, so it can
    never drive the layout.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full_text = text
        self._apply_elision()

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        return QSize(
            metrics.horizontalAdvance(self._full_text),
            super().sizeHint().height(),
        )

    def minimumSizeHint(self):
        # Zero width is the point: the header must never grow to fit this.
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        metrics = QFontMetrics(self.font())
        available = max(0, self.width())
        elided = metrics.elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, available
        )
        if elided != super().text():
            super().setText(elided)


class HealthRowWidget(QFrame):
    """One readable line of health: name, badge, and a plain-English reason."""

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
        self.detail.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
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
    """A 'Technical details' expander that starts closed.

    Troubleshooting text is kept in full, but it no longer dominates a page a
    normal user reads.
    """

    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.toggle = QPushButton(f"\u25b8  {title}")
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
        self.toggle.setText(f"{'\u25be' if checked else '\u25b8'}  {self._title}")

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)
