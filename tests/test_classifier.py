import json
import unittest
from pathlib import Path

from sentinel.classifier import classify
from sentinel.quota import QuotaSnapshot, QuotaWindow


FIXTURES = Path(__file__).parent / "fixtures"


def load_cases():
    return json.loads((FIXTURES / "classifier_cases.json").read_text(encoding="utf-8"))


def snapshots(case_name):
    items = []
    for row in load_cases()[case_name]:
        window = QuotaWindow(
            limit_id="codex",
            slot="primary",
            used_percent=row["usedPercent"],
            duration_minutes=row["windowDurationMins"],
            resets_at=row.get("resetsAt"),
            blocked_reason=None,
        )
        items.append(QuotaSnapshot(observed_at=row["observed_at"], windows=(window,)))
    return items


class ClassifierTests(unittest.TestCase):
    def test_fixed_reset_timestamp_is_anchored(self):
        result = classify(snapshots("anchored"))
        self.assertEqual("ANCHORED", result.state)
        self.assertEqual(0, result.evidence["reset_span_seconds"])

    def test_reset_timestamp_advancing_with_wall_time_is_unanchored(self):
        result = classify(snapshots("sliding"))
        self.assertEqual("UNANCHORED", result.state)
        self.assertAlmostEqual(1.0, result.evidence["reset_slope"], places=2)

    def test_small_reset_timestamp_jitter_stays_anchored(self):
        self.assertEqual("ANCHORED", classify(snapshots("jitter")).state)

    def test_single_sample_is_unknown(self):
        self.assertEqual("UNKNOWN", classify(snapshots("anchored")[:1]).state)

    def test_missing_five_hour_window_is_absent(self):
        weekly = QuotaWindow("codex", "secondary", 10, 10080, 2000604800, None)
        result = classify([QuotaSnapshot(2000000000, (weekly,))])
        self.assertEqual("ABSENT", result.state)

    def test_empty_snapshot_is_absent(self):
        self.assertEqual("ABSENT", classify([QuotaSnapshot(2000000000, ())]).state)

    def test_missing_reset_timestamp_is_unknown(self):
        values = snapshots("anchored")
        broken = QuotaWindow("codex", "primary", 1, 300, None, None)
        values[1] = QuotaSnapshot(values[1].observed_at, (broken,))
        self.assertEqual("UNKNOWN", classify(values).state)

    def test_window_duration_change_is_unknown(self):
        self.assertEqual("UNKNOWN", classify(snapshots("duration_change")).state)

    def test_reset_between_samples_is_unknown(self):
        self.assertEqual("UNKNOWN", classify(snapshots("reset_between")).state)

    def test_percentage_change_does_not_hide_fixed_reset(self):
        self.assertEqual("ANCHORED", classify(snapshots("anchored")).state)

    def test_explicit_block_is_exhausted(self):
        blocked = QuotaWindow("codex", "primary", 99, 300, 2000010000, "rate_limit_reached")
        result = classify([QuotaSnapshot(2000000000, (blocked,))])
        self.assertEqual("EXHAUSTED", result.state)

    def test_hundred_percent_usage_is_exhausted(self):
        blocked = QuotaWindow("codex", "primary", 100, 300, 2000010000, None)
        self.assertEqual("EXHAUSTED", classify([QuotaSnapshot(2000000000, (blocked,))]).state)


if __name__ == "__main__":
    unittest.main()
