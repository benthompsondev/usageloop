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


def present_provider_state(
    state: ProviderViewState, *, now: float, automation_enabled: bool = False
) -> ProviderPresentation:
    """Map internal provider state onto words a normal person can act on.

    The important rule is that nothing here claims more certainty than the state
    supports. A provider that has never been checked says so, rather than
    borrowing the language of a window that is genuinely counting down.
    """
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
    if state.last_verified_at is not None:
        verified = f"Last verified {_friendly_time(state.last_verified_at, now=now)}"
    elif state.usage_checked_at is not None:
        # Observed, but from evidence that reports usage without a boundary.
        verified = f"Last read {_friendly_time(state.usage_checked_at, now=now)}"
    else:
        verified = "Not checked yet"
    usage = (
        f"Last-known usage {state.used_percent}% \u00b7 {_friendly_time(state.usage_checked_at, now=now)}"
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

    if state.status == "Needs attention":
        return ProviderPresentation(
            "CHECK NEEDED",
            "error",
            "Needs attention",
            reset,
            "Settings has the technical reason. Nothing is retried automatically.",
            verified,
            usage,
        )

    if state.status == "Starting":
        return ProviderPresentation(
            "STARTING" if state.provider_id == "claude" else "CHECKING",
            "info",
            "Starting next window" if state.provider_id == "claude" else "Checking safely",
            reset,
            (
                "Running one prompt-free Claude initialization."
                if state.provider_id == "claude"
                else "Running a bounded provider check."
            ),
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

    if state.reset_at is None:
        # Nothing has ever been verified for this provider. Saying "waiting for
        # reset" here would invent a boundary that was never observed.
        if automation_enabled:
            return ProviderPresentation(
                "NOT CHECKED YET",
                "info",
                "Not checked yet",
                reset,
                "UsageLoop will check this provider and start a window when it is safe.",
                verified,
                usage,
            )
        return ProviderPresentation(
            "NOT CHECKED YET",
            "neutral",
            "Ready to set up",
            reset,
            "Turn on UsageLoop when you want it to begin keeping your coding windows ready.",
            verified,
            usage,
        )

    if state.status == "Ready":
        # A window observed without a reported boundary has a derived reset. The
        # countdown is still useful, but the card must not present an estimate
        # with the same confidence as a verified anchor.
        estimated = state.last_verified_at is None
        return ProviderPresentation(
            "READY",
            "success",
            format_countdown(state.reset_at, now),
            f"{reset} (estimated)" if estimated else reset,
            state.detail if estimated and state.detail else "This five-hour window is counting down.",
            verified,
            usage,
        )

    # A boundary is known but the window is not currently running.
    return ProviderPresentation(
        "WAITING",
        "neutral",
        format_countdown(state.reset_at, now),
        reset,
        (
            "The last known window has ended. A new one starts when it is safe."
            if automation_enabled
            else "The last known window has ended. Turn UsageLoop on to start the next one."
        ),
        verified,
        usage,
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
        self.update_state(state, now=time.time(), automation_enabled=False)

    def update_state(
        self, state: ProviderViewState, *, now: float, automation_enabled: bool = False
    ) -> None:
        presented = present_provider_state(
            state, now=now, automation_enabled=automation_enabled
        )
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
