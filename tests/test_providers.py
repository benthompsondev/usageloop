from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sentinel.history import SafeHistory
from sentinel.app_state import ProviderViewState
from sentinel.classifier import Classification, classify
from sentinel.quota import QuotaSnapshot, QuotaWindow
from sentinel.providers import CodexProvider, CompatibilityResult
from sentinel.provider_runtime import ProviderOperationResult


class ProviderAdapterTests(unittest.TestCase):
    def test_manual_sync_can_recover_even_when_saved_history_is_corrupt(self) -> None:
        class SyncRunner:
            def sync(self, runtime_identity):
                state = ProviderViewState(
                    "codex", "Codex", True, True, "Ready", "Synced.",
                    runtime_identity=runtime_identity, reset_at=20_000,
                    last_verified_at=140, used_percent=7, usage_checked_at=140,
                    weekly_used_percent=2, weekly_reset_at=900_000,
                )
                return ProviderOperationResult("SYNC_UPDATED", state, False)

        with tempfile.TemporaryDirectory() as directory:
            history = SafeHistory(Path(directory) / "history.jsonl")
            history.path.write_text('{"event":"trigger_state"', encoding="utf-8")
            current = ProviderViewState(
                "codex", "Codex", True, True, "Needs attention", "Stale.",
                runtime_identity="runtime:1", reset_at=10_000,
                last_verified_at=40, used_percent=80, usage_checked_at=40,
            )
            provider = CodexProvider(
                history=history,
                operation_runner=SyncRunner(),
                executable_finder=lambda: (_ for _ in ()).throw(
                    AssertionError("manual sync should use the current detected state")
                ),
            )

            result = provider.sync_usage(current_state=current)

            self.assertEqual("SYNC_UPDATED", result.outcome)
            self.assertEqual(20_000, result.state.reset_at)
            self.assertEqual(7, result.state.used_percent)
            self.assertEqual(2, result.state.weekly_used_percent)

    def test_manual_sync_never_overrides_a_failed_compatibility_gate(self) -> None:
        class SyncRunner:
            def sync(self, runtime_identity):
                return ProviderOperationResult(
                    "SYNC_UPDATED",
                    ProviderViewState(
                        "codex", "Codex", True, True, "Ready", "Synced.",
                        runtime_identity=runtime_identity, reset_at=20_000,
                        last_verified_at=140, used_percent=7, usage_checked_at=140,
                    ),
                    False,
                )

        with tempfile.TemporaryDirectory() as directory:
            current = ProviderViewState(
                "codex", "Codex", True, False, "Needs attention",
                "Compatibility check failed.", runtime_identity="runtime:1",
            )
            provider = CodexProvider(
                history=SafeHistory(Path(directory) / "history.jsonl"),
                operation_runner=SyncRunner(),
            )

            result = provider.sync_usage(current_state=current)

            self.assertFalse(result.state.automation_supported)

    def test_verified_trigger_result_survives_restart_as_last_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = SafeHistory(Path(directory) / "history.jsonl")
            attempt = history.reserve_trigger(
                mode="rollover",
                idempotency_key="rollover:18000",
                boundary_reset_at=18000,
                model="current-model",
                reasoning_effort="low",
                now=100.0,
            )
            history.transition_trigger(
                attempt.attempt_id,
                "launch_attempted",
                now=101.0,
            )
            history.transition_trigger(
                attempt.attempt_id,
                "request_possibly_sent",
                outcome="turn_completed",
                observed_state="UNANCHORED",
                now=102.0,
            )
            history.transition_trigger(
                attempt.attempt_id,
                "verified",
                outcome="anchor_verified",
                observed_state="ANCHORED",
                now=140.0,
            )
            snapshots = [
                QuotaSnapshot(
                    observed_at,
                    (QuotaWindow("codex", "primary", 0, 300, 18_000, None),),
                )
                for observed_at in (110.0, 120.0, 130.0, 140.0)
            ]
            classification = classify(snapshots)
            for snapshot in snapshots:
                history.record_observation(snapshot, classification, "codex-cli test")

            provider = CodexProvider(
                history=history,
                executable_finder=lambda: Path(__file__),
                identity_reader=lambda _path: "runtime:1",
                version_reader=lambda _path: "test",
                now=lambda: 150.0,
            )

            self.assertEqual(
                "Started and verified the next window",
                provider.detect().last_action,
            )

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
