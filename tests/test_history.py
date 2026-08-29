import json
import tempfile
import unittest
from pathlib import Path

from sentinel.classifier import Classification
from sentinel.history import SafeHistory
from sentinel.quota import QuotaSnapshot, QuotaWindow


class SafeHistoryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
