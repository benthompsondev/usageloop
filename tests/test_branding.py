"""The packaged icon has to work at Windows' real icon sizes."""

import struct
import unittest

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication

from sentinel.branding import ICON_SIZES, build_ico_bytes, make_app_icon, render_mark


def ensure_app() -> None:
    QApplication.instance() or QApplication([])


class BrandMarkTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_app()

    def test_mark_renders_at_every_packaged_size(self) -> None:
        for size in ICON_SIZES:
            with self.subTest(size=size):
                pixmap = render_mark(size)
                self.assertIsInstance(pixmap, QPixmap)
                self.assertEqual(size, pixmap.width())
                self.assertFalse(pixmap.isNull())

    def test_small_sizes_stay_visible(self) -> None:
        """A 16px tray icon that renders as near-empty is a broken icon."""
        image = render_mark(16).toImage()
        opaque = sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 40
        )
        self.assertGreater(opaque, 100)

    def test_app_icon_offers_all_sizes(self) -> None:
        icon = make_app_icon()
        self.assertEqual(
            sorted(ICON_SIZES), sorted({size.width() for size in icon.availableSizes()})
        )


class IcoContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        ensure_app()

    def test_container_declares_every_size(self) -> None:
        data = build_ico_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((0, 1), (reserved, kind))
        self.assertEqual(len(ICON_SIZES), count)

    def test_entries_point_at_real_image_data(self) -> None:
        data = build_ico_bytes()
        _reserved, _kind, count = struct.unpack_from("<HHH", data, 0)
        for index in range(count):
            width, _h, _colours, _r, _planes, _bits, length, offset = struct.unpack_from(
                "<BBBBHHII", data, 6 + index * 16
            )
            with self.subTest(entry=index):
                self.assertGreater(length, 0)
                self.assertLessEqual(offset + length, len(data))
                # 0 in the directory means 256, the ICO format's own convention.
                self.assertIn(width, {0, *ICON_SIZES})

    def test_written_icon_loads_back_at_small_sizes(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "icon.ico"
            path.write_bytes(build_ico_bytes())
            icon = QIcon(str(path))
            loaded = {size.width() for size in icon.availableSizes()}
        self.assertIn(16, loaded)
        self.assertIn(256, loaded)

    def test_rejects_a_size_windows_cannot_store(self) -> None:
        with self.assertRaises(ValueError):
            build_ico_bytes((512,))


if __name__ == "__main__":
    unittest.main()
