import os
from pathlib import Path
import tempfile
from threading import Event, get_ident
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sentinel.app_controller import ApplicationController
from sentinel.app_state import AppSettings, AppStateStore, ProviderViewState
from sentinel.cli import connect
from sentinel.desktop import MainWindow
from sentinel.diagnostics import technical_summary
from sentinel.history import SafeHistory
from sentinel.providers import CodexProvider, file_runtime_identity
from sentinel.transport import (
    CODEX_EXECUTABLE_ENV,
    AppServerUnavailableError,
    CodexOverrideError,
    SentinelRuntimeError,
    build_codex_command,
    discover_codex_runtime,
)
from test_desktop import FakeStartup


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

    def test_known_repair_is_redacted_when_local_app_data_has_an_alias(self):
        repair = self.binary(self.codex / "manual-cli" / "26.9.5.0" / "codex.exe")
        self.registered.return_value = (("26.9.5.0", self.package),)
        os.environ["LOCALAPPDATA"] = str(self.local / ".." / self.local.name)
        runtime = discover_codex_runtime()
        self.assertEqual(repair, runtime.executable)
        self.assertIn(r"%LOCALAPPDATA%\OpenAI\Codex\manual-cli\26.9.5.0\codex.exe",
                      runtime.diagnostic)
        self.assertNotIn(str(self.root.resolve()), runtime.diagnostic)

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

    def test_action_rejects_a_runtime_changed_since_the_scheduler_decision(self):
        self.binary(self.path / "codex.exe")
        runner = Mock()
        provider = CodexProvider(
            history=SafeHistory(self.root / "history.jsonl"), operation_runner=runner,
        )
        expected = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="previous-runtime",
        )
        with self.assertRaisesRegex(SentinelRuntimeError, "runtime changed"):
            provider.run_action("rollover", current_state=expected)
        runner.run.assert_not_called()

    def test_slow_version_check_keeps_gui_responsive_and_gates_scheduled_starts(self):
        app = QApplication.instance() or QApplication([])
        stalled = self.binary(self.codex / "bin" / "0123456789abcdef" / "codex.exe")
        fallback = self.binary(self.path / "codex.exe")
        reading = Event()
        release = Event()
        version_threads = []

        def read_version(executable, *, timeout):
            version_threads.append(get_ident())
            if executable == stalled:
                reading.set()
                release.wait(3)
                raise AppServerUnavailableError("The Codex version check timed out.")
            return self.version.return_value

        def pump_until(predicate):
            deadline = time.monotonic() + 2
            while not predicate() and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertTrue(predicate())

        self.version.side_effect = read_version
        now = time.time()
        runner = Mock()
        history = SafeHistory(self.root / "history.jsonl")
        provider = CodexProvider(history=history, operation_runner=runner)
        previous = ProviderViewState.waiting(
            "codex", "Codex", installed=True, runtime_identity="previous-runtime",
        ).with_reset(int(now - 120), verified_at=now - 240)
        store = AppStateStore(self.root / "state.json")
        store.save(AppSettings(
            automation_enabled=True, first_run_complete=True,
            compatible_runtime_identities={"codex": "previous-runtime"},
        ), {})
        controller = ApplicationController([provider], store)
        controller.start({"codex": previous})
        self.assertEqual("ROLLOVER", controller.decisions(now=now)["codex"].action)
        window = MainWindow(controller, {"codex": provider}, FakeStartup())
        window.clock_timer.stop()
        window.automation_timer.stop()
        pool = QThreadPool()
        window.thread_pool = pool
        heartbeat = []
        try:
            window.evaluate_automation(now=now)
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            pump_until(lambda: reading.is_set() and bool(heartbeat))
            self.assertNotEqual(get_ident(), version_threads[0])
            self.assertEqual("previous-runtime", controller.states["codex"].runtime_identity)
            self.assertEqual({}, window.active_operations)
            window.evaluate_automation(now=now)
            runner.run.assert_not_called()

            # Changes made while discovery is running must govern its result.
            window.dashboard_automation_toggle.setChecked(False)
            self.assertFalse(controller.settings.automation_enabled)
            release.set()
            pump_until(lambda: controller.states["codex"].runtime_identity == file_runtime_identity(fallback))
            self.assertIn("PATH", controller.states["codex"].runtime_discovery)
            self.assertIn("timed out", controller.states["codex"].runtime_discovery_warning)
            self.assertEqual([], runner.mock_calls)
            self.assertEqual([], history.trigger_attempts())
            self.assertEqual([False, False], [thread == get_ident() for thread in version_threads])
        finally:
            release.set()
            pool.waitForDone(4000)
            app.processEvents()
            window.close()


if __name__ == "__main__":
    unittest.main()
