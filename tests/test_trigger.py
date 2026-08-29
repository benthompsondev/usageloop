import unittest
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from sentinel._conpty import ConPtyError, ConPtyResult, run_conpty
from sentinel.transport import build_codex_command
from sentinel.trigger import InteractiveCodexTrigger, TriggerConfig


class InteractiveCodexTriggerTests(unittest.TestCase):
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

    @patch("sentinel.trigger.conpty_available", return_value=True)
    @patch("sentinel.trigger.run_conpty")
    def test_process_started_outcome_is_only_possibly_sent(self, run_conpty, _available):
        run_conpty.return_value = ConPtyResult(5.0, 0, "process_exited")
        trigger = InteractiveCodexTrigger(Path("C:/Codex/codex.exe"), Path("C:/Temp"))
        result = trigger.run()
        self.assertTrue(result.request_possibly_sent)
        self.assertEqual("process_exited", result.terminal_outcome)

    @patch("sentinel.trigger.conpty_available", return_value=True)
    @patch("sentinel.trigger.run_conpty")
    def test_create_process_failure_is_definitely_not_sent(self, run_conpty, _available):
        run_conpty.side_effect = ConPtyError("could not launch", process_started=False)
        trigger = InteractiveCodexTrigger(Path("C:/Codex/codex.exe"), Path("C:/Temp"))
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


if __name__ == "__main__":
    unittest.main()
