import json
from pathlib import Path
import tempfile
import unittest

from sentinel.legacy_cleanup import remove_retired_claude_integration


class RetiredClaudeCleanupTests(unittest.TestCase):
    def test_removes_only_exact_usageloop_statusline_and_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir()
            helper = root / "Programs" / "UsageLoop" / "UsageLoopStatus.exe"
            settings.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": {
                            "type": "command",
                            "command": f'"{helper}"',
                            "refreshInterval": 30,
                        },
                    }
                ),
                encoding="utf-8",
            )
            app_data = root / "UsageLoop"
            app_data.mkdir()
            (app_data / "claude-status.json").write_text("{}", encoding="utf-8")
            (app_data / "claude-attempts.jsonl").write_text("guard", encoding="utf-8")
            codex_history = app_data / "history.jsonl"
            codex_history.write_text("codex", encoding="utf-8")

            result = remove_retired_claude_integration(
                settings_path=settings,
                app_data_dir=app_data,
                owned_helper_paths=(helper,),
            )

            self.assertTrue(result.statusline_removed)
            self.assertEqual(2, result.state_files_removed)
            self.assertEqual({"theme": "dark"}, json.loads(settings.read_text(encoding="utf-8")))
            self.assertEqual("codex", codex_history.read_text(encoding="utf-8"))

    def test_custom_statusline_and_corrupt_settings_are_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            custom = {"statusLine": {"type": "command", "command": "my-status"}}
            settings.write_text(json.dumps(custom), encoding="utf-8")

            result = remove_retired_claude_integration(
                settings_path=settings,
                app_data_dir=root / "app",
                owned_helper_paths=(root / "UsageLoopStatus.exe",),
            )
            self.assertFalse(result.statusline_removed)
            self.assertEqual(custom, json.loads(settings.read_text(encoding="utf-8")))

            settings.write_text("{broken", encoding="utf-8")
            remove_retired_claude_integration(
                settings_path=settings,
                app_data_dir=root / "app",
                owned_helper_paths=(root / "UsageLoopStatus.exe",),
            )
            self.assertEqual("{broken", settings.read_text(encoding="utf-8"))

    def test_similar_helper_path_is_not_treated_as_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            value = {
                "statusLine": {
                    "type": "command",
                    "command": '"C:/Custom/UsageLoopStatus.exe"',
                    "refreshInterval": 30,
                }
            }
            settings.write_text(json.dumps(value), encoding="utf-8")
            remove_retired_claude_integration(
                settings_path=settings,
                app_data_dir=root / "app",
                owned_helper_paths=(root / "Programs" / "UsageLoop" / "UsageLoopStatus.exe",),
            )
            self.assertEqual(value, json.loads(settings.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
