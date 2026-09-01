import copy
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from sentinel.protocol import (
    AppServerClient,
    AppServerProtocolError,
    AppServerRequestRejected,
    AuthenticationUnavailableError,
    merge_sparse_rate_limits,
)
from sentinel.transport import (
    AppServerUnavailableError,
    CodexNotFoundError,
    CodexProcessTransport,
    find_codex_executable,
)


FIXTURES = Path(__file__).parent / "fixtures"


class MemoryTransport:
    def __init__(self, incoming):
        self.incoming = [json.dumps(item) for item in incoming]
        self.sent = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def send_line(self, line):
        self.sent.append(json.loads(line))

    def read_line(self, timeout=None):
        if not self.incoming:
            raise AssertionError("protocol read exceeded fixture messages")
        return self.incoming.pop(0)

    def close(self):
        self.closed = True


def messages():
    return json.loads((FIXTURES / "protocol_messages.json").read_text(encoding="utf-8"))


class AppServerProtocolTests(unittest.TestCase):
    def test_initialization_uses_current_handshake_and_redacts_codex_home(self):
        fixture = messages()
        transport = MemoryTransport([fixture["initialize_result"]])
        client = AppServerClient(transport, client_version="0.1.0")

        server = client.initialize()

        self.assertTrue(transport.started)
        self.assertEqual("windows", server.platform_os)
        self.assertFalse(hasattr(server, "codex_home"))
        self.assertEqual("initialize", transport.sent[0]["method"])
        self.assertEqual("codex-window-sentinel", transport.sent[0]["params"]["clientInfo"]["name"])
        self.assertFalse(transport.sent[0]["params"]["capabilities"]["experimentalApi"])
        self.assertEqual({"method": "initialized"}, transport.sent[1])

    def test_same_handshake_accepts_linux_app_server_identity(self):
        fixture = copy.deepcopy(messages()["initialize_result"])
        fixture["result"]["platformFamily"] = "unix"
        fixture["result"]["platformOs"] = "linux"
        client = AppServerClient(MemoryTransport([fixture]), client_version="1.1.0-beta.1")

        server = client.initialize()

        self.assertEqual("linux", server.platform_os)

    def test_rate_read_correlates_response_and_keeps_sparse_notification(self):
        fixture = messages()
        transport = MemoryTransport(
            [fixture["initialize_result"], fixture["rate_notification"], fixture["rate_response"]]
        )
        client = AppServerClient(transport, client_version="0.1.0")
        client.initialize()

        result = client.read_rate_limits()

        self.assertEqual(12, result["rateLimits"]["primary"]["usedPercent"])
        self.assertEqual("account/rateLimits/read", transport.sent[-1]["method"])
        self.assertIsNone(transport.sent[-1]["params"])
        self.assertEqual(1, len(client.drain_rate_limit_notifications()))

    def test_rate_read_accepts_multi_bucket_result_without_legacy_bucket(self):
        fixture = messages()
        response = {
            "id": 2,
            "result": {
                "rateLimitsByLimitId": {
                    "codex": fixture["rate_response"]["result"]["rateLimits"]
                }
            },
        }
        transport = MemoryTransport([fixture["initialize_result"], response])
        client = AppServerClient(transport, client_version="0.1.0")
        client.initialize()
        try:
            result = client.read_rate_limits()
        except AppServerProtocolError as exc:
            self.fail(f"valid multi-bucket response was rejected: {exc.category}")
        self.assertEqual(12, result["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"])

    def test_authentication_error_is_sanitized_and_categorized(self):
        fixture = messages()
        transport = MemoryTransport([fixture["initialize_result"], fixture["auth_error"]])
        client = AppServerClient(transport, client_version="0.1.0")
        client.initialize()

        with self.assertRaises(AuthenticationUnavailableError) as caught:
            client.read_rate_limits()

        self.assertEqual("authentication_unavailable", caught.exception.category)
        self.assertNotIn("chatgpt", str(caught.exception).lower())

    def test_protocol_emits_only_observation_safe_methods(self):
        fixture = messages()
        transport = MemoryTransport([fixture["initialize_result"], fixture["rate_response"]])
        client = AppServerClient(transport, client_version="0.1.0")
        client.initialize()
        client.read_rate_limits()
        self.assertEqual(
            ["initialize", "initialized", "account/rateLimits/read"],
            [message["method"] for message in transport.sent],
        )

    def test_sparse_notification_merge_preserves_missing_weekly_window(self):
        fixture = messages()
        current = copy.deepcopy(fixture["rate_response"]["result"])
        merged = merge_sparse_rate_limits(current, fixture["rate_notification"]["params"])
        self.assertEqual(13, merged["rateLimits"]["primary"]["usedPercent"])
        self.assertEqual(10080, merged["rateLimits"]["secondary"]["windowDurationMins"])


class ExecutableResolutionTests(unittest.TestCase):
    def test_missing_codex_executable_reports_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CodexNotFoundError) as caught:
                find_codex_executable(path_value=directory, known_candidates=())
        self.assertEqual("codex_not_found", caught.exception.category)

    @unittest.skipIf(os.name == "nt", "Linux PATH precedence")
    def test_linux_prefers_path_without_guessing_an_install_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_codex = root / "path" / "codex"
            alternate = root / "alternate" / "codex"
            path_codex.parent.mkdir()
            alternate.parent.mkdir()
            path_codex.touch()
            alternate.touch()

            found = find_codex_executable(
                path_value=str(path_codex.parent), known_candidates=(alternate,)
            )

        self.assertEqual(path_codex.resolve(), found)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows executable naming")
    def test_native_executable_is_preferred_over_command_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "codex.cmd").touch()
            (root / "codex.exe").touch()
            found = find_codex_executable(path_value=directory, known_candidates=())
        self.assertEqual("codex.exe", found.name)

    @unittest.skipUnless(__import__("os").name == "nt", "Windows process fixture")
    def test_app_server_process_exit_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            command = Path(directory) / "codex.cmd"
            command.write_text("@exit /b 1\n", encoding="utf-8")
            transport = CodexProcessTransport(command)
            transport.start()
            with self.assertRaises(AppServerUnavailableError) as caught:
                transport.read_line(timeout=5)
            transport.close()
        self.assertEqual("app_server_unavailable", caught.exception.category)




class TurnProtocolTests(unittest.TestCase):
    def client(self, incoming):
        fixture = messages()
        transport = MemoryTransport([fixture["initialize_result"], *incoming])
        client = AppServerClient(transport, client_version="0.1.0")
        client.initialize()
        return client, transport

    def test_thread_start_accepts_current_and_nested_identifier_shapes(self):
        for result in ({"threadId": "t-1"}, {"thread": {"id": "t-1"}}, {"id": "t-1"}):
            with self.subTest(result=result):
                client, _ = self.client([{"id": 2, "result": result}])
                self.assertEqual("t-1", client.start_thread({"ephemeral": True}))

    def test_pre_dispatch_rejection_is_distinguished_from_other_errors(self):
        client, _ = self.client([{"id": 2, "error": {"code": -32600, "message": "requires experimentalApi capability"}}])
        with self.assertRaises(AppServerRequestRejected) as caught:
            client.start_thread({"allowProviderModelFallback": True})
        self.assertTrue(caught.exception.rejected_before_dispatch)

    def test_server_side_failure_is_not_treated_as_pre_dispatch(self):
        client, _ = self.client([{"id": 2, "error": {"code": -32000, "message": "upstream failure"}}])
        with self.assertRaises(AppServerRequestRejected) as caught:
            client.start_turn({"threadId": "t-1", "input": []})
        self.assertFalse(caught.exception.rejected_before_dispatch)

    def test_model_list_returns_only_object_entries(self):
        client, _ = self.client([{"id": 2, "result": {"data": [{"id": "m"}, "junk", 5]}}])
        self.assertEqual([{"id": "m"}], client.list_models())

    def test_await_turn_end_reports_completion_and_keeps_rate_notifications(self):
        fixture = messages()
        client, _ = self.client([
            fixture["rate_notification"],
            {"method": "turn/completed", "params": {"threadId": "t-1"}},
        ])
        self.assertEqual("turn_completed", client.await_turn_end(timeout=5))
        self.assertEqual(1, len(client.drain_rate_limit_notifications()))

    def test_completion_arriving_before_turn_start_response_is_not_lost(self):
        client, _ = self.client([
            {"method": "turn/completed", "params": {"threadId": "t-1"}},
            {"id": 2, "result": {"turn": {"id": "turn-1"}}},
        ])
        client.start_turn({"threadId": "t-1", "input": []})
        self.assertEqual("turn_completed", client.await_turn_end(timeout=5))

    def test_await_turn_end_reports_error_notification(self):
        client, _ = self.client([{"method": "error", "params": {"message": "boom"}}])
        self.assertEqual("turn_error", client.await_turn_end(timeout=5))

    def test_await_turn_end_reports_timeout_without_raising(self):
        client, _ = self.client([])
        self.assertEqual("turn_timeout", client.await_turn_end(timeout=0))

    def test_turn_methods_are_gated_behind_the_handshake(self):
        transport = MemoryTransport([])
        client = AppServerClient(transport, client_version="0.1.0")
        with self.assertRaises(Exception):
            client.start_turn({"threadId": "t-1", "input": []})
        self.assertEqual([], transport.sent)


class NativePreferenceTests(unittest.TestCase):
    def test_installed_native_binary_wins_over_a_stale_path_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path_dir = root / "npm"
            path_dir.mkdir()
            shim = path_dir / ("codex.cmd" if __import__("os").name == "nt" else "codex")
            shim.touch()
            native = root / "native" / ("codex.exe" if __import__("os").name == "nt" else "codex")
            native.parent.mkdir()
            native.touch()
            found = find_codex_executable(path_value=str(path_dir), known_candidates=(native,))
        self.assertEqual(native.resolve(), found)

    def test_path_is_still_used_when_no_native_candidate_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "codex.exe" if __import__("os").name == "nt" else "codex"
            (root / name).touch()
            found = find_codex_executable(path_value=str(root), known_candidates=())
        self.assertEqual(name, found.name)

    def test_disappearing_native_candidate_does_not_hide_remaining_install(self):
        with tempfile.TemporaryDirectory() as directory:
            local_app_data = Path(directory)
            bin_root = local_app_data / "OpenAI" / "Codex" / "bin"
            stale = bin_root / "stale" / "codex.exe"
            current = bin_root / "current" / "codex.exe"
            stale.parent.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            stale.touch()
            current.touch()
            original_stat = Path.stat

            def replacement_race(path, *args, **kwargs):
                if path == stale:
                    raise FileNotFoundError("simulated installer replacement")
                return original_stat(path, *args, **kwargs)

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                with mock.patch.object(Path, "stat", replacement_race):
                    try:
                        found = find_codex_executable(path_value="")
                    except OSError as exc:
                        self.fail(f"one stale candidate aborted discovery: {type(exc).__name__}")

        self.assertEqual(current.resolve(), found)


if __name__ == "__main__":
    unittest.main()
