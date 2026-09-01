import os
import sys
import unittest
from unittest import mock


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux integration contract")
class LinuxPlatformContractTests(unittest.TestCase):
    def test_startup_module_does_not_load_windows_registry(self):
        from sentinel import startup

        self.assertIsNone(startup._winreg)
        self.assertNotIn("winreg", sys.modules)

    def test_default_provider_detection_never_calls_windows_version_api(self):
        from pathlib import Path
        import tempfile

        from sentinel.history import SafeHistory
        from sentinel.providers import CodexProvider

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "codex"
            executable.touch()
            with mock.patch(
                "sentinel.providers.windows_file_version",
                side_effect=AssertionError("Windows version API ran on Linux"),
            ):
                state = CodexProvider(
                    history=SafeHistory(Path(directory) / "history.jsonl"),
                    executable_finder=lambda: executable,
                ).detect()
        self.assertTrue(state.installed)
        self.assertIsNone(state.runtime_version)


if __name__ == "__main__":
    unittest.main()
