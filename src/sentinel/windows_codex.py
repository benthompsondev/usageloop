"""Resolve the current user's registered Codex desktop package."""

from __future__ import annotations

import ctypes
from pathlib import Path


_CODEX_PACKAGE_FAMILY = "OpenAI.Codex_2p2nqsd0c76g0"
_INSUFFICIENT_BUFFER = 122


def registered_codex_packages() -> tuple[tuple[str, Path], ...]:
    """Return (package version, install root), newest registered version first.

    Use Windows package registration so packages on other drives work and
    stale package directories on disk are ignored.
    """
    try:
        kernel = ctypes.WinDLL("kernel32")
        query = kernel.GetPackagesByPackageFamily
        package_path = kernel.GetPackagePathByFullName
    except (AttributeError, OSError):
        return ()

    count = ctypes.c_uint32()
    size = ctypes.c_uint32()
    if query(_CODEX_PACKAGE_FAMILY, ctypes.byref(count), None, ctypes.byref(size), None) != _INSUFFICIENT_BUFFER:
        return ()
    names = (ctypes.c_wchar_p * count.value)()
    buffer = ctypes.create_unicode_buffer(size.value)
    if query(_CODEX_PACKAGE_FAMILY, ctypes.byref(count), names, ctypes.byref(size), buffer):
        return ()

    packages = []
    for name in names[:count.value]:
        size = ctypes.c_uint32()
        if package_path(name, ctypes.byref(size), None) != _INSUFFICIENT_BUFFER:
            continue
        root = ctypes.create_unicode_buffer(size.value)
        if package_path(name, ctypes.byref(size), root) == 0:
            version = name.split("_")[1]
            packages.append((version, Path(root.value)))
    return tuple(sorted(packages, key=lambda package: package_version(package[0]), reverse=True))


def package_version(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if len(parts) == 4 and all(part.isascii() and part.isdecimal() and len(part) <= 5 for part in parts):
        return tuple(map(int, parts))
    return ()
