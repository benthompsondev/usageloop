"""Regressions for the Claude Desktop observation path.

The defect these cover: UsageLoop assumed Claude Code inside the Claude Desktop
app used the same statusLine channel as the terminal CLI. It does not, so the
helper was never invoked, no quota cache was ever written, and the dashboard
sat on "not checked yet" while Desktop displayed a live window. Executable
discovery also preferred the standalone terminal CLI, which on the affected
machine was a different install with different sign-in state.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sentinel import claude_desktop
from sentinel.claude_desktop import (
    FIVE_HOURS_SECONDS,
    STALE_AFTER_SECONDS,
    UsageSample,
    derive_window,
    read_usage_samples,
)
from sentinel.providers import ClaudeProvider, desktop_claude_executables, find_claude_executable


NOW = 1_788_130_000.0
# A fabricated identifier. The real file carries a genuine account uuid,
# which is exactly why the parser must never return or store it.
ORG = "00000000-0000-4000-8000-000000000000"


def history(samples, *, version=2):
    return {
        "version": version,
        "samples": [
            {"org": ORG, "t": int(at * 1000), "u": usage} for at, usage in samples
        ],
    }


def write_history(directory, payload):
    path = Path(directory) / "plan-usage-history.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class UsageHistoryParsingTests(unittest.TestCase):
    def test_samples_are_read_as_seconds_and_percentages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_history(directory, history([(NOW, {"fh": 3, "sd": 64})]))
            samples = read_usage_samples(path)
        self.assertEqual(1, len(samples))
        self.assertAlmostEqual(NOW, samples[0].observed_at, places=3)
        self.assertEqual(3.0, samples[0].five_hour_percent)
        self.assertEqual(64.0, samples[0].seven_day_percent)

    def test_account_identifier_is_never_returned(self):
        """The file carries an org uuid. It must not leave this parser."""
        with tempfile.TemporaryDirectory() as directory:
            path = write_history(directory, history([(NOW, {"fh": 3, "sd": 64})]))
            samples = read_usage_samples(path)
        self.assertNotIn(ORG, repr(samples))
        for sample in samples:
            self.assertNotIn("org", vars(sample))

    def test_unknown_schema_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_history(directory, history([(NOW, {"fh": 3})], version=99))
            self.assertEqual((), read_usage_samples(path))

    def test_missing_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual((), read_usage_samples(Path(directory) / "absent.json"))

    def test_corrupt_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan-usage-history.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual((), read_usage_samples(path))

    def test_malformed_samples_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = history([(NOW, {"fh": 5})])
            payload["samples"].extend(
                [
                    "junk",
                    {"t": "later", "u": {"fh": 1}},
                    {"t": int(NOW * 1000), "u": {"fh": 200}},
                    {"t": int(NOW * 1000), "u": {"fh": True}},
                ]
            )
            path = write_history(directory, payload)
            samples = read_usage_samples(path)
        self.assertEqual(1, len(samples))

    def test_samples_are_returned_in_time_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_history(
                directory,
                history([(NOW, {"fh": 9}), (NOW - 900, {"fh": 4}), (NOW - 1800, {"fh": 1})]),
            )
            samples = read_usage_samples(path)
        self.assertEqual([1.0, 4.0, 9.0], [s.five_hour_percent for s in samples])


class WindowDerivationTests(unittest.TestCase):
    def samples(self, pairs):
        return [UsageSample(at, fh, sd) for at, fh, sd in pairs]

    def test_no_samples_yields_nothing(self):
        self.assertIsNone(derive_window([], now=NOW))

    def test_zero_usage_means_no_window_is_running(self):
        window = derive_window(
            self.samples([(NOW - 900, 0.0, 64.0), (NOW, 0.0, 64.0)]), now=NOW
        )
        self.assertFalse(window.active)
        self.assertIsNone(window.estimated_reset_at)

    def test_reset_is_derived_from_the_first_non_zero_sample(self):
        start = NOW - 1800
        window = derive_window(
            self.samples(
                [
                    (start - 900, 0.0, 60.0),
                    (start, 30.0, 61.0),
                    (NOW, 43.0, 62.0),
                ]
            ),
            now=NOW,
        )
        self.assertTrue(window.active)
        self.assertEqual(int(start + FIVE_HOURS_SECONDS), window.estimated_reset_at)

    def test_estimate_is_the_latest_possible_reset_so_acting_is_never_early(self):
        """True start lies between the last zero and the first non-zero sample."""
        last_zero, first_active = NOW - 2700, NOW - 1800
        window = derive_window(
            self.samples([(last_zero, 0.0, 1.0), (first_active, 30.0, 2.0), (NOW, 40.0, 2.0)]),
            now=NOW,
        )
        earliest_possible = last_zero + FIVE_HOURS_SECONDS
        self.assertGreater(window.estimated_reset_at, earliest_possible)
        self.assertEqual(first_active - last_zero, window.estimate_error_seconds)

    def test_a_previous_window_does_not_extend_the_current_one(self):
        window = derive_window(
            self.samples(
                [
                    (NOW - 7200, 80.0, 50.0),  # old window
                    (NOW - 5400, 0.0, 50.0),  # it reset
                    (NOW - 1800, 20.0, 51.0),  # new window starts here
                    (NOW, 25.0, 51.0),
                ]
            ),
            now=NOW,
        )
        self.assertEqual(int(NOW - 1800 + FIVE_HOURS_SECONDS), window.estimated_reset_at)

    def test_an_old_reading_is_marked_stale(self):
        window = derive_window(
            self.samples([(NOW - STALE_AFTER_SECONDS - 1, 20.0, 30.0)]), now=NOW
        )
        self.assertTrue(window.stale)

    def test_a_recent_reading_is_not_stale(self):
        window = derive_window(self.samples([(NOW - 60, 20.0, 30.0)]), now=NOW)
        self.assertFalse(window.stale)


class ExecutableDiscoveryTests(unittest.TestCase):
    """The terminal CLI and the Desktop-bundled binary are different installs."""

    def test_desktop_bundled_binary_is_preferred_over_the_terminal_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            roaming = Path(directory)
            versions = roaming / "Claude" / "claude-code"
            for name in ("2.1.246", "2.1.247"):
                (versions / name).mkdir(parents=True)
                (versions / name / "claude.exe").write_bytes(b"desktop")
            with mock.patch.dict(os.environ, {"APPDATA": str(roaming)}):
                found = find_claude_executable()
                listed = desktop_claude_executables()
        self.assertEqual("2.1.247", found.parent.name)
        self.assertEqual(["2.1.247", "2.1.246"], [p.parent.name for p in listed])

    def test_versions_sort_numerically_not_lexically(self):
        with tempfile.TemporaryDirectory() as directory:
            roaming = Path(directory)
            versions = roaming / "Claude" / "claude-code"
            for name in ("2.1.9", "2.1.10"):
                (versions / name).mkdir(parents=True)
                (versions / name / "claude.exe").write_bytes(b"desktop")
            with mock.patch.dict(os.environ, {"APPDATA": str(roaming)}):
                listed = desktop_claude_executables()
        self.assertEqual("2.1.10", listed[0].parent.name)

    def test_absent_desktop_install_falls_back_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"APPDATA": directory}):
                self.assertEqual((), desktop_claude_executables())


class ProviderIntegrationTests(unittest.TestCase):
    def provider(self, window):
        return ClaudeProvider(
            executable_finder=lambda: Path("C:/claude.exe"),
            identity_reader=lambda path: "claude-file:1",
            version_reader=lambda path: "2.1.247",
            status_store=mock.Mock(load=mock.Mock(return_value=None)),
            status_integration=mock.Mock(),
            desktop_observer=lambda now: window,
            now=lambda: NOW,
        )

    def active_window(self, **overrides):
        base = dict(
            observed_at=NOW - 300,
            five_hour_percent=3.0,
            seven_day_percent=64.0,
            estimated_reset_at=int(NOW + 16000),
            estimate_error_seconds=900.0,
            active=True,
            stale=False,
        )
        base.update(overrides)
        return claude_desktop.DesktopWindow(**base)

    def test_desktop_evidence_populates_the_dashboard(self):
        state = self.provider(self.active_window()).detect()
        self.assertEqual("Ready", state.status)
        self.assertEqual(3.0, state.used_percent)
        self.assertEqual(64.0, state.weekly_used_percent)
        self.assertEqual(int(NOW + 16000), state.reset_at)

    def test_a_derived_reset_is_never_recorded_as_verified(self):
        """Only a reported boundary counts as verification."""
        state = self.provider(self.active_window()).detect()
        self.assertIsNone(state.last_verified_at)

    def test_desktop_evidence_carries_no_weekly_reset_so_the_guard_holds(self):
        from sentinel.claude_runtime import ClaudeOperationRunner

        state = self.provider(self.active_window()).detect()
        self.assertIsNone(state.weekly_reset_at)
        outcome = ClaudeOperationRunner._eligibility("rollover", state, NOW + 20000)
        self.assertEqual("WEEKLY_UNAVAILABLE", outcome[0])

    def test_a_fresh_desktop_reading_holds_automation_past_its_own_boundary(self):
        """First layer: no churn while the reading is current."""
        from sentinel.app_state import automation_decision

        state = self.provider(self.active_window()).detect()
        for offset in (0, 16_100, 30_000):
            with self.subTest(offset=offset):
                decision = automation_decision(
                    True,
                    state,
                    now=NOW + offset,
                    compatible_runtime_identity=state.runtime_identity,
                    checked_runtime_identity=state.runtime_identity,
                )
                self.assertIn(decision.action, {"WAIT", "NONE"})

    def test_even_a_rollover_decision_sends_nothing_from_desktop_evidence(self):
        """Second layer: the weekly guard refuses, whatever the decision says.

        A held decision keeps the UI quiet, but it is not what makes this safe.
        The guarantee is that Desktop evidence carries no weekly reset, so the
        mandatory weekly check can never pass and no request is ever built.
        """
        from sentinel.claude_runtime import ClaudeOperationRunner

        state = self.provider(self.active_window()).detect()
        for mode in ("bootstrap", "rollover"):
            for offset in (16_100, 60_000, 200_000):
                with self.subTest(mode=mode, offset=offset):
                    outcome = ClaudeOperationRunner._eligibility(mode, state, NOW + offset)
                    self.assertIsNotNone(outcome, "eligibility must refuse")
                    self.assertIn(
                        outcome[0],
                        {"WEEKLY_UNAVAILABLE", "BOOTSTRAP_NOT_ELIGIBLE", "ALREADY_READY"},
                    )

    def test_an_idle_desktop_window_reports_no_countdown(self):
        state = self.provider(
            self.active_window(active=False, estimated_reset_at=None, five_hour_percent=0.0)
        ).detect()
        self.assertIsNone(state.reset_at)
        self.assertIn("no five-hour window", state.detail)

    def test_a_stale_reading_says_so_rather_than_showing_a_countdown(self):
        state = self.provider(self.active_window(stale=True)).detect()
        self.assertIsNone(state.reset_at)
        self.assertIn("not recorded usage recently", state.detail)

    def test_no_desktop_evidence_leaves_the_original_detection_alone(self):
        state = self.provider(None).detect()
        self.assertEqual("Waiting", state.status)
        self.assertIn("Compatibility", state.detail)

    def test_an_observer_failure_is_swallowed_and_falls_back(self):
        provider = ClaudeProvider(
            executable_finder=lambda: Path("C:/claude.exe"),
            identity_reader=lambda path: "claude-file:1",
            status_store=mock.Mock(load=mock.Mock(return_value=None)),
            status_integration=mock.Mock(),
            desktop_observer=mock.Mock(side_effect=OSError("locked")),
            now=lambda: NOW,
        )
        self.assertEqual("Waiting", provider.detect().status)


if __name__ == "__main__":
    unittest.main()
