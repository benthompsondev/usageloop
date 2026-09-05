"""Narrow, presentation-safe provider adapters for the desktop app."""

from __future__ import annotations

from dataclasses import dataclass, replace
import ctypes
from pathlib import Path
import time
from typing import Callable

from .app_state import ProviderViewState
from .classifier import classify
from .history import SafeHistory
from .quota import select_five_hour, select_weekly
from .transport import find_codex_executable


ExecutableFinder = Callable[[], Path | None]
IdentityReader = Callable[[Path], str]
VersionReader = Callable[[Path], str | None]

LIGHTWEIGHT_MODEL_UNAVAILABLE_DETAIL = (
    "No supported lightweight trigger model and reasoning level are available. "
    "Automatic starts are paused. No Codex request was sent because UsageLoop "
    "will not use a higher-cost model. Check for updates because Codex's model "
    "lineup may have changed."
)


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
                "Compatibility check passed. The guarded Codex path will be used.",
                runtime_identity,
            )
        if initialized and rate_limits_available is True and model_catalog_available and not suitable_model_available:
            return cls(
                False,
                "Needs attention",
                LIGHTWEIGHT_MODEL_UNAVAILABLE_DETAIL,
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
        weekly = select_weekly(latest)
        if selected is None:
            return state
        status = {
            "ANCHORED": "Ready",
            "UNANCHORED": "Waiting",
            # Exhaustion is a real, conclusive quota state, not a permanent
            # compatibility or transport failure. Once its reset passes, the
            # scheduler must be allowed to re-read until Codex exposes the new
            # unanchored window.
            "EXHAUSTED": "Waiting",
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
            weekly_used_percent=weekly.used_percent if weekly else None,
            weekly_reset_at=weekly.resets_at if weekly else None,
            last_action=_latest_trigger_action(self.history),
            quota_state=classification.state,
        )

    def probe(self) -> CompatibilityResult:
        state = self.detect()
        identity = state.runtime_identity or "unavailable"
        if self._probe is not None:
            return self._probe()
        return self._operation_runner().probe(identity)

    def run_action(self, mode: str, *, current_state: ProviderViewState | None = None):
        state = self.detect()
        identity = state.runtime_identity or "unavailable"
        result = self._operation_runner().run(mode, runtime_identity=identity)
        result_state = result.state
        if (
            result.outcome not in {"ALREADY_ANCHORED", "ANCHOR_VERIFIED"}
            and current_state is not None
            and current_state.reset_at is not None
        ):
            # Sliding UNANCHORED resets are evidence for the classifier, not a
            # new authoritative countdown. Keep showing the last verified
            # boundary until post-trigger observations prove a replacement.
            result_state = replace(
                result_state,
                reset_at=current_state.reset_at,
                last_verified_at=current_state.last_verified_at,
            )
        return replace(
            result,
            state=replace(result_state, runtime_version=state.runtime_version),
        )

    def sync_usage(self, *, current_state: ProviderViewState | None = None):
        detected = current_state if current_state is not None else self.detect()
        identity = detected.runtime_identity or "unavailable"
        result = self._operation_runner().sync(identity)
        if result.outcome == "SYNC_INCONCLUSIVE" and current_state is not None:
            synced_state = replace(
                current_state,
                detail=result.state.detail,
                runtime_identity=identity,
                runtime_version=detected.runtime_version,
                automation_supported=detected.automation_supported,
            )
        else:
            synced_state = replace(
                result.state,
                runtime_version=detected.runtime_version,
                automation_supported=detected.automation_supported,
                last_action=(
                    current_state.last_action
                    if current_state is not None
                    else detected.last_action
                ),
            )
        return replace(result, state=synced_state)

    def _operation_runner(self):
        if self._runner is None:
            from .provider_runtime import CodexOperationRunner

            self._runner = CodexOperationRunner(self.history)
        return self._runner


def file_runtime_identity(executable: Path) -> str:
    try:
        stat = executable.stat()
    except OSError:
        return "unavailable"
    return f"file:{stat.st_size}:{stat.st_mtime_ns}"


def _latest_trigger_action(history: SafeHistory) -> str | None:
    attempts = history.trigger_attempts()
    if not attempts:
        return None
    return {
        "reserved": "Preparing the next window",
        "launch_attempted": "Starting the next window",
        "request_possibly_sent": "Start outcome unclear; no retry",
        "verified": "Started and verified the next window",
        "failed_recoverable": "Start stopped before a request was sent",
        "failed_guarded": "Start not verified; no retry",
    }.get(attempts[-1].state)


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
