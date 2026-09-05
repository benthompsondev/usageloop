# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

root = Path(SPEC).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from sentinel.product import PRODUCT

name = Path(PRODUCT.executable_name).stem

a = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "packaging" / "linux" / "usageloop.png"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX corrupts some Qt shared objects on Linux and buys little on a
    # already-large Qt bundle. The Windows spec keeps it; this one does not.
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=name,
)
