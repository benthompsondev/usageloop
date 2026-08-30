"""The Settings health surface has to be readable and honest without Qt running."""

import unittest

from sentinel.app_state import AppSettings, ProviderViewState
from sentinel.diagnostics import (
    STALE_AFTER_SECONDS,
    build_health_rows,
    local_state_health,
    overall_summary,
    provider_health,
    startup_health,
    technical_summary,
)


NOW = 1_788_100_000.0


def state(**overrides):
    base = dict(
        provider_id="codex",
        display_name="Codex",
        installed=True,
        automation_supported=True,
        status="Waiting",
        detail="Detected.",
    )
    base.update(overrides)
    return ProviderViewState(**base)


class ProviderHealthTests(unittest.TestCase):
    def health(self, provider_state, *, automation=True, compatible=None):
        return provider_health(
            provider_state,
            automation_enabled=automation,
            compatible_identity=compatible,
            now=NOW,
        )

    def test_missing_provider_is_neutral_not_an_error(self):
        row = self.health(state(installed=False))
        self.assertEqual("Not found", row.status)
        self.assertEqual("neutral", row.tone)
        self.assertIn("not installed", row.detail)

    def test_needs_attention_is_an_error_and_says_nothing_was_retried(self):
        row = self.health(state(status="Needs attention"))
        self.assertEqual("Needs attention", row.status)
        self.assertEqual("error", row.tone)
        self.assertIn("retried", row.detail)

    def test_unsupported_automation_reads_as_paused(self):
        row = self.health(state(automation_supported=False))
        self.assertEqual("Paused", row.status)
        self.assertEqual("warning", row.tone)

    def test_open_window_reads_as_ready(self):
        row = self.health(state(reset_at=int(NOW + 9000), last_verified_at=NOW - 60))
        self.assertEqual("Ready", row.status)
        self.assertEqual("success", row.tone)

    def test_old_verification_reads_as_stale_rather_than_ready(self):
        row = self.health(
            state(
                reset_at=int(NOW + 9000),
                last_verified_at=NOW - STALE_AFTER_SECONDS - 1,
            )
        )
        self.assertEqual("Stale", row.status)
        self.assertEqual("warning", row.tone)

    def test_automation_off_reads_as_detected_and_points_at_the_switch(self):
        row = self.health(state(), automation=False)
        self.assertEqual("Detected", row.status)
        self.assertIn("main switch", row.detail)

    def test_checking_state_is_reported_while_a_check_runs(self):
        row = self.health(state(status="Starting"))
        self.assertEqual("Checking", row.status)
        self.assertEqual("info", row.tone)

    def test_compatible_runtime_waiting_is_informational(self):
        row = self.health(
            state(runtime_identity="id-1", usage_checked_at=NOW), compatible="id-1"
        )
        self.assertEqual("Waiting", row.status)
        self.assertEqual("info", row.tone)

    def test_compatible_provider_without_a_quota_read_is_not_claimed_as_waiting(self):
        row = self.health(state(runtime_identity="id-1"), compatible="id-1")
        self.assertEqual("Not checked", row.status)
        self.assertEqual("info", row.tone)
        self.assertIn("No five-hour window reading", row.detail)

    def test_no_row_leaks_raw_internal_wording(self):
        for provider_state in (
            state(),
            state(installed=False),
            state(status="Needs attention"),
            state(automation_supported=False),
            state(status="Starting"),
        ):
            row = self.health(provider_state)
            for banned in ("runtime_identity", "None", "app-server", "idempotency"):
                self.assertNotIn(banned, row.detail)


class LocalStateHealthTests(unittest.TestCase):
    def test_first_run_is_not_a_problem(self):
        row = local_state_health(
            state_file_exists=False, newest_observation_at=None, now=NOW
        )
        self.assertEqual("Not saved yet", row.status)
        self.assertEqual("neutral", row.tone)

    def test_recent_reading_is_healthy(self):
        row = local_state_health(
            state_file_exists=True, newest_observation_at=NOW - 60, now=NOW
        )
        self.assertEqual("Healthy", row.status)
        self.assertEqual("success", row.tone)

    def test_old_reading_is_stale(self):
        row = local_state_health(
            state_file_exists=True,
            newest_observation_at=NOW - STALE_AFTER_SECONDS - 1,
            now=NOW,
        )
        self.assertEqual("Stale", row.status)
        self.assertEqual("warning", row.tone)

    def test_saved_but_no_readings_is_still_healthy(self):
        row = local_state_health(
            state_file_exists=True, newest_observation_at=None, now=NOW
        )
        self.assertEqual("Healthy", row.status)


class StartupHealthTests(unittest.TestCase):
    def test_enabled_and_disabled_both_read_plainly(self):
        self.assertEqual("Enabled", startup_health(True).status)
        self.assertEqual("Disabled", startup_health(False).status)
        self.assertEqual("neutral", startup_health(False).tone)


class SummaryTests(unittest.TestCase):
    def rows(self, **overrides):
        settings = AppSettings(automation_enabled=overrides.pop("automation", False))
        states = overrides.pop("states", {"codex": state()})
        return build_health_rows(
            states,
            settings,
            startup_enabled=overrides.pop("startup", False),
            state_file_exists=overrides.pop("state_file", True),
            now=NOW,
        )

    def test_every_expected_row_is_present(self):
        rows = self.rows(states={"codex": state(), "claude": state(provider_id="claude", display_name="Claude Code")})
        labels = [row.label for row in rows]
        self.assertEqual(
            ["Codex", "Claude Code", "Automation", "Local data", "Windows startup"],
            labels,
        )

    def test_overall_reports_attention_when_any_row_is_an_error(self):
        rows = self.rows(states={"codex": state(status="Needs attention")})
        self.assertEqual("error", overall_summary(rows).tone)

    def test_overall_reports_a_warning_when_something_is_stale(self):
        rows = self.rows(
            states={
                "codex": state(
                    reset_at=int(NOW + 9000),
                    last_verified_at=NOW - STALE_AFTER_SECONDS - 1,
                )
            }
        )
        self.assertEqual("warning", overall_summary(rows).tone)

    def test_all_good_requires_automation_on_and_a_ready_provider(self):
        rows = self.rows(
            automation=True,
            states={"codex": state(reset_at=int(NOW + 9000), last_verified_at=NOW)},
        )
        summary = overall_summary(rows, automation_enabled=True)
        self.assertEqual("All good", summary.status)
        self.assertEqual("success", summary.tone)

    def test_installed_but_switched_off_reports_setup_ok_not_all_good(self):
        """Claiming success before anything is verified is how trust is lost."""
        rows = self.rows(states={"codex": state(reset_at=int(NOW + 9000), last_verified_at=NOW)})
        summary = overall_summary(rows, automation_enabled=False)
        self.assertEqual("Setup OK", summary.status)
        self.assertEqual("info", summary.tone)
        self.assertIn("installed correctly", summary.detail)

    def test_automation_on_with_nothing_verified_is_still_only_setup_ok(self):
        rows = self.rows(automation=True, states={"codex": state()})
        summary = overall_summary(rows, automation_enabled=True)
        self.assertEqual("Setup OK", summary.status)

    def test_one_ready_provider_does_not_hide_an_unchecked_provider(self):
        rows = self.rows(
            automation=True,
            states={
                "codex": state(
                    reset_at=int(NOW + 9000),
                    last_verified_at=NOW,
                    usage_checked_at=NOW,
                ),
                "claude": state(
                    provider_id="claude",
                    display_name="Claude Code",
                    runtime_identity="claude:1",
                ),
            },
        )
        summary = overall_summary(rows, automation_enabled=True)
        self.assertEqual("Partly ready", summary.status)
        self.assertEqual("info", summary.tone)

    def test_automation_row_states_the_off_guarantee(self):
        rows = self.rows(automation=False)
        automation = next(row for row in rows if row.label == "Automation")
        self.assertEqual("Off", automation.status)
        self.assertIn("Nothing is sent", automation.detail)


class TechnicalSummaryTests(unittest.TestCase):
    def summary(self, **kwargs):
        return technical_summary(
            {"codex": state(runtime_version="0.96.0", runtime_identity="id-1")},
            AppSettings(**kwargs),
        )

    def test_summary_keeps_the_troubleshooting_detail(self):
        text = self.summary()
        for expected in ("Codex", "Installed: yes", "Version: 0.96.0", "Raw state:", "Automation"):
            self.assertIn(expected, text)

    def test_summary_names_the_product_and_version(self):
        self.assertIn("UsageLoop", self.summary().splitlines()[0])

    def test_summary_reports_confirmed_compatibility(self):
        text = technical_summary(
            {"codex": state(runtime_identity="id-1")},
            AppSettings(compatible_runtime_identities={"codex": "id-1"}),
        )
        self.assertIn("Compatibility: passed", text)

    def test_summary_carries_nothing_private(self):
        """The summary is meant to be pasted into a bug report.

        "prompt-free" is allowed because it describes the mechanism; what must
        never appear is an actual secret, path to one, or account identity.
        """
        text = self.summary().lower()
        for banned in ("auth.json", "@", "bearer", "sk-ant", "password", "credential"):
            self.assertNotIn(banned, text)
        self.assertNotIn("prompt:", text)

    def test_summary_explains_both_providers_for_troubleshooting(self):
        text = self.summary()
        self.assertIn("Provider support", text)
        self.assertIn("Codex: verified", text)
        self.assertIn("Claude Code: preview", text)

    def test_summary_records_the_two_claude_hosts_and_their_channels(self):
        """The Desktop/terminal split is the thing a bug report needs to say."""
        text = self.summary()
        self.assertIn("terminal CLI", text)
        self.assertIn("Claude Desktop", text)
        self.assertIn("plan-usage history", text)
        self.assertIn("estimate", text)
        self.assertIn("weekly guard cannot pass", text)
        self.assertIn("unproven", text)


if __name__ == "__main__":
    unittest.main()
