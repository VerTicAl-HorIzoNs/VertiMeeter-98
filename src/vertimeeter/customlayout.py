"""Custom layout: every pane is a free rectangle (fractions of the window), foobar-style.
Edit mode puts a drag/resize overlay on each pane. Layouts are saved by name."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme

KEYS = ["hw", "vi", "fx", "ha", "vb", "media"]
GRID = 40  # snap steps across the canvas
MIN_FRAC = 0.04


def default_rects(w=1600.0, h=870.0, widths=None, media_h=190.0) -> dict:
    """The Default arrangement expressed as fractions, so Custom always starts from Default."""
    wd = widths or dict(hw=300, vi=450, fx=215)
    top_h = h - media_h
    x = 0.0
    out = {}
    rest = w - wd["hw"] - wd["vi"] - wd["fx"]
    spans = [("hw", wd["hw"]), ("vi", wd["vi"]), ("fx", wd["fx"]), ("ha", rest * 0.6), ("vb", rest * 0.4)]
    for k, sw in spans:
        out[k] = [x / w, 0.0, sw / w, top_h / h]
        x += sw
    out["media"] = [0.0, top_h / h, 1.0, media_h / h]
    return out


class Overlay(QWidget):
    """Transparent edit layer over one pane: drag inside to move, drag edges/corners to resize."""
    moved = pyqtSignal()

    def __init__(self, canvas: "Canvas", key: str):
        super().__init__(canvas)
        self.canvas, self.key = canvas, key
        self.setMouseTracking(True)
        self.mode = None
        self.anchor = QPoint()
        self.start = QRect()

    def _edge(self, p: QPoint):
        m = 9
        r = self.rect()
        l, rt, t, b = p.x() < m, p.x() > r.width() - m, p.y() < m, p.y() > r.height() - m
        return (l, rt, t, b) if any((l, rt, t, b)) else None

    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        if self.mode is None:
            ed = self._edge(p)
            if ed is None:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif (ed[0] and ed[2]) or (ed[1] and ed[3]):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif (ed[1] and ed[2]) or (ed[0] and ed[3]):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif ed[0] or ed[1]:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            return
        d = self.mapToParent(p) - self.anchor
        r = QRect(self.start)
        if self.mode == "move":
            r.translate(d)
        else:
            l, rt, t, b = self.mode
            if l:
                r.setLeft(r.left() + d.x())
            if rt:
                r.setRight(r.right() + d.x())
            if t:
                r.setTop(r.top() + d.y())
            if b:
                r.setBottom(r.bottom() + d.y())
        self.canvas.set_pixel_rect(self.key, r)

    def mousePressEvent(self, e):
        p = e.position().toPoint()
        self.mode = self._edge(p) or "move"
        self.anchor = self.mapToParent(p)
        self.start = self.geometry()
        self.raise_()

    def mouseReleaseEvent(self, e):
        self.mode = None
        self.moved.emit()

    def paintEvent(self, _):
        t = theme.cur()
        p = QPainter(self)
        p.setPen(QPen(QColor(t["accenthi"]), 3, Qt.PenStyle.DashLine))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        p.fillRect(self.rect(), QColor(166, 77, 255, 28))
        p.setPen(QColor(t["text"]))
        p.drawText(self.rect().adjusted(8, 0, 0, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self.key.upper())
        s = 12
        p.fillRect(self.width() - s, self.height() - s, s, s, QColor(t["accenthi"]))


class Canvas(QWidget):
    changed = pyqtSignal()

    def __init__(self, panes: dict):
        super().__init__()
        self.panes = panes  # key -> widget (including "media")
        self.rects: dict = default_rects()
        self.overlays = {k: Overlay(self, k) for k in panes}
        for o in self.overlays.values():
            o.hide()
            o.moved.connect(self.changed.emit)
        self.edit = False
        self.hidden: set[str] = set()

    def attach(self):
        for k, w in self.panes.items():
            w.setParent(self)
            w.setVisible(k not in self.hidden)
        self.relayout()

    def set_edit(self, on: bool):
        self.edit = on
        self.relayout()

    def set_pixel_rect(self, key: str, r: QRect):
        W, H = max(1, self.width()), max(1, self.height())
        x, y = round(r.x() / W * GRID) / GRID, round(r.y() / H * GRID) / GRID
        w, h = round(r.width() / W * GRID) / GRID, round(r.height() / H * GRID) / GRID
        w, h = max(MIN_FRAC, w), max(MIN_FRAC, h)
        x, y = min(max(0.0, x), 1.0 - w), min(max(0.0, y), 1.0 - h)
        self.rects[key] = [x, y, w, h]
        self.relayout()

    def relayout(self):
        W, H = self.width(), self.height()
        for k, w in self.panes.items():
            fx, fy, fw, fh = self.rects.get(k, [0, 0, 0.2, 0.2])
            r = QRect(int(fx * W), int(fy * H), int(fw * W), int(fh * H))
            w.setGeometry(r)
            vis = k not in self.hidden
            w.setVisible(vis)
            o = self.overlays[k]
            o.setGeometry(r)
            o.setVisible(self.edit and vis)
            if self.edit and vis:
                o.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()


class CustomContainer(QWidget):
    """Edit toolbar on top of the canvas."""
    save = pyqtSignal()
    save_as = pyqtSignal()
    revert = pyqtSignal()
    done = pyqtSignal()

    def __init__(self, canvas: Canvas):
        super().__init__()
        self.canvas = canvas
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.bar = QWidget()
        h = QHBoxLayout(self.bar)
        h.setContentsMargins(2, 2, 2, 2)
        self.name = QLabel("")
        self.name.setObjectName("paneTitle")
        h.addWidget(self.name, 1)
        for text, sig in (("SAVE", self.save), ("SAVE AS...", self.save_as), ("REVERT", self.revert), ("DONE", self.done)):
            b = QPushButton(text)
            b.clicked.connect(sig.emit)
            h.addWidget(b)
        v.addWidget(self.bar)
        v.addWidget(canvas, 1)
        self.bar.setVisible(False)

    def set_edit(self, on: bool, name: str):
        self.bar.setVisible(on)
        self.name.setText("CUSTOM LAYOUT EDIT: " + (name or "(unsaved)") + "   drag a pane to move it, drag its edges to resize")
        self.canvas.set_edit(on)
