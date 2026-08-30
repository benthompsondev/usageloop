"""Prompt-free Claude Code window initialization with durable one-shot guards."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import BinaryIO, Callable, Iterator
import uuid

from .app_state import ProviderViewState, default_app_state_path
from .providers import CompatibilityResult


INIT_ONLY_FLAG = "--init-only"
FIVE_HOURS_SECONDS = 18_000.0
WEEKLY_PROTECTION_PERCENT = 99
RESET_BUFFER_SECONDS = 15.0
RESERVATION_RECOVERY_SECONDS = 120.0
MAX_EVIDENCE_AGE_SECONDS = 6 * 60 * 60
ANCHOR_TOLERANCE_SECONDS = 120.0
_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")
_SAFE_KEY = re.compile(r"^[a-z0-9:_-]{1,96}$")
_STATES = {
    "reserved",
    "launch_attempted",
    "effect_possible",
    "verified",
    "failed_recoverable",
    "failed_guarded",
}
_TRANSITIONS = {
    "reserved": {"launch_attempted", "failed_recoverable"},
    "launch_attempted": {"effect_possible", "failed_recoverable", "failed_guarded"},
    "effect_possible": {"verified", "failed_guarded"},
    "verified": set(),
    "failed_recoverable": set(),
    "failed_guarded": set(),
}


class ClaudeStateError(RuntimeError):
    """The Claude attempt ledger could not be trusted."""


@dataclass(frozen=True)
class ClaudeAttempt:
    attempt_id: str
    mode: str
    idempotency_key: str
    state: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ClaudeProcessOutcome:
    terminal_outcome: str
    effect_possible: bool


@dataclass(frozen=True)
class ClaudeActionResult:
    outcome: str
    state: ProviderViewState
    effect_possible: bool


ProcessRunner = Callable[[list[str], Path, float], ClaudeProcessOutcome]
CapabilityChecker = Callable[[Path], bool]


def build_claude_init_command(executable: Path) -> list[str]:
    """Build the only Claude operation the app is allowed to launch."""
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            os.fspath(executable),
            INIT_ONLY_FLAG,
        ]
    return [os.fspath(executable), INIT_ONLY_FLAG]


def executable_supports_init_only(executable: Path) -> bool:
    """Find the installed runtime's hidden capability without launching Claude.

    The measured 2.1.x runtime supports ``--init-only`` while omitting it from
    ``--help``. Looking for the exact option token in the installed artifact is
    therefore safer than either version pinning or executing the effectful flag
    as a capability probe.
    """
    return any(_artifact_contains_init_only(path) for path in _capability_artifacts(executable))


def _capability_artifacts(executable: Path) -> tuple[Path, ...]:
    artifacts = [executable]
    if executable.suffix.lower() in {".cmd", ".bat"}:
        artifacts.append(
            executable.parent
            / "node_modules"
            / "@anthropic-ai"
            / "claude-code"
            / "cli.js"
        )
    return tuple(artifacts)


def _artifact_contains_init_only(artifact: Path) -> bool:
    needle = INIT_ONLY_FLAG.encode("ascii")
    overlap = len(needle) - 1
    tail = b""
    try:
        with artifact.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    return False
                block = tail + chunk
                if needle in block:
                    return True
                tail = block[-overlap:]
    except OSError:
        return False


def dedicated_claude_workspace() -> Path:
    return default_app_state_path().parent / "claude-trigger-workspace"


def default_claude_attempt_path() -> Path:
    return default_app_state_path().parent / "claude-attempts.jsonl"


class ClaudeAttemptStore:
    """Strict, provider-isolated JSONL state for exactly-once initialization."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_claude_attempt_path()

    @contextmanager
    def reservation_guard(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        with lock_path.open("a+b") as stream:
            _lock_file(stream)
            try:
                yield
            finally:
                _unlock_file(stream)

    def reserve(self, *, mode: str, idempotency_key: str, now: float) -> ClaudeAttempt:
        if mode not in {"bootstrap", "rollover"} or not _SAFE_KEY.fullmatch(idempotency_key):
            raise ValueError("Unsafe Claude attempt reservation.")
        attempt = ClaudeAttempt(uuid.uuid4().hex, mode, idempotency_key, "reserved", now, now)
        self._append(
            {
                "event": "claude_trigger_state",
                "provider": "claude",
                "attempt_id": attempt.attempt_id,
                "mode": mode,
                "idempotency_key": idempotency_key,
                "state": "reserved",
                "occurred_at": float(now),
            }
        )
        return attempt

    def transition(self, attempt_id: str, state: str, *, outcome: str, now: float) -> None:
        if not _SAFE_ID.fullmatch(attempt_id) or state not in _STATES or state == "reserved":
            raise ValueError("Unsafe Claude attempt transition.")
        attempts = {item.attempt_id: item for item in self.attempts()}
        current = attempts.get(attempt_id)
        if current is None or state not in _TRANSITIONS[current.state]:
            raise ClaudeStateError("Claude attempt history is inconsistent.")
        safe_outcome = outcome if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", outcome) else "unexpected_error"
        self._append(
            {
                "event": "claude_trigger_state",
                "provider": "claude",
                "attempt_id": attempt_id,
                "state": state,
                "outcome": safe_outcome,
                "occurred_at": float(now),
            }
        )

    def attempts(self) -> list[ClaudeAttempt]:
        if not self.path.exists():
            return []
        attempts: dict[str, ClaudeAttempt] = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ClaudeStateError("Claude attempt history is unavailable.") from exc
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ClaudeStateError("Claude attempt history is malformed.") from exc
            if not isinstance(row, dict) or row.get("event") != "claude_trigger_state":
                raise ClaudeStateError("Claude attempt history is malformed.")
            attempt_id = row.get("attempt_id")
            state = row.get("state")
            occurred_at = row.get("occurred_at")
            if (
                not isinstance(attempt_id, str)
                or not _SAFE_ID.fullmatch(attempt_id)
                or state not in _STATES
                or not isinstance(occurred_at, (int, float))
                or isinstance(occurred_at, bool)
            ):
                raise ClaudeStateError("Claude attempt history is malformed.")
            if state == "reserved":
                mode = row.get("mode")
                key = row.get("idempotency_key")
                if mode not in {"bootstrap", "rollover"} or not isinstance(key, str) or not _SAFE_KEY.fullmatch(key):
                    raise ClaudeStateError("Claude attempt history is malformed.")
                if attempt_id in attempts:
                    raise ClaudeStateError("Claude attempt history is inconsistent.")
                attempts[attempt_id] = ClaudeAttempt(
                    attempt_id, mode, key, state, float(occurred_at), float(occurred_at)
                )
                continue
            current = attempts.get(attempt_id)
            if current is None or state not in _TRANSITIONS[current.state]:
                raise ClaudeStateError("Claude attempt history is inconsistent.")
            attempts[attempt_id] = replace(current, state=state, updated_at=float(occurred_at))
        return sorted(attempts.values(), key=lambda item: (item.created_at, item.attempt_id))

    def _append(self, row: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


class ClaudeInitTrigger:
    def __init__(
        self,
        executable: Path,
        workspace: Path,
        *,
        process_runner: ProcessRunner | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.executable = executable
        self.workspace = workspace
        self.process_runner = process_runner or _run_process
        self.timeout_seconds = timeout_seconds

    def run(self) -> ClaudeProcessOutcome:
        if not _prepare_workspace(self.workspace):
            return ClaudeProcessOutcome("workspace_unavailable", False)
        try:
            return self.process_runner(
                build_claude_init_command(self.executable),
                self.workspace,
                self.timeout_seconds,
            )
        except OSError:
            return ClaudeProcessOutcome("launch_failed", False)


class ClaudeOperationRunner:
    def __init__(
        self,
        *,
        attempt_store: ClaudeAttemptStore | None = None,
        capability_checker: CapabilityChecker | None = None,
        process_runner: ProcessRunner | None = None,
        workspace: Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.attempt_store = attempt_store or ClaudeAttemptStore()
        self.capability_checker = capability_checker or executable_supports_init_only
        self.process_runner = process_runner
        self.workspace = workspace or dedicated_claude_workspace()
        self.clock = clock

    def probe(self, runtime_identity: str, executable: Path) -> CompatibilityResult:
        if self.capability_checker(executable):
            return CompatibilityResult(
                True,
                "Waiting",
                "Compatibility check passed. prompt-free Claude initialization can be used.",
                runtime_identity,
            )
        return CompatibilityResult(
            False,
            "Needs attention",
            "Claude initialization support was unavailable or ambiguous, so automation is paused.",
            runtime_identity,
        )

    def run(
        self,
        mode: str,
        *,
        executable: Path,
        runtime_identity: str,
        state: ProviderViewState,
    ) -> ClaudeActionResult:
        if mode not in {"bootstrap", "rollover"}:
            raise ValueError("Unsupported Claude operation mode.")
        now = self.clock()
        eligible = self._eligibility(mode, state, now)
        if eligible is not None:
            outcome, detail = eligible
            return ClaudeActionResult(outcome, replace(state, detail=detail), False)
        key = (
            f"rollover:{int(state.reset_at)}"
            if mode == "rollover" and state.reset_at is not None
            else f"bootstrap:{int(now // FIVE_HOURS_SECONDS)}"
        )
        try:
            with self.attempt_store.reservation_guard():
                blocking = self._blocking_attempt(mode, key, now)
                if blocking is not None:
                    guard_until = blocking.created_at + FIVE_HOURS_SECONDS
                    return ClaudeActionResult(
                        "ATTEMPT_ALREADY_RECORDED",
                        replace(
                            state,
                            status="Waiting",
                            detail="Claude initialization may already have run. It will not be repeated.",
                            automation_blocked_until=guard_until,
                            last_action="Initialization guarded",
                        ),
                        False,
                    )
                attempt = self.attempt_store.reserve(mode=mode, idempotency_key=key, now=now)
                self.attempt_store.transition(
                    attempt.attempt_id,
                    "launch_attempted",
                    outcome="launch_attempted",
                    now=now,
                )
        except ClaudeStateError:
            return ClaudeActionResult(
                "STATE_UNAVAILABLE",
                replace(
                    state,
                    status="Needs attention",
                    detail="Claude attempt history could not be trusted, so automation is paused.",
                ),
                False,
            )

        trigger = ClaudeInitTrigger(
            executable,
            self.workspace,
            process_runner=self.process_runner,
        )
        process = trigger.run()
        if not process.effect_possible:
            self.attempt_store.transition(
                attempt.attempt_id,
                "failed_recoverable",
                outcome=process.terminal_outcome,
                now=self.clock(),
            )
            return ClaudeActionResult(
                "INITIALIZATION_NOT_STARTED",
                replace(
                    state,
                    status="Needs attention",
                    detail="Claude initialization did not start. No automatic retry will run.",
                    last_action="Initialization not started",
                    retry_after_restart=process.terminal_outcome
                    in {"launch_failed", "workspace_unavailable"},
                ),
                False,
            )

        completed_at = self.clock()
        self.attempt_store.transition(
            attempt.attempt_id,
            "effect_possible",
            outcome=process.terminal_outcome,
            now=completed_at,
        )
        return ClaudeActionResult(
            "INITIALIZATION_POSSIBLE",
            replace(
                state,
                status="Waiting",
                detail="Initialization ran once. Waiting for the next safe Claude status update.",
                reset_at=None,
                last_action="Initialization ran once",
                automation_blocked_until=completed_at + FIVE_HOURS_SECONDS,
            ),
            True,
        )

    def verify_observed_reset(self, reset_at: int, *, observed_at: float) -> bool:
        """Mark one possible initialization verified from an absolute reset."""
        attempts = self.attempt_store.attempts()
        for attempt in reversed(attempts):
            if attempt.state not in {"effect_possible", "verified"}:
                continue
            if abs(reset_at - (attempt.created_at + FIVE_HOURS_SECONDS)) > ANCHOR_TOLERANCE_SECONDS:
                continue
            if attempt.state == "effect_possible":
                self.attempt_store.transition(
                    attempt.attempt_id,
                    "verified",
                    outcome="anchor_verified",
                    now=observed_at,
                )
            return True
        return False

    @staticmethod
    def _eligibility(
        mode: str, state: ProviderViewState, now: float
    ) -> tuple[str, str] | None:
        if state.reset_at is not None and state.reset_at > now:
            return "ALREADY_READY", "The Claude five-hour window is already counting down."
        if (
            state.weekly_used_percent is None
            or state.weekly_reset_at is None
            or state.weekly_reset_at <= now
            or state.usage_checked_at is None
            or now - state.usage_checked_at > MAX_EVIDENCE_AGE_SECONDS
        ):
            return "WEEKLY_UNAVAILABLE", "A current weekly Claude limit was not available, so nothing was done."
        if state.weekly_used_percent >= WEEKLY_PROTECTION_PERCENT:
            return "WEEKLY_EXHAUSTED", "Weekly protection blocked Claude initialization."
        if mode == "bootstrap":
            if state.reset_at is not None or state.used_percent != 0 or state.usage_checked_at is None:
                return "BOOTSTRAP_NOT_ELIGIBLE", "A safe fresh Claude window state was not available."
        else:
            if state.reset_at is None:
                return "ROLLOVER_UNKNOWN", "The previous Claude reset boundary was not known."
            if now < state.reset_at + RESET_BUFFER_SECONDS:
                return "RESET_BUFFER", "Waiting for the reset buffer to clear."
        return None

    def _blocking_attempt(
        self, mode: str, key: str, now: float
    ) -> ClaudeAttempt | None:
        blocking: list[ClaudeAttempt] = []
        for attempt in self.attempt_store.attempts():
            relevant = (
                attempt.idempotency_key == key
                if mode == "rollover"
                else attempt.created_at >= now - FIVE_HOURS_SECONDS
            )
            if not relevant:
                continue
            if attempt.state == "reserved" and now - attempt.updated_at >= RESERVATION_RECOVERY_SECONDS:
                self.attempt_store.transition(
                    attempt.attempt_id,
                    "failed_recoverable",
                    outcome="reservation_recovered",
                    now=now,
                )
            elif attempt.state != "failed_recoverable":
                blocking.append(attempt)
        return blocking[-1] if blocking else None


def _run_process(command: list[str], workspace: Path, timeout: float) -> ClaudeProcessOutcome:
    try:
        result = subprocess.run(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except subprocess.TimeoutExpired:
        return ClaudeProcessOutcome("process_timeout", True)
    if result.returncode == 0:
        return ClaudeProcessOutcome("process_exited_zero", True)
    stderr = (result.stderr or "").lower()
    parser_failure = INIT_ONLY_FLAG in stderr and any(
        marker in stderr
        for marker in ("unknown option", "unknown argument", "unrecognized option")
    )
    if parser_failure:
        return ClaudeProcessOutcome("init_only_unsupported", False)
    return ClaudeProcessOutcome("process_exited_nonzero", True)


def _prepare_workspace(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        is_junction = getattr(path, "is_junction", None)
        if path.is_symlink() or (callable(is_junction) and is_junction()):
            return False
        return path.is_dir() and next(path.iterdir(), None) is None
    except OSError:
        return False


def _lock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _unlock_file(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
