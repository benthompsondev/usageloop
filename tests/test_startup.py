import unittest

from sentinel.startup import StartupManager


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
        manager = StartupManager("C:/Apps/WindowSentinel.exe", registry=registry)
        manager.set_enabled(True)
        self.assertTrue(manager.is_enabled())
        self.assertEqual('"C:/Apps/WindowSentinel.exe" --background', registry.values["Window Sentinel"])
        manager.set_enabled(False)
        self.assertFalse(manager.is_enabled())


if __name__ == "__main__":
    unittest.main()
