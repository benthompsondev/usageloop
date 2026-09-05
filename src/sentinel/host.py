"""What the running host is called and where its per-user directories live.

UsageLoop is one product with one core. This module holds the few facts that
genuinely differ between a Windows and a Linux desktop session, so the rest of
the app can ask instead of branching on `os.name` in a dozen places.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


WINDOWS = "Windows"
LINUX = "Linux"


def is_windows() -> bool:
    return os.name == "nt"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def platform_label() -> str:
    """The host name shown in Settings, About, and the diagnostic summary."""
    if is_windows():
        return WINDOWS
    if is_linux():
        return LINUX
    return "Desktop"


def xdg_home(variable: str, fallback: Path) -> Path:
    """Honor an XDG variable only when it is absolute, as the spec requires.

    A relative value is not merely unusual, it is undefined, and resolving it
    against the working directory would scatter state wherever UsageLoop
    happened to be launched from.
    """
    configured = os.environ.get(variable, "").strip()
    candidate = Path(configured) if configured else None
    if candidate is not None and candidate.is_absolute():
        return candidate
    return fallback


def xdg_state_home() -> Path:
    return xdg_home("XDG_STATE_HOME", Path.home() / ".local" / "state")


def xdg_data_home() -> Path:
    return xdg_home("XDG_DATA_HOME", Path.home() / ".local" / "share")


def xdg_config_home() -> Path:
    return xdg_home("XDG_CONFIG_HOME", Path.home() / ".config")


def xdg_runtime_dir() -> Path | None:
    """The session runtime directory, when the session actually provides one."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    candidate = Path(runtime) if runtime else None
    return candidate if candidate is not None and candidate.is_absolute() else None
