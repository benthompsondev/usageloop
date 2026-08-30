"""Allowlisted Claude statusLine quota cache with no credential access."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .app_state import default_app_state_path
from .product import PRODUCT


@dataclass(frozen=True)
class ClaudeQuotaStatus:
    observed_at: float
    five_hour_used_percent: float | None
    last_five_hour_reset_at: int | None
    weekly_used_percent: float | None
    weekly_reset_at: int | None


def default_claude_status_path() -> Path:
    return default_app_state_path().parent / "claude-status.json"


class ClaudeStatusStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_claude_status_path()

    def record_statusline(self, payload: object, *, observed_at: float | None = None) -> bool:
        if not isinstance(payload, dict):
            return False
        limits = payload.get("rate_limits")
        if not isinstance(limits, dict):
            return False
        timestamp = time.time() if observed_at is None else float(observed_at)
        previous = self.load()
        five = _window(limits.get("five_hour"))
        weekly = _window(limits.get("seven_day"))
        last_reset = (
            five[1]
            if five is not None and five[1] is not None
            else (previous.last_five_hour_reset_at if previous is not None else None)
        )
        row = {
            "schema_version": 1,
            "observed_at": timestamp,
            "five_hour_used_percent": five[0] if five is not None else None,
            "last_five_hour_reset_at": last_reset,
            "weekly_used_percent": weekly[0] if weekly is not None else None,
            "weekly_reset_at": weekly[1] if weekly is not None else None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        try:
            temporary.write_text(
                json.dumps(row, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return True

    def load(self) -> ClaudeQuotaStatus | None:
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(row, dict) or row.get("schema_version") != 1:
            return None
        observed_at = _number(row.get("observed_at"))
        if observed_at is None:
            return None
        five_used = _percentage(row.get("five_hour_used_percent"))
        five_reset = _epoch(row.get("last_five_hour_reset_at"))
        weekly_used = _percentage(row.get("weekly_used_percent"))
        weekly_reset = _epoch(row.get("weekly_reset_at"))
        return ClaudeQuotaStatus(
            observed_at,
            five_used,
            five_reset,
            weekly_used,
            weekly_reset,
        )


@dataclass(frozen=True)
class StatusLineRegistration:
    compatible: bool
    detail: str


class ClaudeStatusLineIntegration:
    """Register the helper only when no user statusLine would be replaced."""

    def __init__(
        self,
        settings_path: Path | None = None,
        command: str | None = None,
    ):
        self.settings_path = settings_path or Path.home() / ".claude" / "settings.json"
        self.command = command or default_statusline_command()

    def ensure_registered(self) -> StatusLineRegistration:
        settings = self._load_settings()
        if settings is None:
            return StatusLineRegistration(
                False,
                "Claude settings could not be read safely, so nothing was changed.",
            )
        current = settings.get("statusLine")
        expected = {"type": "command", "command": self.command}
        if current == expected:
            return StatusLineRegistration(True, "Claude status caching is ready.")
        if current is not None:
            return StatusLineRegistration(
                False,
                "Claude already has a custom status line. It will not be replaced.",
            )
        settings["statusLine"] = expected
        temporary = self.settings_path.with_suffix(f"{self.settings_path.suffix}.sentinel.tmp")
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(settings, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.settings_path)
        except OSError:
            temporary.unlink(missing_ok=True)
            return StatusLineRegistration(
                False,
                "Claude settings could not be updated safely, so nothing was changed.",
            )
        return StatusLineRegistration(True, "Claude status caching is ready.")

    def remove_if_owned(self) -> bool:
        settings = self._load_settings()
        if settings is None:
            return False
        expected = {"type": "command", "command": self.command}
        if settings.get("statusLine") != expected:
            return False
        settings.pop("statusLine")
        temporary = self.settings_path.with_suffix(f"{self.settings_path.suffix}.sentinel.tmp")
        try:
            temporary.write_text(
                json.dumps(settings, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.settings_path)
        except OSError:
            temporary.unlink(missing_ok=True)
            return False
        return True

    def _load_settings(self) -> dict[str, Any] | None:
        if not self.settings_path.exists():
            return {}
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


def default_statusline_command() -> str:
    if getattr(sys, "frozen", False):
        helper = Path(sys.executable).with_name(PRODUCT.claude_status_helper_name)
        return f'"{helper}"'
    sentinel = Path(sys.executable).with_name("sentinel.exe")
    return f'"{sentinel}" claude-statusline-record'


def render_statusline(payload: object, *, now: float | None = None) -> str:
    current = time.time() if now is None else now
    if not isinstance(payload, dict) or not isinstance(payload.get("rate_limits"), dict):
        return f"{PRODUCT.display_name} | Claude status unavailable"
    five = _window(payload["rate_limits"].get("five_hour"))
    if five is None or five[1] is None:
        return f"{PRODUCT.display_name} | Claude window waiting"
    remaining = max(0, int(five[1] - current))
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    return f"{PRODUCT.display_name} | Claude {hours}h {minutes:02d}m"


def _window(value: object) -> tuple[float | None, int | None] | None:
    if not isinstance(value, dict):
        return None
    return _percentage(value.get("used_percentage")), _epoch(value.get("resets_at"))


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _percentage(value: object) -> float | None:
    number = _number(value)
    if number is None or not 0 <= number <= 100:
        return None
    return number


def _epoch(value: object) -> int | None:
    number = _number(value)
    if number is None:
        return None
    if number > 10_000_000_000:
        number /= 1000
    parsed = int(number)
    return parsed if parsed > 0 else None
