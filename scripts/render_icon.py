"""Render the app icon used by PyInstaller and Inno Setup."""

from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from sentinel.desktop import make_app_icon
from sentinel.product import PRODUCT


def main() -> int:
    QApplication.instance() or QApplication(sys.argv[:1])
    target = Path(__file__).resolve().parents[1] / "packaging" / PRODUCT.icon_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    pixmap = make_app_icon().pixmap(256, 256)
    if not pixmap.save(str(target), "ICO"):
        raise RuntimeError("Qt could not render the Windows icon.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
