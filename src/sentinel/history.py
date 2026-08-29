"""Allowlisted local JSONL history for classifier evidence and diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from . import __version__
from .classifier import Classification
from .quota import QuotaSnapshot, QuotaWindow


_SAFE_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9 ._+/-]{1,80}$")
_EVIDENCE_KEYS = {
    "sample_count",
    "elapsed_seconds",
    "reset_span_seconds",
    "reset_advance_seconds",
    "wall_time_advance_seconds",
    "remaining_distance_span_seconds",
    "reset_slope",
    "target_duration_seconds",
    "used_percent",
    "blocked_reason",
    "notification_count",
}


class SafeHistory:
    def __init__(self, path: Path | None = None):
        self.path = path or default_history_path()

    def record_observation(
        self,
        snapshot: QuotaSnapshot,
        classification: Classification,
        codex_version: str,
    ) -> None:
        row = {
            "event": "observation",
            "timestamp": _iso_timestamp(snapshot.observed_at),
            "observed_at": snapshot.observed_at,
            "sentinel_version": __version__,
            "codex_version": _safe_version(codex_version),
            "windows": [_window_to_dict(window) for window in snapshot.windows],
            "classification": classification.state,
            "confidence": classification.confidence,
            "evidence": _safe_evidence(classification.evidence),
        }
        self._append(row)

    def record_error(self, category: str) -> None:
        safe_category = category if _SAFE_CATEGORY.fullmatch(category) else "unexpected_error"
        self._append(
            {
                "event": "error",
                "timestamp": _iso_timestamp(datetime.now(timezone.utc).timestamp()),
                "sentinel_version": __version__,
                "category": safe_category,
            }
        )

    def record_transition(self, previous: str, current: str) -> None:
        allowed = {"ANCHORED", "UNANCHORED", "ABSENT", "EXHAUSTED", "UNKNOWN"}
        if previous not in allowed or current not in allowed:
            return
        self._append(
            {
                "event": "transition",
                "timestamp": _iso_timestamp(datetime.now(timezone.utc).timestamp()),
                "sentinel_version": __version__,
                "previous": previous,
                "current": current,
            }
        )

    def load_recent(
        self,
        *,
        now: float,
        max_age_seconds: float = 21600,
        limit: int = 4,
    ) -> list[QuotaSnapshot]:
        if not self.path.is_file():
            return []
        snapshots: list[QuotaSnapshot] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                row = json.loads(line)
                snapshot = _snapshot_from_row(row)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if snapshot is None or snapshot.observed_at < now - max_age_seconds:
                continue
            if snapshot.observed_at <= now + 60:
                snapshots.append(snapshot)
        snapshots.sort(key=lambda item: item.observed_at)
        return snapshots[-max(1, limit) :]

    def _append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True) + "\n")


def default_history_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "CodexWindowSentinel" / "sentinel.jsonl"


def _window_to_dict(window: QuotaWindow) -> dict[str, Any]:
    return {
        "limit_id": window.limit_id,
        "slot": window.slot,
        "used_percent": window.used_percent,
        "duration_minutes": window.duration_minutes,
        "resets_at": window.resets_at,
        "blocked_reason": window.blocked_reason,
    }


def _snapshot_from_row(row: Any) -> QuotaSnapshot | None:
    if not isinstance(row, dict) or row.get("event") != "observation":
        return None
    observed_at = row.get("observed_at")
    raw_windows = row.get("windows")
    if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
        return None
    if not isinstance(raw_windows, list):
        return None
    windows: list[QuotaWindow] = []
    for raw in raw_windows:
        if not isinstance(raw, dict):
            return None
        used = raw.get("used_percent")
        slot = raw.get("slot")
        if not isinstance(used, int) or isinstance(used, bool) or slot not in {"primary", "secondary"}:
            return None
        windows.append(
            QuotaWindow(
                limit_id=raw.get("limit_id") if isinstance(raw.get("limit_id"), str) else None,
                slot=slot,
                used_percent=used,
                duration_minutes=raw.get("duration_minutes") if isinstance(raw.get("duration_minutes"), int) else None,
                resets_at=raw.get("resets_at") if isinstance(raw.get("resets_at"), int) else None,
                blocked_reason=raw.get("blocked_reason") if isinstance(raw.get("blocked_reason"), str) else None,
            )
        )
    return QuotaSnapshot(float(observed_at), tuple(windows))


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in _EVIDENCE_KEYS:
        value = evidence.get(key)
        if isinstance(value, (int, float, bool)) and not isinstance(value, complex):
            safe[key] = value
        elif key == "blocked_reason" and isinstance(value, str) and _SAFE_CATEGORY.fullmatch(value):
            safe[key] = value
    return safe


def _safe_version(value: str) -> str:
    return value if _SAFE_VERSION.fullmatch(value) else "unknown"


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
