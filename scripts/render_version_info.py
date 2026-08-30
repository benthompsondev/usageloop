"""Generate PyInstaller's Windows version resource from product metadata."""

from __future__ import annotations

from pathlib import Path

from sentinel.product import PRODUCT


def main() -> int:
    parts = tuple(int(part) for part in PRODUCT.version.split(".")) + (0,)
    version = parts[:4]
    target = (
        Path(__file__).resolve().parents[1]
        / "packaging"
        / PRODUCT.version_resource_filename
    )
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', '{PRODUCT.publisher}'),
         StringStruct('FileDescription', '{PRODUCT.display_name}'),
         StringStruct('FileVersion', '{PRODUCT.version}'),
         StringStruct('InternalName', '{Path(PRODUCT.executable_name).stem}'),
         StringStruct('LegalCopyright', 'Copyright Ben Thompson'),
         StringStruct('OriginalFilename', '{PRODUCT.executable_name}'),
         StringStruct('ProductName', '{PRODUCT.display_name}'),
         StringStruct('ProductVersion', '{PRODUCT.version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
