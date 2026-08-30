import tempfile
import unittest
from pathlib import Path

from sentinel.models import ModelChoice, select_trigger_model
from sentinel.protocol import AppServerProtocolError, AppServerRequestRejected
from sentinel.trigger import AppServerTrigger, TriggerConfig


def catalog_entry(identifier, *, default=False, hidden=False, upgrade=None, efforts=("low", "medium"), default_effort="low"):
    return {
        "id": identifier,
        "isDefault": default,
        "hidden": hidden,
        "upgrade": upgrade,
        "defaultReasoningEffort": default_effort,
        "supportedReasoningEfforts": [{"reasoningEffort": value} for value in efforts],
    }


#: Shape captured from the installed runtime during the live experiment.
LIVE_CATALOG = [
    catalog_entry("gpt-5.6-sol", default=True, efforts=("low", "medium", "high"), default_effort="low"),
    catalog_entry("gpt-5.6-terra", default_effort="medium"),
    catalog_entry("gpt-5.6-luna", default_effort="medium"),
    catalog_entry("gpt-5.4", upgrade="gpt-5.6-terra"),
    catalog_entry("gpt-5.4-mini", upgrade="gpt-5.6-luna"),
]


class FakeClient:
    def __init__(self, catalog=None, *, thread_id="thread-1", thread_error=None,
                 turn_error=None, turn_outcome="turn_completed", models_error=None):
        self.catalog = LIVE_CATALOG if catalog is None else catalog
        self.thread_id = thread_id
        self.thread_error = thread_error
        self.turn_error = turn_error
        self.turn_outcome = turn_outcome
        self.models_error = models_error
        self.thread_params = None
        self.turn_params = None
        self.turn_calls = 0
        self.model_list_calls = 0

    def list_models(self):
        self.model_list_calls += 1
        if self.models_error is not None:
            raise self.models_error
        return self.catalog

    def start_thread(self, params):
        self.thread_params = params
        if self.thread_error is not None:
            raise self.thread_error
        return self.thread_id

    def start_turn(self, params):
        self.turn_calls += 1
        self.turn_params = params
        if self.turn_error is not None:
            raise self.turn_error

    def await_turn_end(self, *, timeout):
        if isinstance(self.turn_outcome, Exception):
            raise self.turn_outcome
        return self.turn_outcome


def rejected(code):
    return AppServerRequestRejected("turn/start", {"code": code, "message": "rejected"})


class ModelSelectionTests(unittest.TestCase):
    def test_selects_visible_default_model_and_its_advertised_effort(self):
        choice = select_trigger_model(LIVE_CATALOG)
        self.assertEqual(ModelChoice("gpt-5.6-sol", "low", True), choice)

    def test_excludes_superseded_models_carrying_an_upgrade_pointer(self):
        catalog = [
            catalog_entry("gpt-5.4-mini", default=True, upgrade="gpt-5.6-luna"),
            catalog_entry("gpt-5.6-luna"),
        ]
        choice = select_trigger_model(catalog)
        self.assertIsNotNone(choice)
        self.assertEqual("gpt-5.6-luna", choice.model)

    def test_excludes_hidden_models(self):
        catalog = [catalog_entry("hidden-default", default=True, hidden=True), catalog_entry("visible")]
        self.assertEqual("visible", select_trigger_model(catalog).model)

    def test_falls_back_to_first_usable_model_when_none_is_marked_default(self):
        catalog = [catalog_entry("first"), catalog_entry("second")]
        choice = select_trigger_model(catalog)
        self.assertEqual("first", choice.model)
        self.assertFalse(choice.is_default)

    def test_returns_none_when_every_model_is_superseded_or_hidden(self):
        catalog = [
            catalog_entry("old", default=True, upgrade="new"),
            catalog_entry("secret", hidden=True),
        ]
        self.assertIsNone(select_trigger_model(catalog))

    def test_effort_falls_back_when_default_effort_is_not_advertised(self):
        catalog = [catalog_entry("m", default=True, efforts=("medium",), default_effort="low")]
        self.assertEqual("medium", select_trigger_model(catalog).reasoning_effort)

    def test_effort_is_none_when_runtime_advertises_none(self):
        catalog = [catalog_entry("m", default=True, efforts=(), default_effort=None)]
        self.assertIsNone(select_trigger_model(catalog).reasoning_effort)

    def test_ignores_malformed_catalog_entries(self):
        catalog = ["nonsense", {"id": 5}, {"id": "bad name!"}, catalog_entry("good")]
        self.assertEqual("good", select_trigger_model(catalog).model)


class TriggerParameterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name) / "trigger-workspace"

    def trigger(self, client, config=None):
        return AppServerTrigger(client, self.workspace, config or TriggerConfig())

    def test_thread_parameters_are_bounded_and_ephemeral(self):
        client = FakeClient()
        trigger = self.trigger(client)
        trigger.run()
        self.assertEqual(
            {
                "ephemeral": True,
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "cwd": str(self.workspace),
                "config": {"mcp_servers": {}},
                "model": "gpt-5.6-sol",
            },
            client.thread_params,
        )

    def test_thread_parameters_omit_capability_gated_fields(self):
        client = FakeClient()
        self.trigger(client).run()
        self.assertNotIn("allowProviderModelFallback", client.thread_params)

    def test_turn_sends_one_minimal_text_input_with_attribution(self):
        client = FakeClient()
        self.trigger(client).run()
        self.assertEqual("thread-1", client.turn_params["threadId"])
        self.assertEqual([{"type": "text", "text": "ok"}], client.turn_params["input"])
        self.assertEqual("low", client.turn_params["effort"])
        self.assertEqual("codex-window-sentinel", client.turn_params["turnTrigger"])

    def test_turn_omits_effort_when_runtime_advertises_none(self):
        client = FakeClient(catalog=[catalog_entry("m", default=True, efforts=(), default_effort=None)])
        self.trigger(client).run()
        self.assertNotIn("effort", client.turn_params)

    def test_description_reports_resolved_model_not_a_persisted_name(self):
        client = FakeClient()
        description = self.trigger(client).describe()
        self.assertEqual("app_server_turn", description.mechanism)
        self.assertEqual("gpt-5.6-sol", description.model)
        self.assertEqual("low", description.reasoning_effort)

    def test_description_never_leaks_prompt_contents(self):
        client = FakeClient()
        description = self.trigger(client, TriggerConfig(prompt="private trigger text")).describe()
        self.assertEqual(len("private trigger text"), description.prompt_characters)
        self.assertNotIn("private", repr(description))

    def test_model_is_resolved_once_per_trigger(self):
        client = FakeClient()
        trigger = self.trigger(client)
        trigger.describe()
        trigger.run()
        self.assertEqual(1, client.model_list_calls)


class TriggerOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name) / "trigger-workspace"

    def trigger(self, client):
        return AppServerTrigger(client, self.workspace, TriggerConfig())

    def test_exactly_one_turn_is_submitted_on_success(self):
        client = FakeClient()
        result = self.trigger(client).run()
        self.assertEqual(1, client.turn_calls)
        self.assertEqual("turn_completed", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)

    def test_unusable_catalog_refuses_to_trigger(self):
        client = FakeClient(catalog=[catalog_entry("old", default=True, upgrade="new")])
        result = self.trigger(client).run()
        self.assertEqual(("model_unavailable", False), (result.terminal_outcome, result.request_possibly_sent))
        self.assertEqual(0, client.turn_calls)

    def test_model_list_failure_refuses_to_trigger(self):
        client = FakeClient(models_error=AppServerProtocolError("boom", "no models"))
        result = self.trigger(client).run()
        self.assertEqual(("model_unavailable", False), (result.terminal_outcome, result.request_possibly_sent))
        self.assertEqual(0, client.turn_calls)

    def test_thread_rejection_leaves_the_opportunity_recoverable(self):
        client = FakeClient(thread_error=AppServerRequestRejected("thread/start", {"code": -32600}))
        result = self.trigger(client).run()
        self.assertEqual(("thread_start_rejected", False), (result.terminal_outcome, result.request_possibly_sent))
        self.assertEqual(0, client.turn_calls)

    def test_pre_dispatch_turn_rejection_is_not_possibly_sent(self):
        for code in (-32600, -32601, -32602):
            with self.subTest(code=code):
                client = FakeClient(turn_error=rejected(code))
                result = self.trigger(client).run()
                self.assertEqual("turn_start_rejected", result.terminal_outcome)
                self.assertFalse(result.request_possibly_sent)

    def test_other_turn_errors_are_treated_as_possibly_sent(self):
        client = FakeClient(turn_error=rejected(-32000))
        result = self.trigger(client).run()
        self.assertEqual("turn_start_error", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)

    def test_transport_failure_after_submission_is_possibly_sent(self):
        client = FakeClient(turn_error=OSError("pipe closed"))
        result = self.trigger(client).run()
        self.assertEqual("turn_start_unconfirmed", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)

    def test_turn_timeout_is_reported_but_still_possibly_sent(self):
        client = FakeClient(turn_outcome="turn_timeout")
        result = self.trigger(client).run()
        self.assertEqual("turn_timeout", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)

    def test_turn_error_notification_is_reported_but_still_possibly_sent(self):
        client = FakeClient(turn_outcome="turn_error")
        result = self.trigger(client).run()
        self.assertEqual("turn_error", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)

    def test_lifecycle_stream_failure_is_still_possibly_sent(self):
        client = FakeClient(turn_outcome=OSError("stream gone"))
        result = self.trigger(client).run()
        self.assertEqual("turn_stream_unavailable", result.terminal_outcome)
        self.assertTrue(result.request_possibly_sent)


if __name__ == "__main__":
    unittest.main()
