"""A read-only view of UsageLoop's existing start records."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .history import HistoryStateError, SafeHistory
from .ui_components import StatusPill


# Saved intermediate states are not proof that a worker is still running.
START_OUTCOMES = {
    "reserved": (
        "Not confirmed", "warning",
        "Preparation was recorded, but no completed start was saved.",
    ),
    "launch_attempted": (
        "Not confirmed", "warning",
        "A start was attempted, but its outcome was not saved. UsageLoop will not retry this attempt.",
    ),
    "request_possibly_sent": (
        "Not confirmed", "warning",
        "A request may have reached Codex. No confirmed reset was saved; this attempt will not be retried.",
    ),
    "verified": (
        "Confirmed", "success",
        "Codex confirmed a running five-hour reset clock after this start.",
    ),
    "failed_recoverable": (
        "Not sent", "warning",
        "No start request was sent. The normal schedule can check again when it is safe.",
    ),
    "failed_guarded": (
        "Not confirmed", "warning",
        "The start could not be confirmed. UsageLoop will not retry this attempt because a request may have been sent.",
    ),
}


def activity_time(timestamp: float) -> str:
    """Absolute local dates remain understandable across restart and midnight."""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    except (OSError, OverflowError, ValueError):
        return "Time unavailable"


class RecentStartsDialog(QDialog):
    def __init__(self, history: SafeHistory | None, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle("Recent starts · UsageLoop")
        self.setObjectName("appRoot")
        self.resize(660, 640)
        self.setMinimumSize(460, 360)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        title = QLabel("Did my window start?")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        intro = QLabel("Your latest 10 start attempts, newest first. All times are local to this PC.")
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        root.addWidget(intro)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)
        note = QLabel(
            "Only UsageLoop start attempts appear here. Syncs, pauses, and time with the app closed "
            "or the PC asleep are not recorded as starts. A confirmed start is not a guarantee of remaining quota."
        )
        note.setWordWrap(True)
        note.setProperty("muted", True)
        root.addWidget(note)
        actions = QHBoxLayout()
        refresh = QPushButton("Refresh history")
        refresh.setObjectName("secondaryButton")
        refresh.setToolTip("Reread local history only. Makes no Codex requests.")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        actions.addStretch()
        close = QPushButton("Close")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        root.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(10)
        try:
            attempts = self.history.trigger_attempts() if self.history is not None else []
            message = "No starts recorded yet. Starts made by UsageLoop will appear here after you use it."
        except (OSError, HistoryStateError):
            attempts = []
            message = "History unavailable. UsageLoop could not read its saved records safely. Nothing has been changed."
        if not attempts:
            label = QLabel(message)
            label.setWordWrap(True)
            layout.addWidget(label)
        for attempt in reversed(attempts[-10:]):
            card = QFrame()
            card.setObjectName("surfaceCard")
            body = QVBoxLayout(card)
            body.setContentsMargins(16, 14, 16, 14)
            body.setSpacing(6)
            header = QHBoxLayout()
            name = QLabel("Automatic start" if attempt.mode == "rollover" else "Manual first start")
            name.setObjectName("sectionTitle")
            name.setWordWrap(True)
            header.addWidget(name)
            header.addStretch()
            status, tone, detail = START_OUTCOMES[attempt.state]
            pill = StatusPill()
            pill.set_status(status, tone)
            header.addWidget(pill)
            body.addLayout(header)
            for text in (activity_time(attempt.created_at), detail):
                label = QLabel(text)
                label.setWordWrap(True)
                body.addWidget(label)
            selection = QLabel(
                f"Model: {attempt.model or 'Not recorded'} · Reasoning: {attempt.reasoning_effort or 'Not recorded'}"
            )
            selection.setWordWrap(True)
            selection.setProperty("muted", True)
            body.addWidget(selection)
            if attempt.updated_at != attempt.created_at:
                updated = QLabel(f"Last recorded: {activity_time(attempt.updated_at)}")
                updated.setWordWrap(True)
                updated.setProperty("muted", True)
                body.addWidget(updated)
            layout.addWidget(card)
        layout.addStretch()
        self.scroll.setWidget(content)
