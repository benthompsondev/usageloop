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
            "id": "gpt-5.6-luna",
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
    def test_weekly_only_automatic_check_remains_read_only_and_calm(self):
        with tempfile.TemporaryDirectory() as directory:
            weekly_only = payload(18_000)
            weekly_only["rateLimitsByLimitId"]["codex"].pop("primary")
            client = FakeClient([weekly_only] * 4)
            clock = iter((100.0, 110.0, 120.0, 130.0))
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: next(clock), sleep=lambda _seconds: None,
            )
            result = runner.run("bootstrap", runtime_identity="runtime:1")
            self.assertEqual("NOT_ELIGIBLE", result.outcome)
            self.assertEqual("ABSENT", result.state.quota_state)
            self.assertEqual("valid_weekly_only", result.state.quota_evidence)
            self.assertEqual(4, client.read_calls)
            self.assertEqual(1, client.model_calls)  # read-only model/list for the trigger description
            self.assertEqual(0, client.thread_calls)
            self.assertEqual(0, client.turn_calls)

    def test_post_send_settles_before_first_sample_then_confirms_fixed_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [100.0]
            sleeps = []

            class DelayedAnchorClient(FakeClient):
                def __init__(self):
                    super().__init__([])

                def read_rate_limits(self):
                    self.read_calls += 1
                    # The Sep 12 first post-send sample still slid. Ten seconds
                    # later Codex reported one fixed reset for all four reads.
                    reset = int(clock[0] + 18_000) if clock[0] < 140 else 18_140
                    return payload(reset)

            def sleep(seconds):
                sleeps.append(seconds)
                clock[0] += seconds

            client = DelayedAnchorClient()
            history = SafeHistory(Path(directory) / "history.jsonl")
            runner = CodexOperationRunner(history,
                session_factory=lambda: FakeSession(client),
                clock=lambda: clock[0], sleep=sleep)
            result = runner.run("bootstrap", runtime_identity="runtime:1")
            self.assertEqual("ANCHOR_VERIFIED", result.outcome)
            self.assertEqual(1, client.turn_calls)
            self.assertEqual(8, client.read_calls)
            self.assertEqual([10.0] * 7, sleeps)
            self.assertEqual("verified", history.trigger_attempts()[-1].state)

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

    def test_exhausted_sync_is_conclusive_but_remains_eligible_for_later_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            exhausted = payload(1_000, used=100)
            exhausted["rateLimitsByLimitId"]["codex"]["primary"][
                "blockedReason"
            ] = "rate_limit_reached"
            times = iter((900.0, 910.0, 920.0, 930.0))
            client = FakeClient([exhausted] * 4)
            runner = CodexOperationRunner(
                SafeHistory(Path(directory) / "history.jsonl"),
                session_factory=lambda: FakeSession(client),
                clock=lambda: next(times),
                sleep=lambda _seconds: None,
            )

            result = runner.sync("runtime:1")

            self.assertEqual("SYNC_UPDATED", result.outcome)
            self.assertEqual("Waiting", result.state.status)
            self.assertEqual("EXHAUSTED", result.state.quota_state)
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
