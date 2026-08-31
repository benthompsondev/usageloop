"""One-time cleanup for the retired Claude preview integration.

Only exact UsageLoop-owned files and status-line registrations are touched.
Unknown or malformed Claude settings fail closed and are left byte-for-byte
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Iterable

from .app_state import app_data_root


RETIRED_HELPER_NAME = "UsageLoopStatus.exe"
RETIRED_STATE_FILES = ("claude-status.json", "claude-attempts.jsonl")


@dataclass(frozen=True)
class CleanupResult:
    statusline_removed: bool
    state_files_removed: int


def remove_retired_claude_integration(
    *,
    settings_path: Path | None = None,
    app_data_dir: Path | None = None,
    owned_helper_paths: Iterable[Path] | None = None,
) -> CleanupResult:
    """Remove the old preview hook without touching user-owned Claude config."""
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    app_data_dir = app_data_dir or app_data_root()
    helper_paths = tuple(owned_helper_paths or _default_owned_helper_paths())

    removed_statusline = _remove_exact_statusline(settings_path, helper_paths)
    removed_files = 0
    for name in RETIRED_STATE_FILES:
        path = app_data_dir / name
        try:
            existed = path.is_file()
            path.unlink(missing_ok=True)
        except OSError:
            continue
        removed_files += int(existed)
    return CleanupResult(removed_statusline, removed_files)


def _default_owned_helper_paths() -> tuple[Path, ...]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (
        local / "Programs" / "UsageLoop" / RETIRED_HELPER_NAME,
        local / "Programs" / "Window Sentinel" / RETIRED_HELPER_NAME,
    )


def _remove_exact_statusline(settings_path: Path, helper_paths: tuple[Path, ...]) -> bool:
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False

    current = payload.get("statusLine")
    owned_entries = []
    for helper in helper_paths:
        command = f'"{helper}"'
        owned_entries.extend(
            (
                {"type": "command", "command": command},
                {"type": "command", "command": command, "refreshInterval": 30},
            )
        )
    if current not in owned_entries:
        return False

    updated = dict(payload)
    updated.pop("statusLine", None)
    temporary = settings_path.with_suffix(f"{settings_path.suffix}.usageloop.tmp")
    try:
        temporary.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, settings_path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
