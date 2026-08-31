from pathlib import Path
import json
import tempfile
import unittest

from sentinel.history import SafeHistory
from sentinel.provider_runtime import CodexOperationRunner


def payload(reset_at, *, used=0):
    return {
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": used,
                    "windowDurationMins": 300,
                    "resetsAt": reset_at,
                },
                "secondary": {
                    "usedPercent": 20,
                    "windowDurationMins": 10080,
                    "resetsAt": 900000,
                },
            }
        }
    }


class FakeClient:
    def __init__(self, payloads, *, model=True):
        self.payloads = list(payloads)
        self.model = model
        self.read_calls = 0
        self.turn_calls = 0
        self.thread_calls = 0
        self.model_calls = 0

    def read_rate_limits(self):
        value = self.payloads[min(self.read_calls, len(self.payloads) - 1)]
        self.read_calls += 1
        return value

    def drain_rate_limit_notifications(self):
        return []

    def list_models(self):
        self.model_calls += 1
        if not self.model:
            return []
        return [{
            "id": "current-model",
            "isDefault": True,
            "hidden": False,
            "upgrade": None,
            "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
            "defaultReasoningEffort": "low",
        }]

    def start_thread(self, params):
        self.thread_calls += 1
        return "thread-1"

    def start_turn(self, params):
        self.turn_calls += 1

    def await_turn_end(self, timeout):
        return "turn_completed"


class FakeSession:
    codex_version = "codex-cli test"

    def __init__(self, client):
        self.client = client
        self.closed = False

    def close(self):
        self.closed = True


class CodexOperationRunnerTests(unittest.TestCase):
    def test_manual_sync_reads_four_samples_without_model_or_turn_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            times = iter((100.0, 110.0, 120.0, 130.0))
            client = FakeClient([payload(18_000, used=12)] * 4)
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: next(times),
                sleep=lambda _seconds: None,
            )

            result = runner.sync("runtime:1")

            self.assertEqual("SYNC_UPDATED", result.outcome)
            self.assertEqual("Ready", result.state.status)
            self.assertEqual(12, result.state.used_percent)
            self.assertEqual(18_000, result.state.reset_at)
            self.assertEqual(20, result.state.weekly_used_percent)
            self.assertEqual(900_000, result.state.weekly_reset_at)
            self.assertEqual(4, client.read_calls)
            self.assertEqual(0, client.model_calls)
            self.assertEqual(0, client.thread_calls)
            self.assertEqual(0, client.turn_calls)
            self.assertFalse(result.request_possibly_sent)

    def test_manual_sync_reports_ambiguous_payload_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = {
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 0,
                            "windowDurationMins": 300,
                        },
                    }
                }
            }
            times = iter((100.0, 110.0, 120.0, 130.0))
            client = FakeClient([malformed] * 4)
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: next(times),
                sleep=lambda _seconds: None,
            )

            result = runner.sync("runtime:1")

            self.assertEqual("SYNC_INCONCLUSIVE", result.outcome)
            self.assertEqual("Needs attention", result.state.status)
            self.assertIsNone(result.state.last_verified_at)
            self.assertEqual(0, client.model_calls)
            self.assertEqual(0, client.turn_calls)

    def test_compatibility_probe_is_read_only_and_accepts_required_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([payload(18_000)] * 4)
            session = FakeSession(client)
            times = iter((100.0, 110.0, 120.0, 130.0))
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: session,
                clock=lambda: next(times),
                sleep=lambda seconds: None,
            )
            result = runner.probe("runtime:new")
            self.assertTrue(result.compatible)
            self.assertEqual(4, client.read_calls)
            self.assertEqual(0, client.thread_calls)
            self.assertEqual(0, client.turn_calls)
            self.assertTrue(session.closed)

            observations = runner.history.load_recent(now=140.0, limit=4)
            self.assertEqual(4, len(observations))
            records = [
                json.loads(line)
                for line in runner.history.path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(all(item["classification"] == "ANCHORED" for item in records))

    def test_missing_suitable_model_fails_compatibility_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient([payload(18_000)], model=False)
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: 100.0,
                sleep=lambda seconds: None,
            )
            self.assertFalse(runner.probe("runtime:new").compatible)

    def test_anchored_preflight_updates_card_without_submitting_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            times = iter((100.0, 110.0, 120.0, 130.0))
            client = FakeClient([payload(18_000)] * 4)
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: next(times),
                sleep=lambda seconds: None,
            )
            result = runner.run("bootstrap", runtime_identity="runtime:1")
            self.assertEqual("ALREADY_ANCHORED", result.outcome)
            self.assertEqual("Ready", result.state.status)
            self.assertEqual(18_000, result.state.reset_at)
            self.assertEqual(20, result.state.weekly_used_percent)
            self.assertEqual(900_000, result.state.weekly_reset_at)
            self.assertEqual(0, client.turn_calls)


if __name__ == "__main__":
    unittest.main()
