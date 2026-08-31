"""Local-only schedule calculations for guarded provider actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo


RESET_BUFFER_SECONDS = 60
CONTINUOUS = "continuous"
DAILY = "daily"
SCHEDULE_MODES = frozenset({CONTINUOUS, DAILY})


@dataclass(frozen=True)
class ScheduleSummary:
    mode: str
    next_action_at: float | None
    due: bool


def _local_candidate(
    day: date, hour: int, minute: int, timezone: tzinfo | None
) -> float:
    """Return one stable real timestamp for a local wall-clock selection.

    Round-tripping through a timestamp normalizes nonexistent spring-forward
    times to the corresponding first real time. ``fold=0`` consistently chooses
    the first occurrence during a fall-back overlap.
    """
    wall_time = datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=timezone,
        fold=0,
    )
    return datetime.fromtimestamp(wall_time.timestamp(), timezone).timestamp()


def next_daily_start_after(
    boundary_reset_at: float,
    hour: int,
    minute: int,
    *,
    timezone: tzinfo | None = None,
) -> float:
    """Find the first selected local start after a verified reset boundary."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("daily start time is outside the supported range")

    boundary = float(boundary_reset_at)
    earliest = boundary + RESET_BUFFER_SECONDS
    local_boundary = datetime.fromtimestamp(boundary, timezone)
    day = local_boundary.date()
    candidate = _local_candidate(day, hour, minute, timezone)
    while candidate < boundary:
        day += timedelta(days=1)
        candidate = _local_candidate(day, hour, minute, timezone)
    return max(candidate, earliest)


def schedule_summary(
    mode: str,
    *,
    boundary_reset_at: float | None,
    now: float,
    hour: int = 4,
    minute: int = 0,
    timezone: tzinfo | None = None,
) -> ScheduleSummary:
    if mode not in SCHEDULE_MODES:
        raise ValueError(f"unsupported schedule mode: {mode}")
    if boundary_reset_at is None:
        return ScheduleSummary(mode, None, False)

    if mode == CONTINUOUS:
        next_action_at = float(boundary_reset_at) + RESET_BUFFER_SECONDS
    else:
        next_action_at = next_daily_start_after(
            boundary_reset_at,
            hour,
            minute,
            timezone=timezone,
        )
    return ScheduleSummary(mode, next_action_at, now >= next_action_at)
