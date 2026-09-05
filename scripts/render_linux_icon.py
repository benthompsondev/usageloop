"""Render the shared UsageLoop mark to the PNG the Linux bundle installs."""

from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from sentinel.branding import render_mark  # noqa: E402


SIZE = 256


def main() -> int:
    QApplication.instance() or QApplication([])
    target = Path(__file__).resolve().parents[1] / "packaging" / "linux" / "usageloop.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not render_mark(SIZE).save(str(target), "PNG"):
        print(f"Could not write {target}", file=sys.stderr)
        return 1
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
