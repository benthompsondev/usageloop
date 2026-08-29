import copy
import json
import tempfile
import unittest
from pathlib import Path

from sentinel.protocol import (
    AppServerClient,
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


if __name__ == "__main__":
    unittest.main()
