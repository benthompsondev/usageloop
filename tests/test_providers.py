from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sentinel.history import SafeHistory
from sentinel.classifier import Classification
from sentinel.quota import QuotaSnapshot, QuotaWindow
from sentinel.app_state import ProviderViewState
from sentinel.providers import ClaudeProvider, CodexProvider, CompatibilityResult


class ProviderAdapterTests(unittest.TestCase):
    def test_codex_detection_reads_file_identity_without_starting_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex.exe"
            executable.write_bytes(b"native")
            probe = mock.Mock()
            provider = CodexProvider(
                history=SafeHistory(Path(directory) / "history.jsonl"),
                executable_finder=lambda: executable,
                identity_reader=lambda path: "codex-file:1",
                version_reader=lambda path: "1.2.3",
                capability_probe=probe,
            )
            state = provider.detect()
            self.assertTrue(state.installed)
            self.assertEqual("codex-file:1", state.runtime_identity)
            self.assertEqual("1.2.3", state.runtime_version)
            probe.assert_not_called()

    def test_codex_unavailable_and_corrupt_history_degrade_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "history.jsonl"
            history_path.write_text("{broken", encoding="utf-8")
            provider = CodexProvider(
                history=SafeHistory(history_path),
                executable_finder=lambda: None,
            )
            state = provider.detect()
            self.assertFalse(state.installed)
            self.assertEqual("Needs attention", state.status)

    def test_claude_missing_degrades_without_running_any_capability_check(self):
        runner = mock.Mock()
        provider = ClaudeProvider(executable_finder=lambda: None, operation_runner=runner)
        state = provider.detect()
        self.assertFalse(state.installed)
        self.assertFalse(state.automation_supported)
        runner.probe.assert_not_called()

    def test_codex_card_uses_last_verified_history_without_provider_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex.exe"
            executable.write_bytes(b"native")
            history = SafeHistory(Path(directory) / "history.jsonl")
            snapshots = []
            for observed_at in (100.0, 110.0, 120.0, 130.0):
                snapshot = QuotaSnapshot(
                    observed_at,
                    (
                        QuotaWindow("codex", "primary", 7, 300, 18_000, None),
                        QuotaWindow("codex", "secondary", 20, 10_080, 600_000, None),
                    ),
                )
                snapshots.append(snapshot)
            classification = Classification(
                "ANCHORED",
                "high",
                "The reset timestamp is fixed.",
                {"sample_count": 4, "elapsed_seconds": 30},
            )
            for snapshot in snapshots:
                history.record_observation(snapshot, classification, "codex-cli 1")
            provider = CodexProvider(
                history=history,
                executable_finder=lambda: executable,
                identity_reader=lambda path: "codex-file:1",
                now=lambda: 140.0,
            )
            state = provider.detect()
            self.assertEqual("Ready", state.status)
            self.assertEqual(18_000, state.reset_at)
            self.assertEqual(7, state.used_percent)
            self.assertEqual(130.0, state.last_verified_at)

    def test_capability_probe_accepts_version_change_when_contract_is_intact(self):
        result = CompatibilityResult.from_capabilities(
            runtime_identity="codex-file:new",
            initialized=True,
            rate_limits_available=True,
            model_catalog_available=True,
            suitable_model_available=True,
        )
        self.assertTrue(result.compatible)
        self.assertEqual("codex-file:new", result.runtime_identity)

    def test_ambiguous_capability_probe_fails_closed(self):
        result = CompatibilityResult.from_capabilities(
            runtime_identity="codex-file:new",
            initialized=True,
            rate_limits_available=None,
            model_catalog_available=True,
            suitable_model_available=True,
        )
        self.assertFalse(result.compatible)
        self.assertEqual("Needs attention", result.status)

    def test_claude_is_detected_and_waits_for_capability_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"native")
            integration = mock.Mock()
            provider = ClaudeProvider(
                executable_finder=lambda: executable,
                identity_reader=lambda path: "claude-file:1",
                status_integration=integration,
            )
            state = provider.detect()
            self.assertTrue(state.installed)
            self.assertTrue(state.automation_supported)
            self.assertEqual("Waiting", state.status)
            self.assertIn("Compatibility", state.detail)
            integration.upgrade_owned_registration.assert_called_once_with()

    def test_claude_version_change_reprobes_capability_not_version_string(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"runtime with --init-only")

            class Runner:
                def probe(self, identity, path):
                    return CompatibilityResult(True, "Waiting", "Compatible.", identity)

            provider = ClaudeProvider(
                executable_finder=lambda: executable,
                identity_reader=lambda path: "claude-file:new",
                version_reader=lambda path: "99.0.0",
                operation_runner=Runner(),
                status_integration=mock.Mock(
                    ensure_registered=lambda: mock.Mock(compatible=True)
                ),
            )
            result = provider.probe()
            self.assertTrue(result.compatible)
            self.assertEqual("claude-file:new", result.runtime_identity)

    def test_claude_compatible_runtime_pauses_if_statusline_cannot_be_registered(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"runtime with --init-only")
            runner = mock.Mock()
            runner.probe.return_value = CompatibilityResult(
                True, "Waiting", "Compatible.", "runtime:1"
            )
            registration = mock.Mock(compatible=False, detail="Existing status line.")
            integration = mock.Mock()
            integration.ensure_registered.return_value = registration
            provider = ClaudeProvider(
                executable_finder=lambda: executable,
                identity_reader=lambda path: "runtime:1",
                operation_runner=runner,
                status_integration=integration,
            )
            result = provider.probe()
            self.assertFalse(result.compatible)
            self.assertIn("Existing status", result.detail)

    def test_claude_runtime_replacement_aborts_before_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"runtime with --init-only")
            runner = mock.Mock()
            provider = ClaudeProvider(
                executable_finder=lambda: executable,
                identity_reader=lambda path: "runtime:new",
                operation_runner=runner,
            )
            approved = ProviderViewState(
                "claude",
                "Claude Code",
                True,
                True,
                "Waiting",
                "Approved runtime.",
                runtime_identity="runtime:old",
            )
            result = provider.run_action("bootstrap", current_state=approved)
            self.assertEqual("CLAUDE_RUNTIME_CHANGED", result.outcome)
            self.assertFalse(result.request_possibly_sent)
            self.assertEqual("Needs attention", result.state.status)
            runner.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
