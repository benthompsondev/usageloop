"""Classify a series of normalized five-hour window observations."""

from dataclasses import dataclass
from typing import Any, Sequence

from .quota import QuotaSnapshot
from .quota import select_five_hour


@dataclass(frozen=True)
class Classification:
    state: str
    confidence: str
    reason: str
    evidence: dict[str, Any]


def classify(snapshots: Sequence[QuotaSnapshot]) -> Classification:
    if not snapshots:
        return _unknown("No observations are available.", {"sample_count": 0})

    ordered = sorted(snapshots, key=lambda item: item.observed_at)
    selections = [select_five_hour(snapshot) for snapshot in ordered]
    evidence: dict[str, Any] = {"sample_count": len(ordered)}

    if any(selection.status == "ambiguous" for selection in selections):
        return _unknown("Multiple five-hour candidates make the evidence ambiguous.", evidence)

    selected = [selection.window for selection in selections if selection.status == "selected"]
    absent_count = sum(selection.status == "absent" for selection in selections)
    if absent_count == len(selections):
        return Classification(
            "ABSENT",
            "explicit",
            "No approximately five-hour window is exposed in the current evidence.",
            evidence,
        )
    if absent_count:
        return _unknown("The five-hour window appeared or disappeared between observations.", evidence)

    windows = [window for window in selected if window is not None]
    current = windows[-1]
    if current.used_percent >= 100 or current.blocked_reason is not None:
        evidence["used_percent"] = current.used_percent
        if current.blocked_reason is not None:
            evidence["blocked_reason"] = current.blocked_reason
        return Classification(
            "EXHAUSTED",
            "explicit",
            "The selected window or its quota bucket explicitly reports exhaustion or blocking.",
            evidence,
        )

    if len(ordered) < 3:
        return _unknown("At least three observations are required.", evidence)

    elapsed = ordered[-1].observed_at - ordered[0].observed_at
    evidence["elapsed_seconds"] = _rounded(elapsed)
    if elapsed < 15:
        return _unknown("Observations span less than 15 seconds.", evidence)
    if any(
        later.observed_at <= earlier.observed_at
        for earlier, later in zip(ordered, ordered[1:])
    ):
        return _unknown("Observation timestamps are not strictly increasing.", evidence)

    identities = {(window.limit_id, window.slot) for window in windows}
    durations = {window.duration_minutes for window in windows}
    if len(identities) != 1:
        return _unknown("The selected quota bucket or window position changed.", evidence)
    if len(durations) != 1:
        return _unknown("The reported window duration changed between observations.", evidence)
    if any(window.resets_at is None for window in windows):
        return _unknown("One or more observations omit a valid reset timestamp.", evidence)

    resets = [window.resets_at for window in windows if window.resets_at is not None]
    if any(reset <= snapshot.observed_at for reset, snapshot in zip(resets, ordered)):
        return _unknown("A reset timestamp is not in the future for all observations.", evidence)

    reset_span = max(resets) - min(resets)
    reset_advance = resets[-1] - resets[0]
    remaining = [reset - snapshot.observed_at for reset, snapshot in zip(resets, ordered)]
    evidence.update(
        {
            "reset_span_seconds": reset_span,
            "reset_advance_seconds": reset_advance,
            "wall_time_advance_seconds": _rounded(elapsed),
            "remaining_distance_span_seconds": _rounded(max(remaining) - min(remaining)),
        }
    )

    if reset_span <= 2:
        return Classification(
            "ANCHORED",
            _confidence(len(ordered), elapsed),
            "The absolute reset timestamp stayed fixed within two seconds while wall time advanced.",
            evidence,
        )

    if any(
        later.used_percent < earlier.used_percent
        for earlier, later in zip(windows, windows[1:])
    ):
        return _unknown("Usage decreased while the reset timestamp changed, indicating a reset or contradictory evidence.", evidence)

    pairwise_tracks_wall = all(
        abs((later_reset - earlier_reset) - (later.observed_at - earlier.observed_at))
        <= max(3.0, (later.observed_at - earlier.observed_at) * 0.25)
        for earlier, later, earlier_reset, later_reset in zip(
            ordered,
            ordered[1:],
            resets,
            resets[1:],
        )
    )
    duration_seconds = windows[0].duration_minutes * 60
    full_window_tolerance = max(300, duration_seconds * 0.05)
    distances_near_full_window = all(
        abs(distance - duration_seconds) <= full_window_tolerance for distance in remaining
    )
    distance_stable = max(remaining) - min(remaining) <= 5
    slope = reset_advance / elapsed
    evidence["reset_slope"] = _rounded(slope)
    evidence["target_duration_seconds"] = duration_seconds

    if pairwise_tracks_wall and distances_near_full_window and distance_stable:
        return Classification(
            "UNANCHORED",
            _confidence(len(ordered), elapsed),
            "The reset timestamp advanced with wall time and stayed near one full window away.",
            evidence,
        )
    return _unknown("Reset behavior is changing but does not match a safe anchored or unanchored pattern.", evidence)


def _unknown(reason: str, evidence: dict[str, Any]) -> Classification:
    return Classification("UNKNOWN", "low", reason, evidence)


def _confidence(sample_count: int, elapsed: float) -> str:
    return "high" if sample_count >= 4 and elapsed >= 30 else "medium"


def _rounded(value: float) -> float:
    return round(value, 3)
