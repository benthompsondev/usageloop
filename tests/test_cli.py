from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from sentinel.classifier import Classification
from sentinel.cli import build_status_payload, create_parser, run_doctor
from sentinel.quota import QuotaSnapshot, QuotaWindow


class CliShapeTests(unittest.TestCase):
    def test_doctor_shows_selected_model_explicit_fallback_and_policy(self):
        def model(identifier, **overrides):
            return {
                "id": identifier,
                "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
                **overrides,
            }

        class Session:
            executable = "fixture-codex"
            codex_version = "fixture"
            platform_os = "windows"

            def __init__(self, catalog):
                self.client = self
                self.catalog = catalog
                self.closed = False

            def read_rate_limits(self):
                return {}

            def list_models(self):
                return self.catalog

            def close(self):
                self.closed = True

        cases = (
            ([model("gpt-5.6-luna"), model("gpt-6-luna")], "gpt-6-luna / low / standard service"),
            ([model("gpt-6-luna", hidden=True), model("gpt-5.6-luna")], "gpt-5.6-luna / low / standard service"),
            ([model("gpt-6-luna", hidden=True), model("gpt-5.6-luna", upgrade="gpt-7-luna")], "none usable"),
        )
        for catalog, selected in cases:
            with self.subTest(selected=selected):
                session = Session(catalog)
                output = StringIO()
                with (patch("sentinel.cli.connect", return_value=session),
                      patch("sentinel.cli.normalize_rate_limits", return_value=QuotaSnapshot(0, ())),
                      redirect_stdout(output)):
                    self.assertEqual(0, run_doctor())
                rendered = output.getvalue()
                self.assertIn(f"Trigger model: {selected}", rendered)
                self.assertIn("Model preference: gpt-6-luna, then gpt-5.6-luna", rendered)
                self.assertIn("Model policy: listed lightweight models only; no unknown or default-model fallback", rendered)
                self.assertTrue(session.closed)

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
        for command in ("doctor", "sample", "watch", "chain", "bootstrap"):
            self.assertEqual(command, parser.parse_args([command]).command)
        status = parser.parse_args(["status", "--json"])
        self.assertEqual("status", status.command)
        self.assertTrue(status.json)

        chain = parser.parse_args(["chain", "--dry-run", "--json"])
        self.assertTrue(chain.dry_run)
        self.assertTrue(chain.json)

        bootstrap = parser.parse_args(["bootstrap", "--confirm", "--json"])
        self.assertTrue(bootstrap.confirm)
        self.assertTrue(bootstrap.json)


if __name__ == "__main__":
    unittest.main()
