"""Render the multi-resolution app icon used by PyInstaller and Inno Setup."""

from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from sentinel.branding import build_ico_bytes
from sentinel.product import PRODUCT


def main() -> int:
    QApplication.instance() or QApplication(sys.argv[:1])
    target = Path(__file__).resolve().parents[1] / "packaging" / PRODUCT.icon_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_ico_bytes())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
