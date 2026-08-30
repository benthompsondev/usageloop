# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

root = Path(SPEC).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from sentinel.product import PRODUCT

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)

# Qt's official Windows wheel links Qt6Core to the Windows system ICU DLL. A
# different generic icuuc.dll on PATH (for example, Poppler's) can be discovered
# by PyInstaller and copied beside the app, where it shadows System32 and fails
# at load time with WinError 127. Never redistribute that unrelated runtime.
_system_icu_names = {"icudt.dll", "icudt78.dll", "icuuc.dll"}
a.binaries = [
    entry for entry in a.binaries if Path(entry[0]).name.lower() not in _system_icu_names
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=Path(PRODUCT.executable_name).stem,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(root / "packaging" / PRODUCT.icon_filename)],
    version=str(root / "packaging" / PRODUCT.version_resource_filename),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=Path(PRODUCT.executable_name).stem,
)
