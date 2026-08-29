"""Normalize safe quota fields from Codex app-server responses."""

from dataclasses import dataclass
import re
from typing import Any


_SAFE_LIMIT_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_BLOCK_REASONS = {
    "rate_limit_reached",
    "workspace_owner_credits_depleted",
    "workspace_member_credits_depleted",
    "workspace_owner_usage_limit_reached",
    "workspace_member_usage_limit_reached",
}


@dataclass(frozen=True)
class QuotaWindow:
    limit_id: str | None
    slot: str
    used_percent: int
    duration_minutes: int | None
    resets_at: int | None
    blocked_reason: str | None


@dataclass(frozen=True)
class QuotaSnapshot:
    observed_at: float
    windows: tuple[QuotaWindow, ...]


@dataclass(frozen=True)
class FiveHourSelection:
    status: str
    window: QuotaWindow | None
    reason: str


def normalize_rate_limits(payload: dict[str, Any], observed_at: float) -> QuotaSnapshot:
    if not isinstance(payload, dict):
        return QuotaSnapshot(observed_at=observed_at, windows=())

    by_limit_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict) and by_limit_id:
        buckets = tuple(by_limit_id.items())
    else:
        compatibility = payload.get("rateLimits")
        buckets = (("codex", compatibility),) if isinstance(compatibility, dict) else ()

    windows: list[QuotaWindow] = []
    for map_key, bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        limit_id = _safe_limit_id(bucket.get("limitId")) or _safe_limit_id(map_key)
        blocked_reason = _blocked_reason(bucket)
        for slot in ("primary", "secondary"):
            window = _normalize_window(
                bucket.get(slot),
                limit_id=limit_id,
                slot=slot,
                blocked_reason=blocked_reason,
            )
            if window is not None:
                windows.append(window)
    return QuotaSnapshot(observed_at=observed_at, windows=tuple(windows))


def select_five_hour(snapshot: QuotaSnapshot) -> FiveHourSelection:
    candidates = [
        window
        for window in snapshot.windows
        if window.duration_minutes is not None
        and 270 <= window.duration_minutes <= 330
    ]
    if not candidates:
        return FiveHourSelection(
            status="absent",
            window=None,
            reason="No window with a duration near 300 minutes is exposed.",
        )
    if len(candidates) == 1:
        return FiveHourSelection("selected", candidates[0], "Selected by window duration.")

    codex_candidates = [window for window in candidates if window.limit_id == "codex"]
    if len(codex_candidates) == 1:
        return FiveHourSelection(
            "selected",
            codex_candidates[0],
            "Multiple candidates exist; selected the sole official codex bucket.",
        )
    return FiveHourSelection(
        "ambiguous",
        None,
        "Multiple approximately five-hour windows are exposed and cannot be distinguished safely.",
    )


def _safe_limit_id(value: Any) -> str | None:
    if isinstance(value, str) and _SAFE_LIMIT_ID.fullmatch(value):
        return value
    return None


def _blocked_reason(bucket: dict[str, Any]) -> str | None:
    reason = bucket.get("rateLimitReachedType")
    if isinstance(reason, str) and reason in _BLOCK_REASONS:
        return reason
    if bucket.get("spendControlReached") is True:
        return "spend_control_reached"
    return None


def _normalize_window(
    value: Any,
    *,
    limit_id: str | None,
    slot: str,
    blocked_reason: str | None,
) -> QuotaWindow | None:
    if not isinstance(value, dict):
        return None
    used_percent = value.get("usedPercent")
    if not _is_int(used_percent) or not 0 <= used_percent <= 100:
        return None
    duration = value.get("windowDurationMins")
    if duration is not None and (not _is_int(duration) or duration <= 0):
        duration = None
    resets_at = value.get("resetsAt")
    if resets_at is not None and not _is_int(resets_at):
        resets_at = None
    return QuotaWindow(
        limit_id=limit_id,
        slot=slot,
        used_percent=used_percent,
        duration_minutes=duration,
        resets_at=resets_at,
        blocked_reason=blocked_reason,
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
