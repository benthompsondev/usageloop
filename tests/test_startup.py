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

        reconcile_startup_preference(AppSettings(start_with_windows=True).start_with_windows, manager)

        self.assertEqual(manager.command, registry.values["UsageLoop"])

    def test_upgrade_removes_stale_startup_when_saved_preference_is_off(self):
        registry = FakeRegistry()
        manager = StartupManager(r"C:\\Program Files\\UsageLoop\\UsageLoop.exe", registry=registry)
        registry.values["UsageLoop"] = r'"C:\\Old UsageLoop\\UsageLoop.exe" --background'

        reconcile_startup_preference(False, manager)

        self.assertNotIn("UsageLoop", registry.values)


if __name__ == "__main__":
    unittest.main()
