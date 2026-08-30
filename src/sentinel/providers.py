"""Narrow, presentation-safe provider adapters for the desktop app."""

from __future__ import annotations

from dataclasses import dataclass, replace
import ctypes
from pathlib import Path
import shutil
import time
from typing import Callable

from .app_state import ProviderViewState
from .classifier import classify
from .history import SafeHistory
from .quota import select_five_hour
from .transport import find_codex_executable


ExecutableFinder = Callable[[], Path | None]
IdentityReader = Callable[[Path], str]
VersionReader = Callable[[Path], str | None]


@dataclass(frozen=True)
class CompatibilityResult:
    compatible: bool
    status: str
    detail: str
    runtime_identity: str

    @classmethod
    def from_capabilities(
        cls,
        *,
        runtime_identity: str,
        initialized: bool,
        rate_limits_available: bool | None,
        model_catalog_available: bool,
        suitable_model_available: bool,
    ) -> "CompatibilityResult":
        checks = (
            initialized,
            rate_limits_available is True,
            model_catalog_available,
            suitable_model_available,
        )
        if all(checks):
            return cls(
                True,
                "Waiting",
                "Compatibility check passed. Sentinel will use the guarded Codex path.",
                runtime_identity,
            )
        return cls(
            False,
            "Needs attention",
            "Codex capabilities were missing or ambiguous, so automatic actions are paused.",
            runtime_identity,
        )


class CodexProvider:
    provider_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        *,
        history: SafeHistory | None = None,
        executable_finder: ExecutableFinder | None = None,
        identity_reader: IdentityReader | None = None,
        version_reader: VersionReader | None = None,
        capability_probe: Callable[[], CompatibilityResult] | None = None,
        operation_runner: object | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.history = history or SafeHistory()
        self._find = executable_finder or find_codex_executable
        self._identity = identity_reader or file_runtime_identity
        self._version = version_reader or windows_file_version
        self._probe = capability_probe
        self._runner = operation_runner
        self._now = now

    def detect(self) -> ProviderViewState:
        executable = self._find()
        if executable is None:
            return ProviderViewState.waiting(
                self.provider_id, self.display_name, installed=False
            )
        state = ProviderViewState.waiting(
            self.provider_id,
            self.display_name,
            installed=True,
            runtime_identity=self._identity(executable),
        )
        state = replace(state, runtime_version=self._version(executable))
        snapshots = self.history.load_recent(now=self._now(), limit=4)
        if not snapshots:
            return state
        classification = classify(snapshots)
        latest = snapshots[-1]
        selected = select_five_hour(latest).window
        if selected is None:
            return state
        status = {
            "ANCHORED": "Ready",
            "UNANCHORED": "Waiting",
            "EXHAUSTED": "Needs attention",
        }.get(classification.state, "Needs attention")
        detail = {
            "ANCHORED": "The last verified five-hour window is ready.",
            "UNANCHORED": "The last check did not prove a fixed reset.",
            "EXHAUSTED": "The last check reported that this window was exhausted.",
        }.get(classification.state, "The last provider check was inconclusive.")
        return ProviderViewState(
            provider_id=state.provider_id,
            display_name=state.display_name,
            installed=True,
            automation_supported=True,
            status=status,
            detail=detail,
            runtime_identity=state.runtime_identity,
            runtime_version=state.runtime_version,
            reset_at=selected.resets_at,
            last_verified_at=latest.observed_at if classification.state == "ANCHORED" else None,
            used_percent=selected.used_percent,
            usage_checked_at=latest.observed_at,
        )

    def probe(self) -> CompatibilityResult:
        state = self.detect()
        identity = state.runtime_identity or "unavailable"
        if self._probe is not None:
            return self._probe()
        return self._operation_runner().probe(identity)

    def run_action(self, mode: str):
        state = self.detect()
        identity = state.runtime_identity or "unavailable"
        result = self._operation_runner().run(mode, runtime_identity=identity)
        return replace(
            result,
            state=replace(result.state, runtime_version=state.runtime_version),
        )

    def _operation_runner(self):
        if self._runner is None:
            from .provider_runtime import CodexOperationRunner

            self._runner = CodexOperationRunner(self.history)
        return self._runner


class ClaudeProvider:
    provider_id = "claude"
    display_name = "Claude Code"

    def __init__(
        self,
        *,
        executable_finder: ExecutableFinder | None = None,
        identity_reader: IdentityReader | None = None,
        version_reader: VersionReader | None = None,
    ):
        self._find = executable_finder or find_claude_executable
        self._identity = identity_reader or file_runtime_identity
        self._version = version_reader or windows_file_version

    def detect(self) -> ProviderViewState:
        executable = self._find()
        if executable is None:
            return ProviderViewState(
                self.provider_id,
                self.display_name,
                False,
                False,
                "Needs attention",
                "Claude Code was not found on this PC.",
            )
        return ProviderViewState(
            self.provider_id,
            self.display_name,
            True,
            False,
            "Needs attention",
            "Detected. Automatic anchoring awaits one exact fresh-window proof.",
            self._identity(executable),
            self._version(executable),
        )


def find_claude_executable() -> Path | None:
    home = Path.home()
    candidates = (
        home / ".local" / "bin" / "claude.exe",
        home / "AppData" / "Local" / "Programs" / "Claude" / "claude.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    resolved = shutil.which("claude.exe") or shutil.which("claude")
    return Path(resolved) if resolved else None


def file_runtime_identity(executable: Path) -> str:
    try:
        stat = executable.stat()
    except OSError:
        return "unavailable"
    return f"file:{stat.st_size}:{stat.st_mtime_ns}"


def windows_file_version(executable: Path) -> str | None:
    class FixedFileInfo(ctypes.Structure):
        _fields_ = [
            ("signature", ctypes.c_uint32),
            ("struct_version", ctypes.c_uint32),
            ("file_version_ms", ctypes.c_uint32),
            ("file_version_ls", ctypes.c_uint32),
            ("product_version_ms", ctypes.c_uint32),
            ("product_version_ls", ctypes.c_uint32),
            ("file_flags_mask", ctypes.c_uint32),
            ("file_flags", ctypes.c_uint32),
            ("file_os", ctypes.c_uint32),
            ("file_type", ctypes.c_uint32),
            ("file_subtype", ctypes.c_uint32),
            ("file_date_ms", ctypes.c_uint32),
            ("file_date_ls", ctypes.c_uint32),
        ]

    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size = version.GetFileVersionInfoSizeW(str(executable), None)
        if not size:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(executable), 0, size, buffer):
            return None
        pointer = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return None
        info = ctypes.cast(pointer, ctypes.POINTER(FixedFileInfo)).contents
    except (AttributeError, OSError, ValueError):
        return None
    values = (
        info.product_version_ms >> 16,
        info.product_version_ms & 0xFFFF,
        info.product_version_ls >> 16,
        info.product_version_ls & 0xFFFF,
    )
    return ".".join(str(value) for value in values)
