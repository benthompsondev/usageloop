"""Local-only schedule calculations for guarded provider actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Sequence


RESET_BUFFER_SECONDS = 60
FIVE_HOUR_WINDOW_SECONDS = 5 * 60 * 60
CONTINUOUS = "continuous"
DAILY = "daily"
WEEKLY = "weekly"
SCHEDULE_MODES = frozenset({CONTINUOUS, DAILY, WEEKLY})


@dataclass(frozen=True)
class ScheduleSummary:
    mode: str
    next_action_at: float | None
    due: bool
    phase: str = "waiting"


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


def tomorrow_first_start(
    mode: str,
    *,
    now: float,
    hour: int = 4,
    minute: int = 0,
    weekly_times: Sequence[tuple[int, int]] | None = None,
    timezone: tzinfo | None = None,
) -> float | None:
    """Capture tomorrow's local first start, not today's next due action."""
    tomorrow = datetime.fromtimestamp(now, timezone).date() + timedelta(days=1)
    if mode == WEEKLY:
        hour, minute = normalize_weekly_times(weekly_times)[tomorrow.weekday()]
    elif mode != DAILY:
        return None
    return _local_candidate(tomorrow, hour, minute, timezone)


def normalize_weekly_times(
    weekly_times: Sequence[tuple[int, int]] | None,
) -> tuple[tuple[int, int], ...]:
    if weekly_times is None or len(weekly_times) != 7:
        raise ValueError("weekly schedule requires exactly seven start times")
    result: list[tuple[int, int]] = []
    for value in weekly_times:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise ValueError("weekly start time must contain hour and minute")
        hour, minute = value
        if (
            not isinstance(hour, int)
            or isinstance(hour, bool)
            or not 0 <= hour <= 23
            or not isinstance(minute, int)
            or isinstance(minute, bool)
            or not 0 <= minute <= 59
        ):
            raise ValueError("weekly start time is outside the supported range")
        result.append((hour, minute))
    return tuple(result)


def next_weekly_start_after(
    reference_at: float,
    weekly_times: Sequence[tuple[int, int]],
    *,
    timezone: tzinfo | None = None,
) -> float:
    """Return the first configured local start strictly after a timestamp."""
    times = normalize_weekly_times(weekly_times)
    reference = float(reference_at)
    local_reference = datetime.fromtimestamp(reference, timezone)
    for offset in range(8):
        day = local_reference.date() + timedelta(days=offset)
        hour, minute = times[day.weekday()]
        candidate = _local_candidate(day, hour, minute, timezone)
        if candidate > reference:
            return candidate
    raise ValueError("weekly schedule did not produce a future start")


def _weekly_start_at_or_before(
    reference_at: float,
    weekly_times: Sequence[tuple[int, int]],
    *,
    timezone: tzinfo | None,
) -> float:
    times = normalize_weekly_times(weekly_times)
    reference = float(reference_at)
    local_reference = datetime.fromtimestamp(reference, timezone)
    for offset in range(8):
        day = local_reference.date() - timedelta(days=offset)
        hour, minute = times[day.weekday()]
        candidate = _local_candidate(day, hour, minute, timezone)
        if candidate <= reference:
            return candidate
    raise ValueError("weekly schedule did not produce a previous start")


def _weekly_schedule_summary(
    boundary_reset_at: float,
    now: float,
    weekly_times: Sequence[tuple[int, int]],
    *,
    timezone: tzinfo | None,
) -> ScheduleSummary:
    boundary = float(boundary_reset_at)
    current = float(now)
    continuous_due = boundary + RESET_BUFFER_SECONDS
    next_target = next_weekly_start_after(current, weekly_times, timezone=timezone)
    pause_start = next_target - FIVE_HOUR_WINDOW_SECONDS

    if boundary > current:
        if pause_start <= continuous_due < next_target:
            return ScheduleSummary(WEEKLY, next_target, False, "overnight_pause")
        return ScheduleSummary(
            WEEKLY, continuous_due, current >= continuous_due, "active_window"
        )

    if pause_start <= current < next_target:
        return ScheduleSummary(WEEKLY, next_target, False, "overnight_pause")

    latest_target = _weekly_start_at_or_before(
        current, weekly_times, timezone=timezone
    )
    if boundary < latest_target:
        next_action = max(continuous_due, latest_target)
        return ScheduleSummary(
            WEEKLY,
            next_action,
            current >= next_action,
            "scheduled_first_start",
        )
    return ScheduleSummary(
        WEEKLY,
        continuous_due,
        current >= continuous_due,
        "continuous_rollover",
    )


def schedule_summary(
    mode: str,
    *,
    boundary_reset_at: float | None,
    now: float,
    hour: int = 4,
    minute: int = 0,
    weekly_times: Sequence[tuple[int, int]] | None = None,
    timezone: tzinfo | None = None,
) -> ScheduleSummary:
    if mode not in SCHEDULE_MODES:
        raise ValueError(f"unsupported schedule mode: {mode}")
    if boundary_reset_at is None:
        return ScheduleSummary(mode, None, False)

    if mode == CONTINUOUS:
        next_action_at = float(boundary_reset_at) + RESET_BUFFER_SECONDS
        return ScheduleSummary(
            mode, next_action_at, now >= next_action_at, "continuous_rollover"
        )
    if mode == DAILY:
        next_action_at = next_daily_start_after(
            boundary_reset_at,
            hour,
            minute,
            timezone=timezone,
        )
        return ScheduleSummary(
            mode, next_action_at, now >= next_action_at, "scheduled_first_start"
        )
    return _weekly_schedule_summary(
        boundary_reset_at,
        now,
        weekly_times or (),
        timezone=timezone,
    )
