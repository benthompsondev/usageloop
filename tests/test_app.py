import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest import mock

from sentinel.app import main


class PackageSmokeTests(unittest.TestCase):
    def test_package_smoke_launches_qt_without_constructing_a_provider(self):
        with mock.patch(
            "sentinel.app.CodexProvider",
            side_effect=AssertionError("package smoke must not start provider work"),
        ):
            self.assertEqual(0, main(["--package-smoke"]))


if __name__ == "__main__":
    unittest.main()
