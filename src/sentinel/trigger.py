"""A minimal normal interactive Codex request used to anchor a new window."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ._conpty import ConPtyError, conpty_available, run_conpty
from .transport import build_codex_command


@dataclass(frozen=True)
class TriggerConfig:
    # The TUI/quiet defaults adapt CCLimitPing's MIT-licensed Codex trigger.
    # See THIRD_PARTY_NOTICES.md for attribution.
    model: str = "gpt-5.4-mini"
    reasoning_effort: str = "low"
    prompt: str = "ok"
    min_runtime_seconds: float = 4.0
    quiet_seconds: float = 2.5
    max_runtime_seconds: float = 45.0
    exit_grace_seconds: float = 5.0


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


class InteractiveCodexTrigger:
    """Run the base Codex TUI inside a Windows pseudo console, never `exec`."""

    def __init__(
        self,
        executable: Path,
        workspace: Path,
        config: TriggerConfig | None = None,
    ):
        self.executable = executable
        self.workspace = workspace
        self.config = config or TriggerConfig()

    def describe(self) -> TriggerDescription:
        return TriggerDescription(
            mechanism="interactive_codex_tui_conpty",
            model=self.config.model,
            reasoning_effort=self.config.reasoning_effort,
            prompt_characters=len(self.config.prompt),
        )

    def command(self) -> list[str]:
        arguments = [
            "-c",
            f"model_reasoning_effort={self.config.reasoning_effort}",
            "-m",
            self.config.model,
            "--no-alt-screen",
            "-s",
            "read-only",
            "-a",
            "never",
            "-C",
            str(self.workspace),
            self.config.prompt,
        ]
        return build_codex_command(self.executable, *arguments)

    def run(self) -> TriggerRunResult:
        if not conpty_available():
            return TriggerRunResult("interactive_tty_unavailable", False)
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            result = run_conpty(
                self.command(),
                cwd=self.workspace,
                min_runtime_seconds=self.config.min_runtime_seconds,
                quiet_seconds=self.config.quiet_seconds,
                max_runtime_seconds=self.config.max_runtime_seconds,
                exit_grace_seconds=self.config.exit_grace_seconds,
            )
        except ConPtyError as exc:
            return TriggerRunResult(
                "runtime_error" if exc.process_started else "launch_failed",
                exc.process_started,
            )
        except OSError:
            return TriggerRunResult("launch_failed", False)
        outcome = result.terminal_outcome
        if outcome in {"process_exited", "process_exited_early"} and result.exit_code != 0:
            outcome += "_nonzero"
        return TriggerRunResult(outcome, True)
