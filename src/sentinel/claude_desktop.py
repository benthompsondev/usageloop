"""Read Claude Desktop's own local plan-usage samples.

Claude Code runs in two hosts that share the `~/.claude` home but not the same
observation surface:

* the terminal CLI, which renders a status line and therefore invokes the
  configured statusLine helper;
* Claude Code inside the Claude Desktop app, which has no terminal status line
  and, as measured on this machine, never invokes that helper at all.

Desktop does keep its own local record of plan usage in
``%APPDATA%/Claude/plan-usage-history.json``. It is written by Desktop for its
own UI, holds no credentials, no prompts, and no conversation content, and is
read here without ever being written to.

What it gives is a usage percentage sampled roughly every fifteen minutes. What
it does not give is an absolute reset timestamp, so the window boundary is
*derived* from the sample where usage returns from zero. That derivation is an
estimate with the sampling interval as its error bar, and it is reported as an
estimate rather than laundered into a verified anchor.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Sequence


FIVE_HOURS_SECONDS = 18_000
#: Desktop samples roughly every fifteen minutes. Three missed samples means the
#: app is not running or is not recording, so the reading is treated as stale.
STALE_AFTER_SECONDS = 45 * 60
#: Only this on-disk schema is understood. Anything else fails closed.
SUPPORTED_VERSION = 2


@dataclass(frozen=True)
class UsageSample:
    observed_at: float
    five_hour_percent: float
    seven_day_percent: float | None


@dataclass(frozen=True)
class DesktopWindow:
    """A window inferred from Desktop's own usage samples."""

    observed_at: float
    five_hour_percent: float
    seven_day_percent: float | None
    #: Upper bound of the true reset, so acting on it can never be early.
    estimated_reset_at: int | None
    #: Seconds of uncertainty carried by the estimate.
    estimate_error_seconds: float | None
    active: bool
    stale: bool


def default_plan_usage_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "Claude" / "plan-usage-history.json"


def read_usage_samples(path: Path | None = None) -> tuple[UsageSample, ...]:
    """Parse Desktop's usage history, keeping only the two numbers we need.

    The file also carries an organization identifier per sample. It is never
    read into the returned data and never written anywhere by this app.
    """
    target = path or default_plan_usage_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ()
    if not isinstance(payload, dict) or payload.get("version") != SUPPORTED_VERSION:
        return ()
    raw = payload.get("samples")
    if not isinstance(raw, list):
        return ()
    samples: list[UsageSample] = []
    for item in raw:
        sample = _sample(item)
        if sample is not None:
            samples.append(sample)
    samples.sort(key=lambda item: item.observed_at)
    return tuple(samples)


def derive_window(samples: Sequence[UsageSample], *, now: float) -> DesktopWindow | None:
    """Infer the current five-hour window from a run of non-zero usage."""
    if not samples:
        return None
    latest = samples[-1]
    stale = now - latest.observed_at > STALE_AFTER_SECONDS

    if latest.five_hour_percent <= 0:
        # Usage is back at zero, so no window is currently running.
        return DesktopWindow(
            observed_at=latest.observed_at,
            five_hour_percent=latest.five_hour_percent,
            seven_day_percent=latest.seven_day_percent,
            estimated_reset_at=None,
            estimate_error_seconds=None,
            active=False,
            stale=stale,
        )

    # Walk back to the first sample of the current non-zero run.
    start_index = len(samples) - 1
    while start_index > 0 and samples[start_index - 1].five_hour_percent > 0:
        start_index -= 1
    first_active = samples[start_index]
    previous_zero = samples[start_index - 1] if start_index > 0 else None

    # The true window start lies in (previous_zero, first_active]. Taking the
    # later end makes the derived reset the latest it could be, so a caller can
    # never act early on it.
    estimated_reset = int(first_active.observed_at + FIVE_HOURS_SECONDS)
    error = (
        first_active.observed_at - previous_zero.observed_at
        if previous_zero is not None
        else None
    )
    return DesktopWindow(
        observed_at=latest.observed_at,
        five_hour_percent=latest.five_hour_percent,
        seven_day_percent=latest.seven_day_percent,
        estimated_reset_at=estimated_reset,
        estimate_error_seconds=error,
        active=True,
        stale=stale,
    )


def observe(path: Path | None = None, *, now: float) -> DesktopWindow | None:
    return derive_window(read_usage_samples(path), now=now)


def _sample(item: Any) -> UsageSample | None:
    if not isinstance(item, dict):
        return None
    stamp = item.get("t")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return None
    usage = item.get("u")
    if not isinstance(usage, dict):
        return None
    five = _percent(usage.get("fh"))
    if five is None:
        return None
    # Desktop stores milliseconds.
    return UsageSample(float(stamp) / 1000.0, five, _percent(usage.get("sd")))


def _percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if not 0 <= value <= 100:
        return None
    return float(value)
