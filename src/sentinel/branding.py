"""The UsageLoop mark, drawn once and reused by the app, tray, and installer.

The mark is a broken emerald ring closed by an arrowhead: a loop that keeps
coming back around. Detail is dropped as the canvas shrinks, because a 16 pixel
tray icon cannot carry an arrowhead without turning into mush, while the ring
silhouette stays recognizable at every size.

The mark carries no lettering. A glyph is unreadable below roughly 48 pixels,
and depending on a specific font would make the packaged icon vary with whatever
fonts the build machine happens to have. The wordmark in the app header carries
the name instead.
"""

from __future__ import annotations

import math
import struct

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)

# Sizes Windows actually asks for across Explorer, the taskbar, Alt+Tab, the
# tray, and the installer wizard.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

TILE = "#0D1117"
TILE_EDGE = "#1F2937"
RING = "#22D3A1"

#: Below this the arrowhead is a couple of pixels and only muddies the ring.
ARROW_MIN_SIZE = 32
#: The ring is open at the top so the gap reads as motion, not damage.
ARC_START_DEGREES = 130
ARC_SPAN_DEGREES = 288


def render_mark(size: int, *, tile: bool = True) -> QPixmap:
    """Draw the mark at one pixel size."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if tile:
        radius = size * 0.22
        painter.setBrush(QBrush(QColor(TILE)))
        if size >= 32:
            edge = QPen(QColor(TILE_EDGE))
            edge.setWidthF(max(1.0, size * 0.015))
            painter.setPen(edge)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        inset = size * 0.015
        painter.drawRoundedRect(
            QRectF(inset, inset, size - inset * 2, size - inset * 2), radius, radius
        )

    # A heavier stroke at small sizes keeps the ring from thinning into noise.
    stroke = size * (0.15 if size < ARROW_MIN_SIZE else 0.105)
    margin = size * (0.235 if tile else 0.14) + stroke / 2
    box = QRectF(margin, margin, size - margin * 2, size - margin * 2)

    pen = QPen(QColor(RING))
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(box, ARC_START_DEGREES * 16, ARC_SPAN_DEGREES * 16)

    if size >= ARROW_MIN_SIZE:
        _draw_arrowhead(painter, box, ARC_START_DEGREES + ARC_SPAN_DEGREES, stroke)

    painter.end()
    return pixmap


def _draw_arrowhead(
    painter: QPainter, box: QRectF, angle_degrees: float, stroke: float
) -> None:
    """Cap the open end of the ring with a triangle following the arc."""
    radius = box.width() / 2
    centre = box.center()
    radians = math.radians(angle_degrees)
    tip = QPointF(
        centre.x() + radius * math.cos(radians),
        centre.y() - radius * math.sin(radians),
    )
    reach = stroke * 1.05
    triangle = QPolygonF(
        [
            QPointF(reach, 0.0),
            QPointF(-reach * 0.75, reach * 0.95),
            QPointF(-reach * 0.75, -reach * 0.95),
        ]
    )
    transform = QTransform()
    transform.translate(tip.x(), tip.y())
    # Screen y grows downward, so the tangent for an increasing sweep angle
    # sits at -(angle + 90) degrees.
    transform.rotate(-(angle_degrees + 90))
    path = QPainterPath()
    path.addPolygon(transform.map(triangle))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(RING)))
    painter.drawPath(path)


def make_app_icon() -> QIcon:
    """Multi-resolution icon for the window, taskbar, and tray."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(render_mark(size))
    return icon


def build_ico_bytes(sizes: tuple[int, ...] = ICON_SIZES) -> bytes:
    """Assemble a real multi-resolution Windows .ico from PNG entries.

    Qt writes only a single image when saving .ico, which leaves Windows to
    downscale one 256 pixel bitmap for the taskbar and tray. PNG compressed
    entries are read by Windows Vista and later and by Inno Setup, so the
    container is written directly instead.
    """
    images: list[tuple[int, bytes]] = []
    for size in sorted(set(sizes)):
        if not 1 <= size <= 256:
            raise ValueError("Windows icon entries must be 1 to 256 pixels.")
        # The QByteArray must outlive the QBuffer writing into it; letting it be
        # a temporary crashes the interpreter.
        storage = QByteArray()
        buffer = QBuffer(storage)
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not render_mark(size).save(buffer, "PNG"):
            raise RuntimeError(f"Qt could not encode the {size}px icon entry.")
        buffer.close()
        images.append((size, bytes(storage)))

    header = struct.pack("<HHH", 0, 1, len(images))
    directory = b""
    offset = len(header) + 16 * len(images)
    payload = b""
    for size, data in images:
        directory += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 means 256 in the ICO directory
            0 if size >= 256 else size,
            0,  # truecolour, so no palette
            0,
            1,  # colour planes
            32,  # bits per pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)
    return header + directory + payload
