"""Cross-filesystem adoption keeps compatibility and one-shot history intact."""

import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sentinel.app_state import AppStateStore, posix_app_data_root
from sentinel.history import SafeHistory


class XdgMigrationTests(unittest.TestCase):
    def seed(self, data_home):
        legacy = data_home / "UsageLoop"
        legacy.mkdir()
        (legacy / "app-state.json").write_text(json.dumps({
            "settings": {"automation_enabled": True,
                         "checked_runtime_identities": {"codex": "runtime:new"},
                         "compatible_runtime_identities": {"codex": "runtime:old"}},
        }))
        history = SafeHistory(legacy / "sentinel.jsonl")
        history.reserve_trigger(mode="rollover", idempotency_key="rollover:123", boundary_reset_at=123, model="gpt-5.6-luna", reasoning_effort="low", now=124)
        return legacy

    def migrate(self, data_home, state_home):
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home), "XDG_STATE_HOME": str(state_home)}):
            return posix_app_data_root()

    def test_real_cross_filesystem_adoption_preserves_guards(self):
        if not Path('/dev/shm').is_dir():
            self.skipTest('Requires a second writable filesystem')
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory(dir='/dev/shm') as state:
            data, state = Path(data), Path(state)
            if data.stat().st_dev == state.stat().st_dev:
                self.skipTest('Requires distinct filesystems')
            legacy = self.seed(data)
            before = {p.name: p.read_bytes() for p in legacy.iterdir()}
            root = self.migrate(data, state)
            self.assertEqual(state / 'usageloop', root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            self.assertEqual(before, {p.name: p.read_bytes() for p in legacy.iterdir()})
            settings = AppStateStore(root / 'app-state.json').load()
            self.assertEqual('runtime:new', settings.checked_runtime_identities['codex'])
            self.assertEqual('runtime:old', settings.compatible_runtime_identities['codex'])
            self.assertEqual(1, len(SafeHistory(root / 'sentinel.jsonl').trigger_attempts()))
            (root / 'new-marker').write_text('authoritative')
            self.assertEqual(root, self.migrate(data, state))
            self.assertTrue((root / 'new-marker').exists())

    def test_failed_copy_never_publishes_partial_state(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            data, state = Path(data), Path(state)
            legacy = self.seed(data)
            def broken_copy(source, target, **kwargs):
                target.mkdir()
                (target / 'app-state.json').write_text('{}')
                raise OSError('disk full')
            with patch.object(Path, 'rename', side_effect=OSError(errno.EXDEV, 'cross-device')), patch('sentinel.app_state.shutil.copytree', side_effect=broken_copy):
                self.assertEqual(legacy, self.migrate(data, state))
            self.assertFalse((state / 'usageloop').exists())
            self.assertEqual([], list(state.iterdir()))
            self.assertEqual(1, len(SafeHistory(legacy / 'sentinel.jsonl').trigger_attempts()))

    def test_changed_source_is_not_adopted(self):
        import shutil
        copytree = shutil.copytree
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as state:
            data, state = Path(data), Path(state)
            legacy = self.seed(data)
            def changed_copy(source, target, **kwargs):
                copytree(source, target, **kwargs)
                (source / 'new-guard').write_text('reserved')
            with patch.object(Path, 'rename', side_effect=OSError(errno.EXDEV, 'cross-device')), patch('sentinel.app_state.shutil.copytree', side_effect=changed_copy):
                self.assertEqual(legacy, self.migrate(data, state))
            self.assertFalse((state / 'usageloop').exists())
            self.assertTrue((legacy / 'new-guard').exists())
