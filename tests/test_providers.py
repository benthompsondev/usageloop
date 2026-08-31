from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sentinel.history import SafeHistory
from sentinel.classifier import Classification
from sentinel.quota import QuotaSnapshot, QuotaWindow
from sentinel.providers import CodexProvider, CompatibilityResult


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
                history=SafeHistory(history_path), executable_finder=lambda: None
            )
            state = provider.detect()
            self.assertFalse(state.installed)
            self.assertEqual("Needs attention", state.status)

    def test_codex_card_uses_last_verified_history_without_provider_traffic(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex.exe"
            executable.write_bytes(b"native")
            history = SafeHistory(Path(directory) / "history.jsonl")
            classification = Classification(
                "ANCHORED", "high", "The reset timestamp is fixed.",
                {"sample_count": 4, "elapsed_seconds": 30},
            )
            for observed_at in (100.0, 110.0, 120.0, 130.0):
                snapshot = QuotaSnapshot(
                    observed_at,
                    (
                        QuotaWindow("codex", "primary", 7, 300, 18_000, None),
                        QuotaWindow("codex", "secondary", 20, 10_080, 600_000, None),
                    ),
                )
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
            self.assertEqual(20, state.weekly_used_percent)
            self.assertEqual(600_000, state.weekly_reset_at)

    def test_capability_probe_accepts_version_change_when_contract_is_intact(self):
        result = CompatibilityResult.from_capabilities(
            runtime_identity="codex-file:new", initialized=True,
            rate_limits_available=True, model_catalog_available=True,
            suitable_model_available=True,
        )
        self.assertTrue(result.compatible)
        self.assertEqual("codex-file:new", result.runtime_identity)

    def test_ambiguous_capability_probe_fails_closed(self):
        result = CompatibilityResult.from_capabilities(
            runtime_identity="codex-file:new", initialized=True,
            rate_limits_available=None, model_catalog_available=True,
            suitable_model_available=True,
        )
        self.assertFalse(result.compatible)
        self.assertEqual("Needs attention", result.status)


if __name__ == "__main__":
    unittest.main()
