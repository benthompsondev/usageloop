"""Per-user Windows startup registration."""

from __future__ import annotations

import winreg

from .product import PRODUCT


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = PRODUCT.display_name


class StartupManager:
    def __init__(self, executable: str, *, registry=winreg):
        self.executable = executable
        self.registry = registry

    @property
    def command(self) -> str:
        return f'"{self.executable}" --background'

    def is_enabled(self) -> bool:
        try:
            key = self.registry.OpenKey(
                self.registry.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                self.registry.KEY_READ,
            )
            try:
                value, _kind = self.registry.QueryValueEx(key, _VALUE_NAME)
            finally:
                self.registry.CloseKey(key)
        except (FileNotFoundError, OSError):
            return False
        return value == self.command

    def set_enabled(self, enabled: bool) -> None:
        key = self.registry.CreateKeyEx(
            self.registry.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            self.registry.KEY_SET_VALUE,
        )
        try:
            if enabled:
                self.registry.SetValueEx(
                    key,
                    _VALUE_NAME,
                    0,
                    self.registry.REG_SZ,
                    self.command,
                )
            else:
                try:
                    self.registry.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        finally:
            self.registry.CloseKey(key)
