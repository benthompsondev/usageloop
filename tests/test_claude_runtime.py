from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from sentinel.app_state import ProviderViewState
from sentinel.claude_runtime import (
    ClaudeAttemptStore,
    ClaudeInitTrigger,
    ClaudeOperationRunner,
    ClaudeProcessOutcome,
    build_claude_init_command,
    executable_supports_init_only,
)


NOW = 50_000.0


def rollover_state() -> ProviderViewState:
    return ProviderViewState(
        "claude",
        "Claude Code",
        True,
        True,
        "Waiting",
        "Reset reached.",
        runtime_identity="claude:new",
        reset_at=int(NOW - 30),
        used_percent=0,
        usage_checked_at=NOW - 40,
        weekly_used_percent=20,
        weekly_reset_at=int(NOW + 100_000),
    )


def bootstrap_state() -> ProviderViewState:
    return ProviderViewState(
        "claude",
        "Claude Code",
        True,
        True,
        "Waiting",
        "No active window.",
        runtime_identity="claude:new",
        used_percent=0,
        usage_checked_at=NOW - 10,
        weekly_used_percent=20,
        weekly_reset_at=int(NOW + 100_000),
    )


class ClaudeCapabilityTests(unittest.TestCase):
    def test_capability_detection_with_and_without_init_only(self):
        with tempfile.TemporaryDirectory() as directory:
            supported = Path(directory) / "supported.exe"
            unsupported = Path(directory) / "unsupported.exe"
            supported.write_bytes(b"binary\0--init-only\0data")
            unsupported.write_bytes(b"binary without hidden option")
            self.assertTrue(executable_supports_init_only(supported))
            self.assertFalse(executable_supports_init_only(unsupported))

    def test_probe_does_not_launch_claude(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"--init-only")
            process = mock.Mock()
            runner = ClaudeOperationRunner(
                attempt_store=ClaudeAttemptStore(Path(directory) / "attempts.jsonl"),
                capability_checker=lambda path: True,
                process_runner=process,
                workspace=Path(directory) / "workspace",
            )
            result = runner.probe("runtime:1", executable)
            self.assertTrue(result.compatible)
            process.assert_not_called()

    def test_capability_removed_fails_closed_without_version_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "claude.exe"
            executable.write_bytes(b"new runtime without option")
            runner = ClaudeOperationRunner(
                attempt_store=ClaudeAttemptStore(Path(directory) / "attempts.jsonl"),
                workspace=Path(directory) / "workspace",
            )
            result = runner.probe("runtime:new", executable)
            self.assertFalse(result.compatible)
            self.assertEqual("runtime:new", result.runtime_identity)


class ClaudeCommandTests(unittest.TestCase):
    def test_command_contains_only_prompt_free_initialization_operation(self):
        command = build_claude_init_command(Path("C:/Program Files/Claude/claude.exe"))
        self.assertEqual(
            ["C:\\Program Files\\Claude\\claude.exe", "--init-only"], command
        )
        joined = " ".join(command)
        for forbidden in (" -p ", "--print", "--model", "--prompt", " ok"):
            self.assertNotIn(forbidden, f" {joined} ")

    @unittest.skipUnless(__import__("os").name == "nt", "Windows command shim behavior")
    def test_cmd_shim_uses_comspec_instead_of_direct_createprocess(self):
        with mock.patch.dict("os.environ", {"COMSPEC": "C:\\Windows\\System32\\cmd.exe"}):
            command = build_claude_init_command(Path("C:/Users/Example/AppData/Roaming/npm/claude.cmd"))
        self.assertEqual(
            [
                "C:\\Windows\\System32\\cmd.exe",
                "/d",
                "/c",
                "C:\\Users\\Example\\AppData\\Roaming\\npm\\claude.cmd",
                "--init-only",
            ],
            command,
        )

    @unittest.skipUnless(__import__("os").name == "nt", "Windows npm shim behavior")
    def test_cmd_shim_capability_is_read_from_adjacent_cli_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "claude.cmd"
            cli = root / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
            cli.parent.mkdir(parents=True)
            shim.write_text("@node cli.js %*", encoding="utf-8")
            cli.write_bytes(b"runtime with --init-only support")
            self.assertTrue(executable_supports_init_only(shim))

    def test_process_parser_failure_is_pre_effect_and_recoverable(self):
        completed = mock.Mock(returncode=1, stderr="error: unknown option '--init-only'")
        with mock.patch("sentinel.claude_runtime.subprocess.run", return_value=completed):
            from sentinel.claude_runtime import _run_process

            result = _run_process(["claude.exe", "--init-only"], Path.cwd(), 5)
        self.assertFalse(result.effect_possible)
        self.assertEqual("init_only_unsupported", result.terminal_outcome)

    def test_nonzero_or_timeout_after_launch_is_ambiguous(self):
        completed = mock.Mock(returncode=1, stderr="runtime stopped")
        with mock.patch("sentinel.claude_runtime.subprocess.run", return_value=completed):
            from sentinel.claude_runtime import _run_process

            failed = _run_process(["claude.exe", "--init-only"], Path.cwd(), 5)
        with mock.patch(
            "sentinel.claude_runtime.subprocess.run",
            side_effect=__import__("subprocess").TimeoutExpired("claude", 5),
        ):
            timed_out = _run_process(["claude.exe", "--init-only"], Path.cwd(), 5)
        self.assertTrue(failed.effect_possible)
        self.assertTrue(timed_out.effect_possible)


class ClaudeOneShotTests(unittest.TestCase):
    def make_runner(self, directory: str, process_runner, *, now: float = NOW):
        executable = Path(directory) / "claude.exe"
        executable.write_bytes(b"--init-only")
        runner = ClaudeOperationRunner(
            attempt_store=ClaudeAttemptStore(Path(directory) / "attempts.jsonl"),
            capability_checker=lambda path: True,
            process_runner=process_runner,
            workspace=Path(directory) / "workspace",
            clock=lambda: now,
        )
        return runner, executable

    def test_rollover_runs_exactly_once_and_repeated_polling_is_guarded(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            first = runner.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            second = runner.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            self.assertEqual("INITIALIZATION_POSSIBLE", first.outcome)
            self.assertEqual("ATTEMPT_ALREADY_RECORDED", second.outcome)
            self.assertEqual(1, process.call_count)

    def test_concurrent_calls_reserve_only_one_process_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls: list[list[str]] = []
            calls_lock = threading.Lock()

            def process(command, _workspace, _timeout):
                with calls_lock:
                    calls.append(command)
                return ClaudeProcessOutcome("process_exited_zero", True)

            first, executable = self.make_runner(directory, process)
            second, _ = self.make_runner(directory, process)
            results = []
            barrier = threading.Barrier(2)

            def run(worker):
                barrier.wait()
                results.append(worker.run(
                    "rollover",
                    executable=executable,
                    runtime_identity="runtime:1",
                    state=rollover_state(),
                ))

            threads = [threading.Thread(target=run, args=(worker,)) for worker in (first, second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertEqual(1, len(calls))
            self.assertEqual(
                {"INITIALIZATION_POSSIBLE", "ATTEMPT_ALREADY_RECORDED"},
                {item.outcome for item in results},
            )

    def test_definite_launch_failure_is_recoverable_but_not_auto_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("launch_failed", False))
            runner, executable = self.make_runner(directory, process)
            result = runner.run(
                "bootstrap", executable=executable, runtime_identity="runtime:1", state=bootstrap_state()
            )
            self.assertEqual("INITIALIZATION_NOT_STARTED", result.outcome)
            self.assertEqual("Needs attention", result.state.status)
            self.assertTrue(result.state.retry_after_restart)
            self.assertEqual("failed_recoverable", runner.attempt_store.attempts()[-1].state)

    def test_ambiguous_outcome_gets_full_window_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_timeout", True))
            runner, executable = self.make_runner(directory, process)
            result = runner.run(
                "bootstrap", executable=executable, runtime_identity="runtime:1", state=bootstrap_state()
            )
            self.assertTrue(result.effect_possible)
            self.assertEqual(NOW + 18_000, result.state.automation_blocked_until)
            self.assertEqual("effect_possible", runner.attempt_store.attempts()[-1].state)

    def test_restart_during_reservation_blocks_then_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ClaudeAttemptStore(Path(directory) / "attempts.jsonl")
            store.reserve(mode="bootstrap", idempotency_key="bootstrap:2", now=NOW)
            blocked_process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            blocked, executable = self.make_runner(directory, blocked_process, now=NOW + 30)
            blocked_result = blocked.run(
                "bootstrap", executable=executable, runtime_identity="runtime:1", state=bootstrap_state()
            )
            self.assertEqual("ATTEMPT_ALREADY_RECORDED", blocked_result.outcome)
            blocked_process.assert_not_called()

            recovered_process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            recovered, executable = self.make_runner(directory, recovered_process, now=NOW + 121)
            recovered_result = recovered.run(
                "bootstrap", executable=executable, runtime_identity="runtime:1", state=bootstrap_state()
            )
            self.assertEqual("INITIALIZATION_POSSIBLE", recovered_result.outcome)
            recovered_process.assert_called_once()

    def test_restart_after_launch_attempt_never_launches_again(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ClaudeAttemptStore(Path(directory) / "attempts.jsonl")
            attempt = store.reserve(mode="rollover", idempotency_key=f"rollover:{int(NOW - 30)}", now=NOW)
            store.transition(attempt.attempt_id, "launch_attempted", outcome="launch_attempted", now=NOW)
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            restarted, executable = self.make_runner(directory, process, now=NOW + 500)
            result = restarted.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            self.assertEqual("ATTEMPT_ALREADY_RECORDED", result.outcome)
            process.assert_not_called()

    def test_weekly_missing_or_exhausted_never_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            missing = replace_state(rollover_state(), weekly_used_percent=None)
            exhausted = replace_state(rollover_state(), weekly_used_percent=99)
            self.assertEqual(
                "WEEKLY_UNAVAILABLE",
                runner.run("rollover", executable=executable, runtime_identity="r", state=missing).outcome,
            )
            self.assertEqual(
                "WEEKLY_EXHAUSTED",
                runner.run("rollover", executable=executable, runtime_identity="r", state=exhausted).outcome,
            )
            process.assert_not_called()

    def test_stale_weekly_evidence_never_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            stale = replace_state(rollover_state(), usage_checked_at=NOW - 21_601)
            result = runner.run(
                "rollover", executable=executable, runtime_identity="r", state=stale
            )
            self.assertEqual("WEEKLY_UNAVAILABLE", result.outcome)
            process.assert_not_called()

    def test_attempt_log_contains_no_command_output_or_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_nonzero", True))
            runner, executable = self.make_runner(directory, process)
            runner.run("rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state())
            raw = runner.attempt_store.path.read_text(encoding="utf-8")
            for forbidden in ("--init-only", "prompt", "token", "credential", "stderr", "response"):
                self.assertNotIn(forbidden, raw.lower())

    def test_corrupt_attempt_state_fails_closed_without_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempts.jsonl"
            path.write_text("{truncated", encoding="utf-8")
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            result = runner.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            self.assertEqual("STATE_UNAVAILABLE", result.outcome)
            self.assertEqual("Needs attention", result.state.status)
            process.assert_not_called()

    def test_absolute_reset_verifies_possible_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            runner.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            self.assertTrue(
                runner.verify_observed_reset(int(NOW + 18_000), observed_at=NOW + 60)
            )
            self.assertEqual("verified", runner.attempt_store.attempts()[-1].state)

    def test_unrelated_active_reset_does_not_verify_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            process = mock.Mock(return_value=ClaudeProcessOutcome("process_exited_zero", True))
            runner, executable = self.make_runner(directory, process)
            runner.run(
                "rollover", executable=executable, runtime_identity="runtime:1", state=rollover_state()
            )
            self.assertFalse(
                runner.verify_observed_reset(int(NOW + 16_000), observed_at=NOW + 60)
            )
            self.assertEqual("effect_possible", runner.attempt_store.attempts()[-1].state)


def replace_state(state: ProviderViewState, **changes) -> ProviderViewState:
    from dataclasses import replace

    return replace(state, **changes)


if __name__ == "__main__":
    unittest.main()
