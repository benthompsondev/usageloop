import json
import unittest
from pathlib import Path

from sentinel.quota import normalize_rate_limits, select_five_hour


FIXTURES = Path(__file__).parent / "fixtures"


class QuotaNormalizationTests(unittest.TestCase):
    def test_multi_bucket_response_preserves_windows_without_legacy_duplicates(self):
        payload = json.loads((FIXTURES / "rate_limits_read.json").read_text(encoding="utf-8"))
        snapshot = normalize_rate_limits(payload, observed_at=2000000000)

        self.assertEqual(3, len(snapshot.windows))
        self.assertEqual({30, 300, 10080}, {window.duration_minutes for window in snapshot.windows})
        self.assertNotIn("must-not-be-normalized-or-logged", repr(snapshot))
        self.assertNotIn("not persisted", repr(snapshot))

    def test_five_hour_selection_uses_duration_not_primary_position(self):
        payload = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 9, "windowDurationMins": 10080, "resetsAt": 2000604800},
                "secondary": {"usedPercent": 3, "windowDurationMins": 300, "resetsAt": 2000010000}
            }
        }
        selected = select_five_hour(normalize_rate_limits(payload, 2000000000))
        self.assertEqual("selected", selected.status)
        self.assertEqual("secondary", selected.window.slot)

    def test_multiple_five_hour_buckets_prefer_the_codex_bucket(self):
        payload = {
            "rateLimitsByLimitId": {
                "other": {"primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": 2000018000}},
                "codex": {"primary": {"usedPercent": 2, "windowDurationMins": 300, "resetsAt": 2000010000}}
            },
            "rateLimits": {}
        }
        selected = select_five_hour(normalize_rate_limits(payload, 2000000000))
        self.assertEqual("codex", selected.window.limit_id)

    def test_ambiguous_same_duration_windows_are_not_guessed(self):
        payload = {
            "rateLimitsByLimitId": {
                "first": {"primary": {"usedPercent": 5, "windowDurationMins": 300, "resetsAt": 2000018000}},
                "second": {"primary": {"usedPercent": 2, "windowDurationMins": 300, "resetsAt": 2000010000}}
            },
            "rateLimits": {}
        }
        self.assertEqual("ambiguous", select_five_hour(normalize_rate_limits(payload, 2000000000)).status)

    def test_malformed_window_is_ignored_without_raising(self):
        payload = {"rateLimits": {"primary": {"usedPercent": "many", "windowDurationMins": 300}}}
        snapshot = normalize_rate_limits(payload, 2000000000)
        self.assertEqual((), snapshot.windows)


if __name__ == "__main__":
    unittest.main()
