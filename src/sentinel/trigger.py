"""One minimal subscription-backed Codex turn used to anchor a new window.

Sentinel drives the local `codex app-server` protocol directly. Codex owns
authentication and the model request exactly as it does for observation, so the
trigger and the observer share one process, one handshake, and one auth owner.

An earlier design drove the interactive TUI through a Windows pseudo console and
parsed rendered screens. Measured evidence retired it: the terminal separates
words with cursor-forward escapes rather than spaces, and update, directory
trust, and model-deprecation screens sit between launch and the composer. See
`docs/TUI_TRIGGER_POSTMORTEM.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import ModelChoice, select_trigger_model
from .protocol import AppServerProtocolError, AppServerRequestRejected
from .transport import SentinelRuntimeError


UNRESOLVED_MODEL = "unresolved"


@dataclass(frozen=True)
class TriggerConfig:
    prompt: str = "ok"
    turn_timeout_seconds: float = 90.0
    turn_trigger: str = "codex-window-sentinel"


@dataclass(frozen=True)
class TriggerDescription:
    mechanism: str
    model: str
    reasoning_effort: str
    prompt_characters: int


@dataclass(frozen=True)
class TriggerRunResult:
    terminal_outcome: str
    request_possibly_sent: bool


class Trigger(Protocol):
    def describe(self) -> TriggerDescription: ...
    def run(self) -> TriggerRunResult: ...


class TurnClient(Protocol):
    def list_models(self) -> list[dict]: ...
    def start_thread(self, params: dict) -> str: ...
    def start_turn(self, params: dict) -> None: ...
    def await_turn_end(self, *, timeout: float) -> str: ...


def dedicated_trigger_workspace(history_path: Path) -> Path:
    return history_path.parent / "trigger-workspace"


class AppServerTrigger:
    """Start one ephemeral thread and submit exactly one minimal turn."""

    def __init__(
        self,
        client: TurnClient,
        workspace: Path,
        config: TriggerConfig | None = None,
    ):
        self.client = client
        self.workspace = workspace
        self.config = config or TriggerConfig()
        self._choice: ModelChoice | None = None
        self._resolved = False

    def resolve_model(self) -> ModelChoice | None:
        """Resolve once per trigger instance. `model/list` spends no quota."""
        if not self._resolved:
            self._resolved = True
            try:
                self._choice = select_trigger_model(self.client.list_models())
            except (AppServerProtocolError, SentinelRuntimeError, OSError):
                self._choice = None
        return self._choice

    def describe(self) -> TriggerDescription:
        choice = self.resolve_model()
        return TriggerDescription(
            mechanism="app_server_turn",
            model=choice.model if choice else UNRESOLVED_MODEL,
            reasoning_effort=(choice.reasoning_effort if choice and choice.reasoning_effort else "default"),
            prompt_characters=len(self.config.prompt),
        )

    def thread_parameters(self, choice: ModelChoice) -> dict:
        # `allowProviderModelFallback` is deliberately absent: it requires the
        # `experimentalApi` capability that Sentinel opts out of, and the
        # resolved model is already a current non-superseded default.
        return {
            "ephemeral": True,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "cwd": str(self.workspace),
            "config": {"mcp_servers": {}},
            "model": choice.model,
        }

    def turn_parameters(self, thread_id: str, choice: ModelChoice) -> dict:
        params: dict = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": self.config.prompt}],
            "turnTrigger": self.config.turn_trigger,
        }
        if choice.reasoning_effort is not None:
            params["effort"] = choice.reasoning_effort
        return params

    def run(self) -> TriggerRunResult:
        choice = self.resolve_model()
        if choice is None:
            return TriggerRunResult("model_unavailable", False)

        if not self._prepare_workspace():
            return TriggerRunResult("workspace_unavailable", False)

        try:
            thread_id = self.client.start_thread(self.thread_parameters(choice))
        except AppServerRequestRejected:
            return TriggerRunResult("thread_start_rejected", False)
        except (AppServerProtocolError, SentinelRuntimeError, OSError):
            return TriggerRunResult("thread_start_failed", False)

        try:
            self.client.start_turn(self.turn_parameters(thread_id, choice))
        except AppServerRequestRejected as exc:
            # Codex refused the request itself, so no model work began and the
            # opportunity stays recoverable. Any other rejection may have
            # reached the model, so it is treated as possibly sent.
            if exc.rejected_before_dispatch:
                return TriggerRunResult("turn_start_rejected", False)
            return TriggerRunResult("turn_start_error", True)
        except (AppServerProtocolError, SentinelRuntimeError, OSError):
            return TriggerRunResult("turn_start_unconfirmed", True)

        try:
            outcome = self.client.await_turn_end(timeout=self.config.turn_timeout_seconds)
        except (AppServerProtocolError, OSError):
            outcome = "turn_stream_unavailable"
        # Every path below has transmitted one turn. The quota observer, not
        # this lifecycle signal, decides whether the window actually anchored.
        return TriggerRunResult(outcome, True)

    def _prepare_workspace(self) -> bool:
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            is_junction = getattr(self.workspace, "is_junction", None)
            if self.workspace.is_symlink() or (callable(is_junction) and is_junction()):
                return False
            return self.workspace.is_dir() and next(self.workspace.iterdir(), None) is None
        except OSError:
            return False
