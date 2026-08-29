import unittest

from sentinel.classifier import Classification
from sentinel.cli import build_status_payload, create_parser
from sentinel.quota import QuotaSnapshot, QuotaWindow


class CliShapeTests(unittest.TestCase):
    def test_status_json_has_machine_readable_five_hour_and_other_windows(self):
        five_hour = QuotaWindow("codex", "primary", 12, 300, 2000010000, None)
        weekly = QuotaWindow("codex", "secondary", 34, 10080, 2000604800, None)
        snapshot = QuotaSnapshot(2000000000, (five_hour, weekly))
        classification = Classification(
            "ANCHORED",
            "high",
            "fixed reset",
            {"sample_count": 4, "reset_span_seconds": 0},
        )

        payload = build_status_payload(snapshot, classification, "codex-cli 0.146.0")

        self.assertEqual("ANCHORED", payload["five_hour_window"]["state"])
        self.assertEqual(10000, payload["five_hour_window"]["remaining_seconds"])
        self.assertEqual(300, payload["five_hour_window"]["duration_minutes"])
        self.assertEqual(1, len(payload["other_windows"]))
        self.assertEqual(10080, payload["other_windows"][0]["duration_minutes"])
        self.assertNotIn("primary", payload["five_hour_window"])

    def test_unknown_is_rendered_when_one_valid_sample_exists(self):
        five_hour = QuotaWindow("codex", "primary", 12, 300, 2000010000, None)
        snapshot = QuotaSnapshot(2000000000, (five_hour,))
        classification = Classification("UNKNOWN", "low", "need more samples", {"sample_count": 1})
        payload = build_status_payload(snapshot, classification, "codex-cli 0.146.0")
        self.assertEqual("UNKNOWN", payload["five_hour_window"]["state"])
        self.assertEqual("need more samples", payload["five_hour_window"]["reason"])

    def test_parser_supports_required_commands_and_status_json(self):
        parser = create_parser()
        for command in ("doctor", "sample", "watch"):
            self.assertEqual(command, parser.parse_args([command]).command)
        status = parser.parse_args(["status", "--json"])
        self.assertEqual("status", status.command)
        self.assertTrue(status.json)


if __name__ == "__main__":
    unittest.main()
