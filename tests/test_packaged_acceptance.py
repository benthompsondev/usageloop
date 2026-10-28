"""The packaged acceptance entry point must never construct a real provider."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sentinel.app import main


class PackagedAcceptanceTests(unittest.TestCase):
    def test_real_pages_in_six_languages_without_production_startup(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("sentinel.app.CodexProvider", side_effect=AssertionError("Real provider")), \
                patch("sentinel.app.AppStateStore", side_effect=AssertionError("Real settings")), \
                patch("sentinel.app.SingleInstanceGuard", side_effect=AssertionError("Real IPC")), \
                patch("sentinel.app.create_startup_manager", side_effect=AssertionError("Real startup")):
            self.assertEqual(0, main(["--ui-acceptance", directory]))
            report = json.loads((Path(directory) / "acceptance.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual({"en", "zh_CN", "es", "pt_BR", "fr", "ja"},
                             {entry["language"] for entry in report["languages"]})
            self.assertEqual(48, len(list(Path(directory).glob("*/*.png"))))
