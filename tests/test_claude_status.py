import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sentinel.product import PRODUCT
from sentinel.claude_status import (
    ClaudeStatusLineIntegration,
    ClaudeStatusStore,
    render_statusline,
)
from sentinel.providers import ClaudeProvider


class ClaudeStatusStoreTests(unittest.TestCase):
    def test_records_only_allowlisted_statusline_quota_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            store = ClaudeStatusStore(path)
            payload = {
                "session_id": "private-session",
                "transcript_path": "C:/private/transcript.jsonl",
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 0,
                        "resets_at": 1000,
                        "account": "private-account",
                    },
                    "seven_day": {"used_percentage": 12.5, "resets_at": 9000},
                },
            }
            self.assertTrue(store.record_statusline(payload, observed_at=100))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("session", raw)
            self.assertNotIn("transcript", raw)
            self.assertNotIn("account", raw)
            status = store.load()
            self.assertEqual(0, status.five_hour_used_percent)
            self.assertEqual(12.5, status.weekly_used_percent)

    def test_missing_five_hour_preserves_last_boundary_but_not_weekly_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ClaudeStatusStore(Path(directory) / "status.json")
            store.record_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 3, "resets_at": 1000},
                        "seven_day": {"used_percentage": 10, "resets_at": 9000},
                    }
                },
                observed_at=100,
            )
            store.record_statusline({"rate_limits": {}}, observed_at=1100)
            status = store.load()
            self.assertEqual(1000, status.last_five_hour_reset_at)
            self.assertIsNone(status.weekly_used_percent)

    def test_corrupt_cache_fails_closed_as_no_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(ClaudeStatusStore(path).load())

    def test_provider_maps_future_status_to_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"--init-only")
            store = ClaudeStatusStore(Path(directory) / "status.json")
            store.record_statusline(
                {
                    "rate_limits": {
                        "five_hour": {"used_percentage": 0, "resets_at": 1000},
                        "seven_day": {"used_percentage": 10, "resets_at": 9000},
                    }
                },
                observed_at=100,
            )
            runner = mock.Mock()
            runner.verify_observed_reset.return_value = True
            provider = ClaudeProvider(
                executable_finder=lambda: executable,
                identity_reader=lambda path: "runtime:1",
                status_store=store,
                status_integration=mock.Mock(),
                operation_runner=runner,
                now=lambda: 200,
                desktop_observer=lambda now: None,
            )
            state = provider.detect()
            self.assertEqual("Ready", state.status)
            self.assertEqual(1000, state.reset_at)
            self.assertEqual(10, state.weekly_used_percent)
            self.assertEqual("Initialization verified", state.last_action)
            runner.verify_observed_reset.assert_called_once_with(1000, observed_at=100.0)

    def test_hidden_recorder_command_does_not_contact_claude(self):
        from sentinel.cli import main

        payload = json.dumps(
            {"rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 1000}}}
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "sentinel.claude_status.default_claude_status_path",
            return_value=Path(directory) / "status.json",
        ), mock.patch("sys.stdin", io.StringIO(payload)):
            self.assertEqual(0, main(["claude-statusline-record"]))

    def test_registration_adds_only_missing_statusline_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
            integration = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"')
            result = integration.ensure_registered()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(result.compatible)
            self.assertEqual("dark", saved["theme"])
            self.assertEqual(
                {
                    "type": "command",
                    "command": '"C:/safe/helper.exe"',
                    "refreshInterval": 30,
                },
                saved["statusLine"],
            )
            self.assertTrue(integration.ensure_registered().compatible)

    def test_registration_upgrades_its_own_pre_refresh_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "statusLine": {
                            "type": "command",
                            "command": '"C:/safe/helper.exe"',
                        },
                    }
                ),
                encoding="utf-8",
            )

            integration = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"')
            changed = integration.upgrade_owned_registration()

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(changed)
            self.assertEqual("dark", saved["theme"])
            self.assertEqual(30, saved["statusLine"]["refreshInterval"])

    def test_local_upgrade_replaces_only_the_exact_pre_rebrand_helper_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            legacy = '"C:/Programs/Window Sentinel/UsageLoopStatus.exe"'
            current = '"C:/Programs/UsageLoop/UsageLoopStatus.exe"'
            path.write_text(
                json.dumps(
                    {"statusLine": {"type": "command", "command": legacy}}
                ),
                encoding="utf-8",
            )
            integration = ClaudeStatusLineIntegration(
                path,
                current,
                legacy_commands=(legacy,),
            )

            self.assertTrue(integration.upgrade_owned_registration())

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(current, saved["statusLine"]["command"])
            self.assertEqual(30, saved["statusLine"]["refreshInterval"])

    def test_local_upgrade_never_creates_or_replaces_a_statusline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
            integration = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"')
            self.assertFalse(integration.upgrade_owned_registration())
            self.assertEqual({"theme": "dark"}, json.loads(path.read_text(encoding="utf-8")))

            custom = {"statusLine": {"type": "command", "command": "custom"}}
            path.write_text(json.dumps(custom), encoding="utf-8")
            self.assertFalse(integration.upgrade_owned_registration())
            self.assertEqual(custom, json.loads(path.read_text(encoding="utf-8")))

    def test_registration_never_replaces_existing_custom_statusline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"statusLine": {"type": "command", "command": "my-status"}}
            path.write_text(json.dumps(original), encoding="utf-8")
            result = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"').ensure_registered()
            self.assertFalse(result.compatible)
            self.assertEqual(original, json.loads(path.read_text(encoding="utf-8")))

    def test_unregistration_removes_only_exact_sentinel_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            integration = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"')
            self.assertTrue(integration.ensure_registered().compatible)
            self.assertTrue(integration.remove_if_owned())
            self.assertNotIn("statusLine", json.loads(path.read_text(encoding="utf-8")))

            custom = {"statusLine": {"type": "command", "command": "replacement"}}
            path.write_text(json.dumps(custom), encoding="utf-8")
            self.assertFalse(integration.remove_if_owned())
            self.assertEqual(custom, json.loads(path.read_text(encoding="utf-8")))

    def test_corrupt_claude_settings_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            result = ClaudeStatusLineIntegration(path, '"C:/safe/helper.exe"').ensure_registered()
            self.assertFalse(result.compatible)
            self.assertEqual("{broken", path.read_text(encoding="utf-8"))

    def test_statusline_output_uses_only_safe_countdown(self):
        payload = {
            "session_id": "private",
            "rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 1900}},
        }
        self.assertEqual(f"{PRODUCT.display_name} | Claude 0h 15m", render_statusline(payload, now=1000))


if __name__ == "__main__":
    unittest.main()
