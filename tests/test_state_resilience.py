"""Regression tests for corrupt local state, folder migration, and the OFF switch.

Each case here reproduces something that was actually observed or is required by
the product's safety promise, not a hypothetical.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sentinel.app_state import (
    AppSettings,
    AppStateStore,
    ProviderViewState,
    app_data_root,
    automation_decision,
    format_countdown,
)
from sentinel.app_controller import ApplicationController
from sentinel.history import SafeHistory
from sentinel.product import PRODUCT


VALID = {
    "provider_id": "codex",
    "display_name": "Codex",
    "installed": True,
    "automation_supported": True,
    "status": "Ready",
    "detail": "ready",
}


def store_with(providers: dict) -> AppStateStore:
    directory = tempfile.mkdtemp()
    path = Path(directory) / "app-state.json"
    path.write_text(
        json.dumps({"schema_version": 1, "settings": {}, "providers": providers}),
        encoding="utf-8",
    )
    return AppStateStore(path)


class CorruptProviderCacheTests(unittest.TestCase):
    """A hand-edited or half-written state file used to crash the clock tick."""

    def test_wrong_types_are_dropped_instead_of_reaching_the_clock(self) -> None:
        store = store_with(
            {
                "codex": {
                    **VALID,
                    "reset_at": "not-an-int",
                    "used_percent": "also-wrong",
                    "automation_blocked_until": "nope",
                }
            }
        )
        state = store.load_provider_cache()["codex"]
        self.assertIsNone(state.reset_at)
        self.assertIsNone(state.used_percent)
        self.assertIsNone(state.automation_blocked_until)
        # Both of these raised TypeError before the fields were normalized.
        self.assertEqual("Not verified yet", format_countdown(state.reset_at, 1_788_000_000.0))
        self.assertEqual(
            "BOOTSTRAP", automation_decision(True, state, now=1_788_000_000.0).action
        )

    def test_non_finite_numbers_are_rejected(self) -> None:
        for bad in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(bad=bad):
                directory = tempfile.mkdtemp()
                path = Path(directory) / "app-state.json"
                # json.dumps writes these as the Infinity/NaN literals that
                # Python's own decoder accepts, which is how they could arrive.
                path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "settings": {},
                            "providers": {"codex": {**VALID, "reset_at": bad}},
                        }
                    ),
                    encoding="utf-8",
                )
                state = AppStateStore(path).load_provider_cache()["codex"]
                self.assertIsNone(state.reset_at)

    def test_valid_numbers_still_load(self) -> None:
        store = store_with({"codex": {**VALID, "reset_at": 1_788_018_000, "used_percent": 12.5}})
        state = store.load_provider_cache()["codex"]
        self.assertEqual(1_788_018_000, state.reset_at)
        self.assertEqual(12.5, state.used_percent)

    def test_missing_required_text_drops_the_record(self) -> None:
        store = store_with({"codex": {**VALID, "status": 5}})
        self.assertEqual({}, store.load_provider_cache())

    def test_unknown_field_drops_the_record_rather_than_guessing(self) -> None:
        store = store_with({"codex": {**VALID, "bogus": 1}})
        self.assertEqual({}, store.load_provider_cache())

    def test_booleans_are_not_accepted_as_timestamps(self) -> None:
        store = store_with({"codex": {**VALID, "reset_at": True}})
        self.assertIsNone(store.load_provider_cache()["codex"].reset_at)

    def test_unreadable_file_yields_empty_state(self) -> None:
        directory = tempfile.mkdtemp()
        path = Path(directory) / "app-state.json"
        path.write_text("{not json at all", encoding="utf-8")
        store = AppStateStore(path)
        self.assertEqual({}, store.load_provider_cache())
        self.assertEqual(AppSettings(), store.load())


class AppDataMigrationTests(unittest.TestCase):
    """The state folder carries the one-shot guards, so a rename must not lose it."""

    def setUp(self) -> None:
        platform_patch = mock.patch("sentinel.app_state.sys.platform", "win32")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_legacy_folder_is_migrated_when_the_new_name_is_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / PRODUCT.legacy_app_data_folder
            legacy.mkdir()
            (legacy / "history.jsonl").write_text("guard", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                root = app_data_root()
            self.assertEqual(Path(directory) / PRODUCT.app_data_folder, root)
            self.assertEqual("guard", (root / "history.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(legacy.exists())

    def test_existing_new_folder_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / PRODUCT.legacy_app_data_folder
            legacy.mkdir()
            (legacy / "marker").write_text("old", encoding="utf-8")
            current = Path(directory) / PRODUCT.app_data_folder
            current.mkdir()
            (current / "marker").write_text("new", encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                root = app_data_root()
            self.assertEqual(current, root)
            self.assertEqual("new", (root / "marker").read_text(encoding="utf-8"))
            self.assertTrue(legacy.exists())

    def test_failed_migration_keeps_using_the_legacy_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / PRODUCT.legacy_app_data_folder
            legacy.mkdir()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                with mock.patch.object(Path, "rename", side_effect=OSError("locked")):
                    root = app_data_root()
            # Falling back preserves the guards rather than silently starting fresh.
            self.assertEqual(legacy, root)

    def test_no_legacy_folder_uses_the_new_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                root = app_data_root()
            self.assertEqual(Path(directory) / PRODUCT.app_data_folder, root)

    def test_default_state_and_history_share_the_canonical_packaged_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / PRODUCT.app_data_folder
            current.mkdir()
            legacy = Path(directory) / PRODUCT.legacy_app_data_folder
            legacy.mkdir()
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": directory}):
                store = AppStateStore()
                history = SafeHistory()

            self.assertEqual(current / "app-state.json", store.path)
            self.assertEqual(current / "sentinel.jsonl", history.path)


class RecordingProvider:
    """Fails loudly if anything reaches the provider while automation is off."""

    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.detect_calls = 0
        self.probe_calls = 0
        self.action_calls: list[str] = []

    def detect(self) -> ProviderViewState:
        self.detect_calls += 1
        return ProviderViewState.waiting(self.provider_id, self.provider_id.title(), installed=True)

    def probe(self):  # pragma: no cover - reaching this is the failure
        self.probe_calls += 1
        raise AssertionError("probe contacted a provider while automation was off")

    def run_action(self, mode, *, current_state=None):  # pragma: no cover
        self.action_calls.append(mode)
        raise AssertionError("run_action contacted a provider while automation was off")


class AutomationOffTests(unittest.TestCase):
    def test_off_produces_no_provider_triggering_decision(self) -> None:
        providers = [RecordingProvider("codex")]
        controller = ApplicationController(providers, store_with({}))
        controller.start()
        controller.set_automation_enabled(False)
        controller.refresh_local_states()
        decisions = controller.decisions(now=1_788_000_000.0)
        self.assertEqual({"NONE"}, {decision.action for decision in decisions.values()})
        for provider in providers:
            self.assertEqual(0, provider.probe_calls)
            self.assertEqual([], provider.action_calls)
            # Local detection is filesystem-only and stays allowed.
            self.assertGreater(provider.detect_calls, 0)

    def test_off_is_the_default_for_a_brand_new_install(self) -> None:
        controller = ApplicationController([RecordingProvider("codex")], store_with({}))
        controller.start()
        self.assertFalse(controller.settings.automation_enabled)
        self.assertFalse(controller.settings.start_with_windows)
        self.assertEqual(
            "NONE", controller.decisions(now=1_788_000_000.0)["codex"].action
        )


if __name__ == "__main__":
    unittest.main()
