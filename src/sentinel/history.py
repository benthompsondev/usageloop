"""Allowlisted local JSONL history for classifier evidence and diagnostics."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, BinaryIO, Iterator
import uuid

from . import __version__
from .app_state import app_data_root
from .classifier import Classification
from .quota import QuotaSnapshot, QuotaWindow


_SAFE_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9 ._+/-]{1,80}$")
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SAFE_REASONING = re.compile(r"^[a-z]{1,16}$")
_SAFE_ATTEMPT_ID = re.compile(r"^[a-f0-9]{32}$")
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[a-z0-9:_-]{1,96}$")
_ATTEMPT_STATES = {
    "reserved",
    "launch_attempted",
    "request_possibly_sent",
    "verified",
    "failed_recoverable",
    "failed_guarded",
}
_ATTEMPT_MODES = {"rollover", "bootstrap"}
_ALLOWED_ATTEMPT_TRANSITIONS = {
    "reserved": {"launch_attempted", "failed_recoverable"},
    "launch_attempted": {
        "request_possibly_sent",
        "verified",
        "failed_recoverable",
        "failed_guarded",
    },
    "request_possibly_sent": {"verified", "failed_guarded"},
    "verified": set(),
    "failed_recoverable": set(),
    "failed_guarded": set(),
}
_OBSERVED_STATES = {"ANCHORED", "UNANCHORED", "ABSENT", "EXHAUSTED", "UNKNOWN"}
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


class HistoryStateError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class HistoryIntegrityError(HistoryStateError):
    def __init__(self):
        super().__init__("history_corrupt", "Saved history contains a malformed record.")


class HistoryUnavailableError(HistoryStateError):
    def __init__(self):
        super().__init__("history_unavailable", "Saved history could not be read safely.")


@dataclass(frozen=True)
class TriggerAttempt:
    attempt_id: str
    mode: str
    idempotency_key: str
    boundary_reset_at: int | None
    state: str
    created_at: float
    updated_at: float


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

    @contextmanager
    def trigger_reservation_guard(self) -> Iterator[None]:
        """Serialize the short duplicate-check and reservation transaction.

        The operating-system lock is released automatically if the app exits,
        so restart recovery continues to rely on the persisted attempt state.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with lock_path.open("a+b") as stream:
            _lock_file(stream)
            try:
                yield
            finally:
                _unlock_file(stream)

    def reserve_trigger(
        self,
        *,
        mode: str,
        idempotency_key: str,
        boundary_reset_at: int | None,
        model: str,
        reasoning_effort: str,
        now: float,
    ) -> TriggerAttempt:
        if mode not in _ATTEMPT_MODES:
            raise ValueError("Unsupported trigger mode.")
        if not _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("Unsafe idempotency key.")
        attempt = TriggerAttempt(
            attempt_id=uuid.uuid4().hex,
            mode=mode,
            idempotency_key=idempotency_key,
            boundary_reset_at=int(boundary_reset_at) if boundary_reset_at is not None else None,
            state="reserved",
            created_at=float(now),
            updated_at=float(now),
        )
        self._append(
            {
                "event": "trigger_state",
                "timestamp": _iso_timestamp(now),
                "occurred_at": float(now),
                "sentinel_version": __version__,
                "attempt_id": attempt.attempt_id,
                "mode": mode,
                "idempotency_key": idempotency_key,
                "boundary_reset_at": attempt.boundary_reset_at,
                "state": "reserved",
                "model": model if _SAFE_MODEL.fullmatch(model) else "unknown",
                "reasoning_effort": (
                    reasoning_effort if _SAFE_REASONING.fullmatch(reasoning_effort) else "unknown"
                ),
            }
        )
        return attempt

    def transition_trigger(
        self,
        attempt_id: str,
        state: str,
        *,
        outcome: str | None = None,
        observed_state: str | None = None,
        now: float,
    ) -> None:
        if not _SAFE_ATTEMPT_ID.fullmatch(attempt_id):
            raise ValueError("Unsafe attempt identifier.")
        if state not in _ATTEMPT_STATES or state == "reserved":
            raise ValueError("Unsupported trigger state transition.")
        attempts = {item.attempt_id: item for item in self.trigger_attempts()}
        current = attempts.get(attempt_id)
        if current is None or state not in _ALLOWED_ATTEMPT_TRANSITIONS[current.state]:
            raise HistoryIntegrityError()
        row: dict[str, Any] = {
            "event": "trigger_state",
            "timestamp": _iso_timestamp(now),
            "occurred_at": float(now),
            "sentinel_version": __version__,
            "attempt_id": attempt_id,
            "state": state,
        }
        if outcome is not None:
            row["outcome"] = outcome if _SAFE_CATEGORY.fullmatch(outcome) else "unexpected_error"
        if observed_state is not None:
            row["observed_state"] = (
                observed_state if observed_state in _OBSERVED_STATES else "UNKNOWN"
            )
        self._append(row)

    def trigger_attempts(self) -> list[TriggerAttempt]:
        attempts: dict[str, TriggerAttempt] = {}
        for row in self._read_rows(strict=True):
            if row.get("event") != "trigger_state":
                continue
            attempt_id = row.get("attempt_id")
            state = row.get("state")
            occurred_at = row.get("occurred_at")
            if (
                not isinstance(attempt_id, str)
                or not _SAFE_ATTEMPT_ID.fullmatch(attempt_id)
                or state not in _ATTEMPT_STATES
                or not isinstance(occurred_at, (int, float))
                or isinstance(occurred_at, bool)
            ):
                raise HistoryIntegrityError()
            if state == "reserved":
                mode = row.get("mode")
                key = row.get("idempotency_key")
                boundary = row.get("boundary_reset_at")
                if (
                    mode not in _ATTEMPT_MODES
                    or not isinstance(key, str)
                    or not _SAFE_IDEMPOTENCY_KEY.fullmatch(key)
                    or (
                        boundary is not None
                        and (not isinstance(boundary, int) or isinstance(boundary, bool))
                    )
                ):
                    raise HistoryIntegrityError()
                attempts[attempt_id] = TriggerAttempt(
                    attempt_id,
                    mode,
                    key,
                    boundary,
                    state,
                    float(occurred_at),
                    float(occurred_at),
                )
            elif attempt_id not in attempts:
                raise HistoryIntegrityError()
            else:
                if state not in _ALLOWED_ATTEMPT_TRANSITIONS[attempts[attempt_id].state]:
                    raise HistoryIntegrityError()
                attempts[attempt_id] = replace(
                    attempts[attempt_id], state=state, updated_at=float(occurred_at)
                )
        return sorted(attempts.values(), key=lambda item: (item.created_at, item.attempt_id))

    def record_trigger_attempt(
        self,
        boundary_reset_at: int,
        model: str,
        reasoning_effort: str,
    ) -> None:
        self._append(
            {
                "event": "trigger_attempt",
                "timestamp": _iso_timestamp(datetime.now(timezone.utc).timestamp()),
                "sentinel_version": __version__,
                "boundary_reset_at": int(boundary_reset_at),
                "model": model if _SAFE_MODEL.fullmatch(model) else "unknown",
                "reasoning_effort": (
                    reasoning_effort if _SAFE_REASONING.fullmatch(reasoning_effort) else "unknown"
                ),
            }
        )

    def record_trigger_result(
        self,
        boundary_reset_at: int,
        outcome: str,
        observed_state: str,
    ) -> None:
        safe_outcome = outcome if _SAFE_CATEGORY.fullmatch(outcome) else "unexpected_error"
        allowed_states = {"ANCHORED", "UNANCHORED", "ABSENT", "EXHAUSTED", "UNKNOWN"}
        self._append(
            {
                "event": "trigger_result",
                "timestamp": _iso_timestamp(datetime.now(timezone.utc).timestamp()),
                "sentinel_version": __version__,
                "boundary_reset_at": int(boundary_reset_at),
                "outcome": safe_outcome,
                "observed_state": observed_state if observed_state in allowed_states else "UNKNOWN",
            }
        )

    def trigger_attempt_count(self, boundary_reset_at: int) -> int:
        return sum(
            1
            for row in self._read_rows()
            if row.get("event") == "trigger_attempt"
            and row.get("boundary_reset_at") == int(boundary_reset_at)
        )

    def latest_anchored_reset_before(
        self,
        now: float,
        *,
        max_age_seconds: float = 21600,
    ) -> int | None:
        candidates: list[int] = []
        for row in self._read_rows():
            if row.get("event") != "observation" or row.get("classification") != "ANCHORED":
                continue
            observed_at = row.get("observed_at")
            if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
                continue
            if observed_at < now - max_age_seconds or observed_at > now + 60:
                continue
            windows = row.get("windows")
            if not isinstance(windows, list):
                continue
            for window in windows:
                if not isinstance(window, dict):
                    continue
                duration = window.get("duration_minutes")
                reset = window.get("resets_at")
                if (
                    isinstance(duration, int)
                    and 270 <= duration <= 330
                    and isinstance(reset, int)
                    and reset <= now
                ):
                    candidates.append(reset)
        return max(candidates) if candidates else None

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

    def _read_rows(self, *, strict: bool = False) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            if strict:
                raise HistoryUnavailableError() from exc
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                if strict:
                    raise HistoryIntegrityError() from exc
                continue
            if isinstance(row, dict):
                rows.append(row)
            elif strict:
                raise HistoryIntegrityError()
        return rows


def default_history_path() -> Path:
    return app_data_root() / "sentinel.jsonl"


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


def _lock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
