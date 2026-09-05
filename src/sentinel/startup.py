"""Per-user startup registration for the current desktop session."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from .host import is_linux, xdg_config_home
from .product import PRODUCT


if os.name == "nt":
    import winreg as _winreg
else:  # The registry module does not exist off Windows.
    _winreg = None


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = PRODUCT.display_name
_AUTOSTART_FILENAME = "usageloop.desktop"


class StartupRegistration(Protocol):
    """The narrow contract the Settings toggle and reconciler depend on."""

    def is_enabled(self) -> bool: ...
    def has_registration(self) -> bool: ...
    def has_valid_current_registration(self) -> bool: ...
    def set_enabled(self, enabled: bool) -> None: ...


class WindowsStartupManager:
    def __init__(self, executable: str, *, registry=None):
        self.executable = executable
        self.registry = _winreg if registry is None else registry
        if self.registry is None:
            raise OSError("Windows startup registration is unavailable here.")

    @property
    def command(self) -> str:
        return f'"{self.executable}" --background'

    def is_enabled(self) -> bool:
        value = self._registered_command()
        return value == self.command

    def has_registration(self) -> bool:
        return self._registered_command() is not None

    def has_valid_current_registration(self) -> bool:
        return self.is_enabled() and Path(self.executable).is_file()

    def _registered_command(self) -> str | None:
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
            return None
        return value if isinstance(value, str) else None

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


class XdgStartupManager:
    """One freedesktop autostart entry owned by the current Linux user.

    The entry is compared by exact content, so an upgrade that moves the
    executable is detected as "not the current registration" and repaired by
    the same reconciler Windows uses.
    """

    def __init__(self, executable: str, *, config_home: Path | None = None):
        self.executable = str(Path(executable).resolve())
        self.config_home = config_home or xdg_config_home()
        self.path = self.config_home / "autostart" / _AUTOSTART_FILENAME

    @property
    def command(self) -> str:
        # Desktop-entry Exec quoting: a quoted argument escapes backslash and
        # the quote character itself. A path with spaces stays one argument.
        escaped = self.executable.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}" --background'

    @property
    def content(self) -> str:
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={PRODUCT.display_name}\n"
            f"Comment={PRODUCT.tagline}\n"
            f"Exec={self.command}\n"
            "Icon=usageloop\n"
            "Terminal=false\n"
            "Hidden=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    def is_enabled(self) -> bool:
        try:
            return self.path.read_text(encoding="utf-8") == self.content
        except (OSError, UnicodeDecodeError):
            return False

    def has_registration(self) -> bool:
        return self.path.is_file()

    def has_valid_current_registration(self) -> bool:
        return self.is_enabled() and Path(self.executable).is_file()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Replace atomically so a crash cannot leave a half-written entry
            # that the session would try to launch.
            temporary = self.path.with_name(f"{_AUTOSTART_FILENAME}.tmp")
            temporary.write_text(self.content, encoding="utf-8")
            os.replace(temporary, self.path)
            return
        self.path.unlink(missing_ok=True)


def create_startup_manager(executable: str) -> StartupRegistration:
    if is_linux():
        return XdgStartupManager(executable)
    return WindowsStartupManager(executable)


#: Existing Windows call sites and tests keep this public name.
StartupManager = WindowsStartupManager


def reconcile_startup_preference(
    enabled: bool, manager: StartupRegistration
) -> bool:
    """Return the durable preference after safely normalizing per-user startup."""
    if enabled:
        if not manager.is_enabled():
            manager.set_enabled(True)
        return True
    if manager.has_valid_current_registration():
        return True
    if manager.has_registration():
        manager.set_enabled(False)
    return False
