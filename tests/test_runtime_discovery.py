import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel.app_state import AppSettings
from sentinel.cli import connect
from sentinel.diagnostics import technical_summary
from sentinel.providers import CodexProvider
from sentinel.transport import (
    CODEX_EXECUTABLE_ENV,
    AppServerUnavailableError,
    CodexOverrideError,
    SentinelRuntimeError,
    build_codex_command,
    discover_codex_runtime,
)


class RuntimeDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.local = self.root / "User Folder" / "AppData" / "Local"
        self.codex = self.local / "OpenAI" / "Codex"
        self.package = self.root / "Other Volume" / "WindowsApps" / "Codex package"
        self.path = self.root / "Standalone CLI"
        self.enterContext(patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.local),
            "PATH": f'"{self.path}"',
            CODEX_EXECUTABLE_ENV: "",
        }))
        self.windows = self.enterContext(patch("sentinel.transport.is_windows", return_value=True))
        self.registered = self.enterContext(patch(
            "sentinel.transport.registered_codex_packages", return_value=()
        ))
        self.version = self.enterContext(patch(
            "sentinel.transport.read_codex_version", return_value="codex-cli 0.171.0-alpha.13"
        ))

    def binary(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path.resolve()

    def test_explicit_override_with_spaces_wins_without_scanning_msix(self):
        self.binary(self.codex / "bin" / "old-hash" / "codex.exe")
        explicit = self.binary(self.root / "Private Tools" / "codex.exe")
        os.environ[CODEX_EXECUTABLE_ENV] = str(explicit)
        runtime = discover_codex_runtime()
        self.assertEqual(explicit, runtime.executable)
        self.assertEqual("Explicit override (<path hidden>/codex.exe)", runtime.diagnostic)
        self.assertEqual([str(explicit), "app-server", "--stdio"],
                         build_codex_command(runtime.executable, "app-server", "--stdio"))
        self.registered.assert_not_called()

    def test_linux_override_reports_cli_version_without_a_pe_resource(self):
        self.windows.return_value = False
        explicit = self.binary(self.root / "Linux install" / "codex")
        os.environ[CODEX_EXECUTABLE_ENV] = str(explicit)
        runtime = discover_codex_runtime()
        self.assertEqual(explicit, runtime.executable)
        self.assertEqual(self.version.return_value, runtime.version)

    def test_missing_override_surfaces_a_safe_diagnostic_without_fallback(self):
        self.binary(self.path / "codex.exe")
        os.environ[CODEX_EXECUTABLE_ENV] = str(self.root / "Private Tools" / "codex.exe")
        with self.assertRaises(CodexOverrideError) as caught:
            discover_codex_runtime()
        self.assertIn(CODEX_EXECUTABLE_ENV, str(caught.exception))
        state = CodexProvider().detect()
        text = technical_summary({"codex": state}, AppSettings())
        self.assertFalse(state.automation_supported)
        self.assertIn("Runtime: Explicit override", text)
        self.assertIn("does not name an accessible file", text)
        self.assertNotIn("Private Tools", text)
        self.version.assert_not_called()
        self.registered.assert_not_called()

    def test_explicit_binary_with_unrecognized_version_stops_discovery(self):
        self.binary(self.path / "codex.exe")
        explicit = self.binary(self.root / "Private Tools" / "codex.exe")
        os.environ[CODEX_EXECUTABLE_ENV] = str(explicit)
        self.version.return_value = "unexpected output with private data"
        with self.assertRaisesRegex(CodexOverrideError, "unrecognized version string") as caught:
            discover_codex_runtime()
        self.assertNotIn("private data", str(caught.exception))
        self.assertEqual(1, self.version.call_count)

    def test_registered_version_uses_matching_repair_over_other_installs(self):
        self.registered.return_value = (("26.9.5.0", self.package),)
        self.binary(self.package / "app" / "resources" / "codex.exe")
        current = self.binary(self.codex / "manual-cli" / "26.9.5.0" / "codex.exe")
        self.binary(self.codex / "manual-cli" / "26.10.0.0" / "codex.exe")
        legacy = self.binary(self.codex / "bin" / "0123456789abcdef" / "codex.exe")
        os.utime(legacy, (current.stat().st_mtime + 3600,) * 2)
        runtime = discover_codex_runtime()
        self.assertEqual(current, runtime.executable)
        self.assertIn(r"%LOCALAPPDATA%\OpenAI\Codex\manual-cli\26.9.5.0\codex.exe", runtime.diagnostic)

    def test_broken_matching_repair_falls_back_to_registered_install(self):
        self.registered.return_value = (("26.9.5.0", self.package),)
        repair = self.binary(self.codex / "manual-cli" / "26.9.5.0" / "codex.exe")
        bundled = self.binary(self.package / "app" / "resources" / "codex.exe")
        self.version.side_effect = [AppServerUnavailableError("The Codex version check timed out."),
                                    self.version.return_value]
        runtime = discover_codex_runtime()
        self.assertEqual(bundled, runtime.executable)
        self.assertEqual("Registered Codex MSIX", runtime.source)
        self.assertIn("Desktop repair matching MSIX: The Codex version check timed out", runtime.skipped)
        self.assertEqual([repair, bundled], [call.args[0] for call in self.version.call_args_list])

    def test_without_msix_versioned_repairs_sort_numerically(self):
        self.binary(self.codex / "manual-cli" / "26.9.5.0" / "codex.exe")
        current = self.binary(self.codex / "manual-cli" / "26.10.0.0" / "codex.exe")
        self.binary(self.codex / "manual-cli" / "codex.exe")
        self.assertEqual(current, discover_codex_runtime().executable)

    def test_failed_managed_runtime_falls_back_to_path(self):
        self.binary(self.codex / "bin" / "0123456789abcdef" / "codex.exe")
        standalone = self.binary(self.path / "codex.exe")
        self.version.side_effect = [AppServerUnavailableError(), self.version.return_value]
        runtime = discover_codex_runtime()
        self.assertEqual(standalone, runtime.executable)
        self.assertEqual("PATH", runtime.source)
        self.assertIn("Managed Codex bin", runtime.skipped[0])

    def test_changed_runtime_is_rejected_before_app_server_starts(self):
        self.binary(self.path / "codex.exe")
        with (patch("sentinel.cli.CodexProcessTransport") as launch,
              self.assertRaises(SentinelRuntimeError) as caught):
            connect(expected_runtime_identity="previous-runtime")
        self.assertEqual("codex_runtime_changed", caught.exception.category)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
