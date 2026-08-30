import unittest
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

from sentinel._conpty import ConPtyError, ConPtyResult, run_conpty
from sentinel.transport import build_codex_command
from sentinel.trigger import (
    CodexTuiController,
    InteractiveCodexTrigger,
    TriggerConfig,
    build_terminal_environment,
    dedicated_trigger_workspace,
)


class InteractiveCodexTriggerTests(unittest.TestCase):
    def test_inherited_dumb_term_becomes_supported_and_other_environment_is_preserved(self):
        environment = build_terminal_environment(
            {"Term": "dumb", "SENTINEL_PRESERVE": "unchanged"}
        )
        self.assertEqual("xterm-256color", environment["TERM"])
        self.assertNotIn("Term", environment)
        self.assertEqual("unchanged", environment["SENTINEL_PRESERVE"])

    def test_dedicated_workspace_is_selected_beside_the_safe_history(self):
        self.assertEqual(
            Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace"),
            dedicated_trigger_workspace(
                Path("C:/LocalApp/CodexWindowSentinel/sentinel.jsonl")
            ),
        )

    def test_exact_trust_prompt_for_dedicated_workspace_is_confirmed(self):
        workspace = Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace")
        controller = CodexTuiController(workspace, allow_workspace_trust=True)
        response = controller.receive(_trust_screen(workspace))
        self.assertEqual(b"\r", response)
        self.assertIn("trust_prompt", controller.events)
        self.assertIn("trust_confirmed", controller.events)
        self.assertEqual(0.5, controller.response_delay_seconds)
        self.assertIsNone(controller.failure_outcome)

    def test_fragmented_exact_trust_prompt_waits_for_complete_screen(self):
        workspace = Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace")
        controller = CodexTuiController(workspace, allow_workspace_trust=True)
        screen = _trust_screen(workspace)
        split_at = screen.index(b"1. Yes")
        self.assertEqual(b"", controller.receive(screen[:split_at]))
        self.assertIsNone(controller.failure_outcome)
        self.assertEqual(b"\r", controller.receive(screen[split_at:]))

    def test_exact_trust_prompt_without_explicit_permission_is_not_confirmed(self):
        workspace = Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace")
        controller = CodexTuiController(workspace, allow_workspace_trust=False)
        response = controller.receive(_trust_screen(workspace))
        self.assertEqual(b"", response)
        self.assertEqual("workspace_trust_required", controller.failure_outcome)

    def test_trust_prompt_for_wrong_path_is_not_confirmed(self):
        workspace = Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace")
        controller = CodexTuiController(workspace, allow_workspace_trust=True)
        response = controller.receive(_trust_screen(Path("C:/Users/Ben/project")))
        self.assertEqual(b"", response)
        self.assertEqual("unexpected_trust_path", controller.failure_outcome)
        self.assertNotIn("trust_confirmed", controller.events)

    def test_unexpected_tui_prompt_fails_closed(self):
        controller = CodexTuiController(
            Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace"),
            allow_workspace_trust=True,
        )
        response = controller.receive(
            b"Choose an unrelated setup option\r\n1. Continue\r\n2. Quit\r\nPress enter to continue"
        )
        self.assertEqual(b"", response)
        self.assertEqual("unexpected_tui_prompt", controller.failure_outcome)

    def test_main_composer_is_detected(self):
        controller = CodexTuiController(
            Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace"),
            allow_workspace_trust=True,
        )
        controller.receive(b"> Ask Codex to do anything\r\n")
        self.assertIn("main_composer_ready", controller.events)

    def test_positional_request_submission_reaches_turn_activity(self):
        controller = CodexTuiController(
            Path("C:/LocalApp/CodexWindowSentinel/trigger-workspace"),
            allow_workspace_trust=True,
        )
        controller.receive(b"> Ask Codex to do anything\r\n")
        controller.receive(b"Working (0s - esc to interrupt)\r\n")
        self.assertIn("positional_prompt_submitted", controller.events)
        self.assertIn("turn_activity", controller.events)
        self.assertTrue(controller.request_possibly_sent)
        self.assertEqual("turn_activity_observed", controller.success_outcome)

    def test_native_executable_uses_interactive_tui_not_exec(self):
        trigger = InteractiveCodexTrigger(
            Path("C:/Codex/codex.exe"),
            Path("C:/Sentinel/trigger-workspace"),
            TriggerConfig(model="gpt-5.4-mini", reasoning_effort="low", prompt="ok"),
        )
        command = trigger.command()
        self.assertEqual(
            [
                "C:\\Codex\\codex.exe",
                "-c",
                "model_reasoning_effort=low",
                "-m",
                "gpt-5.4-mini",
                "--no-alt-screen",
                "-s",
                "read-only",
                "-a",
                "never",
                "-C",
                "C:\\Sentinel\\trigger-workspace",
                "ok",
            ],
            command,
        )
        self.assertNotIn("exec", command)

    @patch.dict("os.environ", {"COMSPEC": "C:\\Windows\\System32\\cmd.exe"})
    def test_cmd_shim_is_wrapped_in_command_interpreter(self):
        trigger = InteractiveCodexTrigger(
            Path("C:/Users/Ben/AppData/Roaming/npm/codex.cmd"),
            Path("C:/Sentinel/trigger-workspace"),
        )
        command = trigger.command()
        self.assertEqual("C:\\Windows\\System32\\cmd.exe", command[0])
        self.assertEqual(
            ["/d", "/c", "C:\\Users\\Ben\\AppData\\Roaming\\npm\\codex.cmd"],
            command[1:4],
        )
        self.assertNotIn("exec", command)

    @unittest.skipUnless(os.name == "nt", "Windows ConPTY fixture")
    def test_cmd_shim_launches_through_conpty_without_bad_exe_format(self):
        with tempfile.TemporaryDirectory(prefix="sentinel shim ") as directory:
            root = Path(directory)
            shim = root / "codex.cmd"
            shim.write_text("@exit /b 0\n", encoding="utf-8")
            result = run_conpty(
                build_codex_command(shim, "--version"),
                cwd=root,
                min_runtime_seconds=0,
                quiet_seconds=0.1,
                max_runtime_seconds=2,
                exit_grace_seconds=0.1,
            )
        self.assertEqual("process_exited", result.terminal_outcome)

    @unittest.skipUnless(os.name == "nt", "Windows ConPTY fixture")
    def test_native_executable_launches_directly_through_conpty(self):
        executable = Path(os.environ.get("COMSPEC", "C:/Windows/System32/cmd.exe"))
        with tempfile.TemporaryDirectory() as directory:
            result = run_conpty(
                [str(executable), "/d", "/c", "exit", "0"],
                cwd=Path(directory),
                min_runtime_seconds=0,
                quiet_seconds=0.1,
                max_runtime_seconds=2,
                exit_grace_seconds=0.1,
            )
        self.assertEqual("process_exited", result.terminal_outcome)

    @unittest.skipUnless(os.name == "nt", "Windows ConPTY fixture")
    def test_conpty_child_receives_terminal_standard_handles(self):
        captured = bytearray()

        def capture_output(output_fd, activity, _input_fd, _controller):
            try:
                while chunk := os.read(output_fd, 4096):
                    captured.extend(chunk)
                    activity[0] = time.monotonic()
            finally:
                os.close(output_fd)

        with tempfile.TemporaryDirectory() as directory:
            with patch("sentinel._conpty._drain_output", side_effect=capture_output):
                result = run_conpty(
                    [
                        sys.executable,
                        "-c",
                        "import sys; print(f'terminal={sys.stdin.isatty() and sys.stdout.isatty()}')",
                    ],
                    cwd=Path(directory),
                    min_runtime_seconds=0,
                    quiet_seconds=0.1,
                    max_runtime_seconds=2,
                    exit_grace_seconds=0.1,
                )
        self.assertEqual(0, result.exit_code)
        self.assertIn(b"terminal=True", captured)

    @unittest.skipUnless(os.name == "nt", "Windows ConPTY fixture")
    def test_controller_waits_for_explicit_state_instead_of_generic_quiet(self):
        class WaitingController:
            stop_outcome = None
            response_delay_seconds = 0.0

            def receive(self, _chunk):
                return b""

        with tempfile.TemporaryDirectory() as directory:
            result = run_conpty(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                cwd=Path(directory),
                min_runtime_seconds=0,
                quiet_seconds=0.05,
                max_runtime_seconds=0.4,
                exit_grace_seconds=0.1,
                controller=WaitingController(),
            )
        self.assertEqual("runtime_cap_reached", result.terminal_outcome)
        self.assertGreaterEqual(result.runtime_seconds, 0.35)

    @patch("sentinel.trigger.conpty_available", return_value=True)
    @patch("sentinel.trigger.run_conpty")
    def test_process_started_outcome_is_only_possibly_sent(self, run_conpty, _available):
        run_conpty.return_value = ConPtyResult(5.0, 0, "process_exited")
        with tempfile.TemporaryDirectory() as directory:
            trigger = InteractiveCodexTrigger(
                Path("C:/Codex/codex.exe"), Path(directory) / "trigger-workspace"
            )
            result = trigger.run()
        self.assertTrue(result.request_possibly_sent)
        self.assertEqual("process_exited", result.terminal_outcome)
        environment = run_conpty.call_args.kwargs["environment"]
        self.assertEqual("xterm-256color", environment["TERM"])

    @patch("sentinel.trigger.conpty_available", return_value=True)
    @patch("sentinel.trigger.run_conpty")
    def test_nonempty_dedicated_workspace_fails_before_launch(self, run_conpty, _available):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "trigger-workspace"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("untrusted instructions", encoding="utf-8")
            trigger = InteractiveCodexTrigger(Path("C:/Codex/codex.exe"), workspace)
            result = trigger.run()
        self.assertEqual("workspace_unsafe", result.terminal_outcome)
        self.assertFalse(result.request_possibly_sent)
        run_conpty.assert_not_called()

    @patch("sentinel.trigger.conpty_available", return_value=True)
    @patch("sentinel.trigger.run_conpty")
    def test_create_process_failure_is_definitely_not_sent(self, run_conpty, _available):
        run_conpty.side_effect = ConPtyError("could not launch", process_started=False)
        with tempfile.TemporaryDirectory() as directory:
            trigger = InteractiveCodexTrigger(
                Path("C:/Codex/codex.exe"), Path(directory) / "trigger-workspace"
            )
            result = trigger.run()
        self.assertFalse(result.request_possibly_sent)
        self.assertEqual("launch_failed", result.terminal_outcome)

    def test_description_reports_prompt_length_not_contents(self):
        trigger = InteractiveCodexTrigger(
            Path("C:/Codex/codex.exe"),
            Path("C:/Sentinel/trigger-workspace"),
            TriggerConfig(prompt="private trigger text"),
        )
        description = trigger.describe()
        self.assertEqual(len("private trigger text"), description.prompt_characters)
        self.assertNotIn("private", repr(description))


def _trust_screen(workspace: Path) -> bytes:
    return (
        f"> You are in {workspace}\r\n\r\n"
        "Do you trust the contents of this directory? Working with untrusted contents comes "
        "with higher risk of prompt injection. Trusting the directory allows project-local "
        "config, hooks, and exec policies to load.\r\n\r\n"
        "1. Yes, continue\r\n"
        "2. No, quit\r\n\r\n"
        "Press enter to continue\r\n"
    ).encode()


if __name__ == "__main__":
    unittest.main()
