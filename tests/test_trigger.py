import unittest
from pathlib import Path

from sentinel.trigger import InteractiveCodexTrigger, TriggerConfig


class InteractiveCodexTriggerTests(unittest.TestCase):
    def test_trigger_uses_interactive_tui_not_exec(self):
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
