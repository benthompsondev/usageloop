import json
import tempfile
import threading
import unittest
from pathlib import Path

from sentinel.classifier import Classification
from sentinel.history import SafeHistory
from sentinel.quota import QuotaSnapshot, QuotaWindow


class SafeHistoryTests(unittest.TestCase):
    def test_trigger_reservation_guard_serializes_independent_history_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.jsonl"
            first = SafeHistory(path)
            second = SafeHistory(path)
            first_acquired = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            second_acquired = threading.Event()

            def hold_first():
                with first.trigger_reservation_guard():
                    first_acquired.set()
                    release_first.wait(2)

            def wait_second():
                second_started.set()
                with second.trigger_reservation_guard():
                    second_acquired.set()

            first_thread = threading.Thread(target=hold_first)
            second_thread = threading.Thread(target=wait_second)
            first_thread.start()
            self.assertTrue(first_acquired.wait(2))
            second_thread.start()
            self.assertTrue(second_started.wait(2))
            self.assertFalse(second_acquired.wait(0.2))
            release_first.set()
            first_thread.join(2)
            second_thread.join(2)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertTrue(second_acquired.is_set())

    def test_observation_log_contains_only_allowlisted_quota_fields(self):
        snapshot = QuotaSnapshot(
            2000000000,
            (QuotaWindow("codex", "primary", 12, 300, 2000010000, None),),
        )
        classification = Classification(
            "UNKNOWN", "low", "insufficient evidence", {"sample_count": 1}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.jsonl"
            history = SafeHistory(path)
            history.record_observation(snapshot, classification, "codex-cli 0.146.0")
            row = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(
            {
                "event",
                "timestamp",
                "observed_at",
                "sentinel_version",
                "codex_version",
                "windows",
                "classification",
                "confidence",
                "evidence",
            },
            set(row),
        )
        serialized = json.dumps(row).lower()
        for forbidden in ("token", "email", "account_id", "prompt", "conversation", "auth.json"):
            self.assertNotIn(forbidden, serialized)

    def test_recent_observations_round_trip_without_raw_protocol_data(self):
        first = QuotaSnapshot(
            2000000000,
            (QuotaWindow("codex", "primary", 12, 300, 2000010000, None),),
        )
        second = QuotaSnapshot(
            2000000010,
            (QuotaWindow("codex", "primary", 13, 300, 2000010000, None),),
        )
        unknown = Classification("UNKNOWN", "low", "waiting", {"sample_count": 1})
        with tempfile.TemporaryDirectory() as directory:
            history = SafeHistory(Path(directory) / "sentinel.jsonl")
            history.record_observation(first, unknown, "codex-cli 0.146.0")
            history.record_observation(second, unknown, "codex-cli 0.146.0")
            loaded = history.load_recent(now=2000000020, max_age_seconds=60, limit=4)
        self.assertEqual([first, second], loaded)

    def test_errors_record_category_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.jsonl"
            history = SafeHistory(path)
            history.record_error("authentication_unavailable")
            row = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("authentication_unavailable", row["category"])
        self.assertEqual({"event", "timestamp", "sentinel_version", "category"}, set(row))

    def test_trigger_state_log_excludes_prompt_credentials_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.jsonl"
            history = SafeHistory(path)
            attempt = history.reserve_trigger(
                mode="bootstrap",
                idempotency_key="bootstrap:test",
                boundary_reset_at=None,
                model="gpt-5.4-mini",
                reasoning_effort="low",
                now=2000010000,
            )
            history.transition_trigger(attempt.attempt_id, "launch_attempted", now=2000010001)
            history.transition_trigger(
                attempt.attempt_id,
                "failed_guarded",
                outcome="anchor_not_verified",
                observed_state="UNANCHORED",
                now=2000010032,
            )
            serialized = path.read_text(encoding="utf-8").lower()

        for forbidden in (
            "prompt",
            "token",
            "email",
            "account_id",
            "auth.json",
            "conversation",
            "process_output",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn('"model":"gpt-5.4-mini"', serialized)
        self.assertIn('"reasoning_effort":"low"', serialized)

    def test_trigger_attempt_lifecycle_round_trips_latest_state(self):
        with tempfile.TemporaryDirectory() as directory:
            history = SafeHistory(Path(directory) / "sentinel.jsonl")
            attempt = history.reserve_trigger(
                mode="rollover",
                idempotency_key="rollover:2000010000",
                boundary_reset_at=2000010000,
                model="gpt-5.4-mini",
                reasoning_effort="low",
                now=2000010015,
            )
            history.transition_trigger(attempt.attempt_id, "launch_attempted", now=2000010016)
            history.transition_trigger(attempt.attempt_id, "request_possibly_sent", now=2000010017)
            loaded = SafeHistory(history.path).trigger_attempts()

        self.assertEqual(1, len(loaded))
        self.assertEqual("request_possibly_sent", loaded[0].state)
        self.assertEqual("rollover:2000010000", loaded[0].idempotency_key)


if __name__ == "__main__":
    unittest.main()
