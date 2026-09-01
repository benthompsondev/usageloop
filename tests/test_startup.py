from pathlib import Path
import tempfile
import unittest

from sentinel.product import PRODUCT
from sentinel.app_state import AppSettings
from sentinel.startup import StartupManager, reconcile_startup_preference


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def CreateKeyEx(self, root, path, reserved, access):
        return self

    def OpenKey(self, root, path, reserved, access):
        return self

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], self.REG_SZ

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]

    def CloseKey(self, key):
        pass


class StartupManagerTests(unittest.TestCase):
    def test_startup_registration_is_per_user_and_reversible(self):
        registry = FakeRegistry()
        manager = StartupManager("C:/Apps/UsageLoop.exe", registry=registry)
        manager.set_enabled(True)
        self.assertTrue(manager.is_enabled())
        self.assertEqual('"C:/Apps/UsageLoop.exe" --background', registry.values[PRODUCT.display_name])
        manager.set_enabled(False)
        self.assertFalse(manager.is_enabled())

    def test_upgrade_rewrites_enabled_startup_to_the_current_executable(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\\Program Files\\UsageLoop\\UsageLoop.exe", registry=registry)
        registry.values["UsageLoop"] = r'"C:\\Old UsageLoop\\UsageLoop.exe" --background'

        normalized = reconcile_startup_preference(
            AppSettings(start_with_windows=True).start_with_windows, manager
        )

        self.assertTrue(normalized)
        self.assertEqual(manager.command, registry.values["UsageLoop"])

    def test_upgrade_removes_stale_startup_when_saved_preference_is_off(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\\Program Files\\UsageLoop\\UsageLoop.exe", registry=registry)
        registry.values["UsageLoop"] = r'"C:\\Old UsageLoop\\UsageLoop.exe" --background'

        normalized = reconcile_startup_preference(False, manager)

        self.assertFalse(normalized)
        self.assertNotIn("UsageLoop", registry.values)

    def test_upgrade_adopts_an_exact_current_registration_when_saved_off(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "UsageLoop.exe"
            executable.touch()
            registry = FakeRegistry()
            manager = StartupManager(str(executable), registry=registry)
            registry.values["UsageLoop"] = manager.command

            normalized = reconcile_startup_preference(False, manager)

            self.assertTrue(normalized)
            self.assertEqual(manager.command, registry.values["UsageLoop"])

    def test_saved_off_without_a_registration_remains_off(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\Apps\UsageLoop.exe", registry=registry)

        normalized = reconcile_startup_preference(False, manager)

        self.assertFalse(normalized)
        self.assertNotIn("UsageLoop", registry.values)

    def test_saved_off_does_not_adopt_a_missing_current_target(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\Missing\UsageLoop.exe", registry=registry)
        registry.values["UsageLoop"] = manager.command

        normalized = reconcile_startup_preference(False, manager)

        self.assertFalse(normalized)
        self.assertNotIn("UsageLoop", registry.values)

    def test_saved_on_with_valid_registration_remains_on(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\Apps\UsageLoop.exe", registry=registry)
        registry.values["UsageLoop"] = manager.command

        normalized = reconcile_startup_preference(True, manager)

        self.assertTrue(normalized)
        self.assertEqual(manager.command, registry.values["UsageLoop"])

    def test_saved_on_repairs_a_missing_registration(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\Apps\UsageLoop.exe", registry=registry)

        normalized = reconcile_startup_preference(True, manager)

        self.assertTrue(normalized)
        self.assertEqual(manager.command, registry.values["UsageLoop"])


if __name__ == "__main__":
    unittest.main()
