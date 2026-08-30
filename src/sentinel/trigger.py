"""A minimal normal interactive Codex request used to anchor a new window."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping, Protocol

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
    allow_workspace_trust: bool = True


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


_TERM_WARNING = 'TERM is set to "dumb"'
_TRUST_QUESTION = "Do you trust the contents of this directory?"
_TRUST_EXPLANATION = (
    "Working with untrusted contents comes with higher risk of prompt injection. "
    "Trusting the directory allows project-local config, hooks, and exec policies to load."
)
_ANSI_OSC = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class CodexTuiController:
    """Recognize only the bounded Codex startup states needed by Sentinel."""

    def __init__(self, workspace: Path, *, allow_workspace_trust: bool):
        self.workspace = workspace
        self.allow_workspace_trust = allow_workspace_trust
        self.events: list[str] = ["terminal_startup"]
        self.failure_outcome: str | None = None
        self.success_outcome: str | None = None
        self.request_possibly_sent = False
        self._visible = ""
        self._trust_response_sent = False

    @property
    def stop_outcome(self) -> str | None:
        return self.failure_outcome or self.success_outcome

    @property
    def response_delay_seconds(self) -> float:
        # Codex renders the protected trust screen, then drains input that may
        # belong to the preceding screen. The one confirmation must arrive after
        # that bounded transition, never in the same output callback.
        return 0.5

    def receive(self, chunk: bytes) -> bytes:
        if self.stop_outcome is not None:
            return b""
        self._visible = (self._visible + _visible_terminal_text(chunk))[-32_768:]
        normalized = " ".join(self._visible.split())

        if _TERM_WARNING in normalized:
            self.failure_outcome = "unexpected_term_warning"
            self.events.append("term_warning")
            return b""

        if (
            not self._trust_response_sent
            and _TRUST_QUESTION in normalized
            and "Press enter to continue" in normalized
        ):
            if not self._is_exact_trust_prompt(normalized):
                self.failure_outcome = "unexpected_trust_path"
                self.events.append("trust_prompt")
                return b""
            self.events.append("trust_prompt")
            if not self.allow_workspace_trust:
                self.failure_outcome = "workspace_trust_required"
                return b""
            self._trust_response_sent = True
            self.request_possibly_sent = True
            self.events.append("trust_confirmed")
            return b"\r"

        if self._trust_response_sent and "Failed to set trust for" in normalized:
            self.failure_outcome = "workspace_trust_failed"
            self.request_possibly_sent = False
            return b""

        if not self._trust_response_sent and _looks_like_unexpected_prompt(normalized):
            self.failure_outcome = "unexpected_tui_prompt"
            return b""

        if "Ask Codex to do anything" in normalized:
            if "main_composer_ready" not in self.events:
                self.events.append("main_composer_ready")

        if re.search(r"Working \(\d+s .*to interrupt\)", normalized):
            if "main_composer_ready" not in self.events:
                self.events.append("main_composer_ready")
            self.events.extend(["positional_prompt_submitted", "turn_activity"])
            self.request_possibly_sent = True
            self.success_outcome = "turn_activity_observed"
        return b""

    def _is_exact_trust_prompt(self, normalized: str) -> bool:
        match = re.search(r"> You are in (.+?) Do you trust the contents", normalized)
        if match is None:
            return False
        displayed_path = match.group(1).strip()
        try:
            displayed = os.path.normcase(os.path.abspath(displayed_path))
            expected = os.path.normcase(os.path.abspath(self.workspace))
        except (OSError, ValueError):
            return False
        return (
            displayed == expected
            and _TRUST_EXPLANATION in normalized
            and "1. Yes, continue" in normalized
            and "2. No, quit" in normalized
            and "Press enter to continue" in normalized
        )


def build_terminal_environment(
    parent: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if parent is None else parent)
    for key in [key for key in environment if key.upper() == "TERM"]:
        del environment[key]
    environment["TERM"] = "xterm-256color"
    return environment


def dedicated_trigger_workspace(history_path: Path) -> Path:
    return history_path.parent / "trigger-workspace"


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
            self._prepare_workspace()
            controller = CodexTuiController(
                self.workspace,
                allow_workspace_trust=self.config.allow_workspace_trust,
            )
            result = run_conpty(
                self.command(),
                cwd=self.workspace,
                min_runtime_seconds=self.config.min_runtime_seconds,
                quiet_seconds=self.config.quiet_seconds,
                max_runtime_seconds=self.config.max_runtime_seconds,
                exit_grace_seconds=self.config.exit_grace_seconds,
                environment=build_terminal_environment(),
                controller=controller,
            )
        except ConPtyError as exc:
            return TriggerRunResult(
                "runtime_error" if exc.process_started else "launch_failed",
                exc.process_started,
            )
        except OSError:
            return TriggerRunResult("workspace_unsafe", False)
        outcome = controller.stop_outcome or result.terminal_outcome
        if outcome in {"process_exited", "process_exited_early"} and result.exit_code != 0:
            outcome += "_nonzero"
        request_possibly_sent = (
            controller.request_possibly_sent or controller.failure_outcome is None
        )
        return TriggerRunResult(outcome, request_possibly_sent)

    def _prepare_workspace(self) -> None:
        self.workspace.parent.mkdir(parents=True, exist_ok=True)
        if not self.workspace.exists():
            self.workspace.mkdir()
        if (
            not self.workspace.is_dir()
            or self.workspace.is_symlink()
            or getattr(self.workspace, "is_junction", lambda: False)()
            or any(self.workspace.iterdir())
        ):
            raise OSError("The dedicated trigger workspace is not an empty local directory.")


def _visible_terminal_text(chunk: bytes) -> str:
    text = chunk.decode("utf-8", errors="replace")
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _looks_like_unexpected_prompt(normalized: str) -> bool:
    return (
        "Continue anyway? [y/N]:" in normalized
        or (
            "Press enter to continue" in normalized
            and _TRUST_QUESTION not in normalized
        )
    )
