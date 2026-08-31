"""The UsageLoop mark, drawn once and reused by the app, tray, and installer.

The mark is a set of emerald arrows wrapped around a "5hr" glyph: a five-hour
window that keeps coming back around.

Detail is size-adaptive. At 32 pixels and above the full loop-plus-5hr mark is
drawn. Below that the lettering is removed and the loop becomes a thicker,
high-contrast silhouette for the Windows tray.

The "5hr" is drawn from explicit path geometry rather than typeset. Rendering
text would make the packaged icon depend on a font being installed on whichever
machine ran the build, which is not something an icon should depend on.
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

TILE = "#08131F"
TILE_EDGE = "#284158"
RING = "#20D5A4"
RING_SECONDARY = "#14B8A6"
GLYPH = "#F6FFFC"

#: Below this the "5hr" lettering and the arrowhead are a few pixels across and
#: read as noise, so the ring-only silhouette is used.
DETAIL_MIN_SIZE = 32
#: Fraction of the canvas the glyph occupies on its longest side. Three
#: characters are wider than two, so this is measured across the width.
GLYPH_SCALE = 0.47


def five_hour_glyph() -> QPainterPath:
    """The "5hr" lettering on a shared grid, built from geometry not a font.

    Baseline sits at y=60 with the x-height around y=32, so the three characters
    align the way type would without needing a typeface to be installed.
    """
    path = QPainterPath()
    # 5: top bar, left stem to the waist, then the bowl.
    path.moveTo(34, 6)
    path.lineTo(9, 6)
    path.lineTo(9, 31)
    path.lineTo(21, 31)
    path.cubicTo(31, 31, 35, 36, 35, 45)
    path.cubicTo(35, 55, 28, 61, 18, 61)
    path.cubicTo(12, 61, 8, 59, 5, 56)
    # h: full-height stem, shoulder, right leg.
    path.moveTo(46, 3)
    path.lineTo(46, 61)
    path.moveTo(46, 35)
    path.cubicTo(51, 27, 60, 27, 66, 35)
    path.lineTo(66, 61)
    # r: short stem and a single arm.
    path.moveTo(78, 29)
    path.lineTo(78, 61)
    path.moveTo(78, 39)
    path.cubicTo(82, 31, 87, 29, 94, 31)
    return path


def render_mark(size: int, *, tile: bool = True) -> QPixmap:
    """Draw the mark at one pixel size, dropping detail as the canvas shrinks."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    detailed = size >= DETAIL_MIN_SIZE

    if tile:
        radius = size * 0.22
        painter.setBrush(QBrush(QColor(TILE)))
        if size >= DETAIL_MIN_SIZE:
            edge = QPen(QColor(TILE_EDGE))
            edge.setWidthF(max(1.0, size * 0.015))
            painter.setPen(edge)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        inset = size * 0.015
        painter.drawRoundedRect(
            QRectF(inset, inset, size - inset * 2, size - inset * 2), radius, radius
        )

    # A heavier stroke on the small ring-only sizes keeps it from thinning out.
    stroke = size * (0.15 if not detailed else 0.078)
    margin = size * (0.085 if tile else 0.05) + stroke / 2
    if not detailed and tile:
        margin = size * 0.235 + stroke / 2
    box = QRectF(margin, margin, size - margin * 2, size - margin * 2)

    segments = (
        ((16, 96), (142, 94), (266, 88))
        if size >= 64
        else ((28, 132), (205, 124))
    )
    colours = (RING, RING_SECONDARY, RING)
    for index, (start, span) in enumerate(segments):
        pen = QPen(QColor(colours[index]))
        pen.setWidthF(stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(box, start * 16, span * 16)
        if detailed:
            _draw_arrowhead(painter, box, start + span, stroke, colours[index])

    if detailed:
        _draw_glyph(painter, size)

    painter.end()
    return pixmap


def _draw_arrowhead(
    painter: QPainter,
    box: QRectF,
    angle_degrees: float,
    stroke: float,
    colour: str = RING,
) -> None:
    """Cap the open end of the ring with a triangle following the arc."""
    radius = box.width() / 2
    centre = box.center()
    radians = math.radians(angle_degrees)
    tip = QPointF(
        centre.x() + radius * math.cos(radians),
        centre.y() - radius * math.sin(radians),
    )
    reach = stroke * 1.02
    triangle = QPolygonF(
        [
            QPointF(reach, 0.0),
            QPointF(-reach * 0.7, reach),
            QPointF(-reach * 0.7, -reach),
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
    painter.setBrush(QBrush(QColor(colour)))
    painter.drawPath(path)


def _draw_glyph(painter: QPainter, size: int) -> None:
    raw = five_hour_glyph()
    bounds = raw.boundingRect()
    scale = (size * GLYPH_SCALE) / max(bounds.width(), bounds.height())
    transform = QTransform()
    transform.translate(size / 2, size / 2)
    transform.scale(scale, scale)
    transform.translate(-bounds.center().x(), -bounds.center().y())
    pen = QPen(QColor(GLYPH))
    pen.setWidthF(max(1.0, size * 0.043))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(transform.map(raw))


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
