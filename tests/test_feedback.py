from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from sentinel.product import PRODUCT


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / ".github" / "ISSUE_TEMPLATE"


class IssueFormTests(unittest.TestCase):
    def load_yaml(self, name: str):
        with (TEMPLATE_ROOT / name).open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)

    def test_feedback_urls_are_centralized_and_open_the_expected_forms(self) -> None:
        self.assertEqual(
            "https://github.com/benthompsondev/usageloop/issues/new?template=bug_report.yml",
            PRODUCT.bug_report_url,
        )
        self.assertEqual(
            "https://github.com/benthompsondev/usageloop/issues/new?template=feature_request.yml",
            PRODUCT.feature_request_url,
        )

    def test_issue_forms_are_quick_with_only_one_required_answer_each(self) -> None:
        bug = self.load_yaml("bug_report.yml")
        feature = self.load_yaml("feature_request.yml")

        self.assertEqual(
            [
                "what_went_wrong",
                "anything_else",
                "diagnostic_summary",
            ],
            [item["id"] for item in bug["body"] if "id" in item],
        )
        self.assertEqual(
            ["usefulness", "anything_else"],
            [item["id"] for item in feature["body"] if "id" in item],
        )

        for form in (bug, feature):
            fields = [item for item in form["body"] if "id" in item]
            required = [
                item["id"]
                for item in fields
                if item.get("validations", {}).get("required") is True
            ]
            self.assertEqual([fields[0]["id"]], required)

    def test_issue_forms_render_friendly_low_pressure_questions(self) -> None:
        bug = self.load_yaml("bug_report.yml")
        feature = self.load_yaml("feature_request.yml")

        bug_fields = {
            item["id"]: item["attributes"] for item in bug["body"] if "id" in item
        }
        feature_fields = {
            item["id"]: item["attributes"]
            for item in feature["body"]
            if "id" in item
        }

        self.assertEqual("What went wrong?", bug_fields["what_went_wrong"]["label"])
        self.assertEqual(
            "Anything else that might help?", bug_fields["anything_else"]["label"]
        )
        self.assertIn("skip", bug_fields["anything_else"]["description"].lower())
        self.assertEqual(
            "What would make UsageLoop more useful for you?",
            feature_fields["usefulness"]["label"],
        )
        self.assertEqual(
            "Anything else you'd like to add?",
            feature_fields["anything_else"]["label"],
        )

    def test_blank_issues_are_disabled(self) -> None:
        config = self.load_yaml("config.yml")
        self.assertIs(config["blank_issues_enabled"], False)

    def test_both_forms_warn_against_pasting_private_codex_data(self) -> None:
        required_warnings = (
            "passwords",
            "api keys",
            "private codex conversations",
            "other sensitive information",
        )
        for name in ("bug_report.yml", "feature_request.yml"):
            form = self.load_yaml(name)
            markdown = " ".join(
                item.get("attributes", {}).get("value", "")
                for item in form["body"]
                if item.get("type") == "markdown"
            ).lower()
            for warning in required_warnings:
                with self.subTest(form=name, warning=warning):
                    self.assertIn(warning, markdown)

    def test_no_interactive_field_solicits_sensitive_codex_data(self) -> None:
        sensitive_requests = (
            "credential",
            "api key",
            "codex prompt",
            "codex response",
            "conversation",
            "account information",
            "auth file",
            "unrelated log",
        )
        for name in ("bug_report.yml", "feature_request.yml"):
            form = self.load_yaml(name)
            for item in form["body"]:
                if item.get("type") == "markdown":
                    continue
                attributes = item.get("attributes", {})
                prompt = " ".join(
                    str(attributes.get(key, ""))
                    for key in ("label", "description", "placeholder")
                ).lower()
                for sensitive in sensitive_requests:
                    with self.subTest(form=name, field=item.get("id"), term=sensitive):
                        self.assertNotIn(sensitive, prompt)


class ScreenshotToolTests(unittest.TestCase):
    def test_screenshot_tool_generates_current_dashboard_settings_and_about(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "capture-ui-screenshots.py"),
                    # Rendering into a temp directory cannot disturb the
                    # published images, so the foreign-host guard is waived
                    # rather than making the test host-dependent.
                    "--allow-foreign-host",
                    directory,
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            for name in (
                "dashboard.png",
                "settings.png",
                "settings-weekly.png",
                "settings-weekly-time-focused.png",
                "settings-weekly-expanded.png",
                "settings-weekly-expanded-1024x768.png",
                "about.png",
            ):
                target = Path(directory) / name
                with self.subTest(image=name):
                    self.assertTrue(target.is_file())
                    self.assertGreater(target.stat().st_size, 10_000)

    def test_the_linux_set_renders_only_the_pages_that_differ(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "capture-ui-screenshots.py"),
                    "--platform",
                    "Linux",
                    directory,
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            rendered = sorted(p.name for p in Path(directory).iterdir())
            # Every other page is identical on both hosts, so the Linux set
            # stays three images instead of a duplicate of the whole gallery.
            self.assertEqual(
                ["about-linux.png", "settings-linux.png", "updates-linux.png"],
                rendered,
            )
            for name in rendered:
                with self.subTest(image=name):
                    self.assertGreater((Path(directory) / name).stat().st_size, 10_000)

    def test_the_windows_set_is_not_rewritten_from_a_foreign_host(self) -> None:
        if os.name == "nt":
            self.skipTest("the guard only applies off Windows")
        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            environment["QT_QPA_PLATFORM"] = "offscreen"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "capture-ui-screenshots.py"),
                    directory,
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )

        # Font metrics differ per host, so an accidental run here would rewrite
        # every published Windows image with a subtly different render.
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Refusing to rewrite", result.stderr)


if __name__ == "__main__":
    unittest.main()
