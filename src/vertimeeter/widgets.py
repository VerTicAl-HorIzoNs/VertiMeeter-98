"""Custom-painted controls. Geometry and colors are copied from mockup 5 (knob 40x54, fader 44, meter 22 ...)."""
from __future__ import annotations

import math
import time

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QLabel, QWidget

from . import theme


def _c(k):
    return QColor(theme.cur()[k])


def _mono(px):
    f = QFont("DejaVu Sans Mono")
    f.setPixelSize(px)
    return f


def _sans(px, bold=False):
    f = QFont("DejaVu Sans")
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def interp(x, xs, ys):
    for i in range(1, len(xs)):
        if x <= xs[i]:
            return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[-1]


FDB = [-60, -40, -20, -10, 0, 6, 12]
FP = [0, .12, .38, .55, .78, .9, 1]


class ClickLabel(QLabel):
    """QLabel that reports clicks / double clicks / right clicks (name boxes, device boxes, app rows)."""
    clicked = pyqtSignal()
    doubleClicked = pyqtSignal()
    context = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.context.emit()
        elif e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()


class Fader(QWidget):
    """Vertical fader -60..+12 dB with the mockup's tapered scale, tick marks both sides and a 34x16 cap."""
    changed = pyqtSignal(float)

    def __init__(self, db=0.0, parent=None):
        super().__init__(parent)
        self.db = db
        self.lo, self.hi = -60.0, 12.0
        self.setFixedWidth(44)
        self.setMinimumHeight(60)
        self.setToolTip("Fader gain: drag / wheel (Shift = fine). Double-click = 0 dB")

    def set_db(self, db, emit=False):
        db = round(max(self.lo, min(self.hi, db)), 1)
        if db != self.db:
            self.db = db
            self.update()
            if emit:
                self.changed.emit(db)

    def _y(self, db):
        return 9 + (1 - interp(db, FDB, FP)) * (self.height() - 18)

    def _from_y(self, py):
        p = 1 - (py - 9) / max(1, self.height() - 18)
        p = max(0.0, min(1.0, p))
        db = interp(p, FP, FDB)
        if abs(db) < .4:
            db = 0.0
        self.set_db(db, True)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._from_y(e.position().y())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._from_y(e.position().y())

    def mouseDoubleClickEvent(self, e):
        self.set_db(0.0, True)

    def wheelEvent(self, e):
        fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.set_db(self.db + (0.5 if e.angleDelta().y() > 0 else -0.5) * (0.2 if fine else 1), True)

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = 44, self.height()
        acc, dim = _c("knobacc"), QColor("#7d7794")
        for db in FDB:
            yy = round(self._y(db))
            z = db == 0
            ln = 7 if z else 4
            p.fillRect(0, yy, ln, 1, acc if z else dim)
            p.fillRect(W - ln, yy, ln, 1, acc if z else dim)
        p.fillRect(19, 9, 6, H - 18, QColor("#050508"))
        p.setPen(QPen(QColor("#4a4560"), 1))
        p.drawRect(QRectF(19.5, 9.5, 5, H - 19))
        yy = round(self._y(self.db))
        p.fillRect(5, yy - 8, 34, 16, QColor("#1a1726"))
        p.setPen(QPen(QColor("#8a82a8"), 1))
        p.drawRect(QRectF(5.5, yy - 7.5, 33, 15))
        p.fillRect(9, yy - 1, 26, 2, acc)
        p.fillRect(6, yy + 7, 33, 1, QColor("#000000"))


class Meter(QWidget):
    """Stereo LED-segment peak meter with peak hold and clip LED (22 px wide, same drawing as the mockup)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.disp = [-90.0, -90.0]
        self.hold = [-90.0, -90.0]
        self.ht = [0.0, 0.0]
        self.clip = False
        self._t = time.monotonic()
        self.l = self.r = 0.0
        self.setFixedWidth(22)
        self.setMinimumHeight(60)
        self.setToolTip("Peak meter (click to clear clip LED)")

    def mousePressEvent(self, e):
        self.clip = False
        self.update()

    def peak_db(self) -> float:
        return max(self.disp)

    def set_levels(self, l, r):
        now = time.monotonic()
        dt = min(0.2, now - self._t)
        self._t = now
        self.l, self.r = l, r
        for ch, v in enumerate((l, r)):
            db = 20 * math.log10(v) if v > 1e-6 else -90.0
            self.disp[ch] = max(db, self.disp[ch] - 2.2 * dt * 60)
            if db >= self.hold[ch]:
                self.hold[ch] = db
                self.ht[ch] = now
            elif now - self.ht[ch] > 1.5:
                self.hold[ch] = max(db, self.hold[ch] - 1.0)
            if v >= .999:
                self.clip = True
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = 22, self.height()
        p.fillRect(0, 0, W, H, QColor("#050508"))
        p.setPen(QPen(QColor("#4a4560"), 1))
        p.drawRect(QRectF(.5, .5, W - 1, H - 1))
        p.fillRect(2, 2, W - 4, 5, QColor("#e0304f" if self.clip else "#2a1018"))
        top, seg, gap = 9, 3, 1
        n = max(1, (H - top - 2) // (seg + gap))
        bw = (W - 5) // 2
        cols = (QColor("#e0304f"), _c("bright"), _c("hi"), _c("dk"))
        for ch in (0, 1):
            for k in range(n):
                d = -60 + (k + 1) / n * 60
                yy = H - 2 - (k + 1) * (seg + gap) + gap
                lit = self.disp[ch] >= d - 60 / n * .5
                held = (not lit) and abs(self.hold[ch] - d) < 60 / n * .6
                if lit:
                    col = cols[0] if d > -2 else cols[1] if d > -9 else cols[2] if d > -24 else cols[3]
                elif held:
                    col = QColor("#eadcff")
                else:
                    col = QColor("#1a1626")
                p.fillRect(2 + ch * (bw + 1), yy, bw, seg, col)


class Knob(QWidget):
    """40x54 rotary: label on top, arc + needle, value below. Drag up/down, wheel, double-click resets.
    Right-click emits `context`."""
    changed = pyqtSignal(float)
    context = pyqtSignal()

    def __init__(self, label, lo=0.0, hi=10.0, val=0.0, default=None, fmt="{:.1f}", bip=None, parent=None):
        super().__init__(parent)
        self.label, self.lo, self.hi, self.val = label, lo, hi, val
        self.default = val if default is None else default
        self.fmt = fmt
        self.bip = (lo < 0 < hi) if bip is None else bip
        self._y = 0.0
        self._v0 = val
        self.setFixedSize(40, 54)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(f"{label}: drag / wheel (Shift = fine). Double-click = reset")

    def set_value(self, v, emit=False):
        v = max(self.lo, min(self.hi, v))
        if abs(v - self.default) < (self.hi - self.lo) * .012:
            v = self.default
        if v != self.val:
            self.val = v
            self.update()
            if emit:
                self.changed.emit(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.context.emit()
            return
        self._y = e.position().y()
        self._v0 = self.val

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.set_value(self._v0 + (self._y - e.position().y()) / 120.0 * (self.hi - self.lo) * (0.2 if fine else 1), True)

    def mouseDoubleClickEvent(self, e):
        self.set_value(self.default, True)

    def wheelEvent(self, e):
        fine = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        st = (self.hi - self.lo) / 60 * (0.2 if fine else 1)
        self.set_value(self.val + (st if e.angleDelta().y() > 0 else -st), True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx, cy, r = 20.0, 26.0, 12.0
        f = (self.val - self.lo) / max(1e-9, self.hi - self.lo)
        a = 225 - f * 270
        box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#2a2638"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawArc(box, int(225 * 16), int(-270 * 16))
        acc = _c("knobacc")
        p.setPen(QPen(acc, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        if self.bip:
            f0 = (self.default - self.lo) / max(1e-9, self.hi - self.lo)
            a0 = 225 - f0 * 270
            if abs(a - a0) > .3:
                p.drawArc(box, int(a0 * 16), int((a - a0) * 16))
        elif f > .001:
            p.drawArc(box, int(225 * 16), int((a - 225) * 16))
        p.setBrush(QColor("#1d1a28"))
        p.setPen(QPen(QColor("#8a82a8"), 1))
        p.drawEllipse(QPointF(cx, cy), r - 4, r - 4)
        rad = math.radians(a)
        p.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        p.drawLine(QPointF(cx + 2 * math.cos(rad), cy - 2 * math.sin(rad)),
                   QPointF(cx + (r - 3) * math.cos(rad), cy - (r - 3) * math.sin(rad)))
        # the mockup's CSS (font-size 11px on *) overrides the SVG font sizes, so both texts are 11 px
        p.setFont(_sans(11, True))
        p.setPen(QColor("#7d7794"))
        p.drawText(QPointF((40 - p.fontMetrics().horizontalAdvance(self.label)) / 2, 8), self.label)
        p.setFont(_mono(11))
        p.setPen(_c("hi"))
        txt = self.fmt.format(self.val)
        p.drawText(QPointF((40 - p.fontMetrics().horizontalAdvance(txt)) / 2, 50), txt)


class HSlider(QWidget):
    """Horizontal slider: dark groove, accent fill, 12x22 cap (media volume, taps dialog, ...)."""
    changed = pyqtSignal(float)

    def __init__(self, lo=0.0, hi=1.0, val=0.0, default=None, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.val = lo, hi, val
        self.default = val if default is None else default
        self.setMinimumHeight(22)
        self.setMinimumWidth(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_value(self, v, emit=False):
        v = max(self.lo, min(self.hi, v))
        if v != self.val:
            self.val = v
            self.update()
            if emit:
                self.changed.emit(v)

    def _at(self, x):
        m = 6
        f = (x - m) / max(1, self.width() - 2 * m)
        return self.lo + max(0.0, min(1.0, f)) * (self.hi - self.lo)

    def mousePressEvent(self, e):
        self.set_value(self._at(e.position().x()), True)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self.set_value(self._at(e.position().x()), True)

    def mouseDoubleClickEvent(self, e):
        self.set_value(self.default, True)

    def wheelEvent(self, e):
        st = (self.hi - self.lo) / 40
        self.set_value(self.val + (st if e.angleDelta().y() > 0 else -st), True)

    def paintEvent(self, _):
        p = QPainter(self)
        m = 6
        cy = self.height() / 2
        frac = (self.val - self.lo) / max(1e-9, self.hi - self.lo)
        g = QRectF(m, cy - 5, self.width() - 2 * m, 10)
        p.fillRect(g, _c("trackbg"))
        x = m + frac * g.width()
        p.fillRect(QRectF(m, cy - 5, x - m, 10), _c("accent"))
        p.setPen(QPen(_c("borderhi"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(g)
        p.setBrush(_c("text"))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(QRectF(x - 6, cy - 11, 12, 22))


class ScrollSlider(QWidget):
    """The wide pane scroll slider (mockup: 14 px track, 110 px cap with an accent underline)."""
    valueChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mx = 0
        self.v = 0
        self.setMinimumWidth(40)
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_range(self, mx):
        self.mx = max(0, mx)
        self.v = min(self.v, self.mx)
        self.update()

    def set_value(self, v):
        v = max(0, min(self.mx, int(v)))
        if v != self.v:
            self.v = v
            self.update()

    def _cap(self):
        return min(110, max(20, self.width() - 10))

    def _from(self, x):
        cap = self._cap()
        f = (x - cap / 2) / max(1, self.width() - cap)
        v = int(round(max(0.0, min(1.0, f)) * self.mx))
        if v != self.v:
            self.v = v
            self.valueChanged.emit(v)
            self.update()

    def mousePressEvent(self, e):
        if self.mx > 0:
            self._from(e.position().x())

    def mouseMoveEvent(self, e):
        if self.mx > 0 and e.buttons() & Qt.MouseButton.LeftButton:
            self._from(e.position().x())

    def wheelEvent(self, e):
        if self.mx > 0:
            self.set_value(self.v - e.angleDelta().y() / 2)
            self.valueChanged.emit(self.v)

    def paintEvent(self, _):
        p = QPainter(self)
        if self.mx <= 0:
            p.setOpacity(.4)
        W, H = self.width(), self.height()
        cap = self._cap()
        tr = QRectF(0, (H - 14) / 2, W, 14)
        f = self.v / self.mx if self.mx else 0.0
        x = f * (W - cap)
        p.fillRect(tr, _c("trackbg"))
        p.fillRect(QRectF(0, tr.top(), x + cap / 2, 14), _c("accentdk"))
        p.setPen(QPen(_c("borderhi"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(.5, tr.top() + .5, W - 1, 13))
        th = QRectF(x, (H - 20) / 2, cap, 20)
        p.fillRect(th, _c("panel2"))
        p.fillRect(QRectF(th.left() + 1, th.bottom() - 3, cap - 2, 2), _c("accent"))
        p.setPen(QPen(_c("borderhi"), 1))
        p.drawLine(QPointF(th.left(), th.top()), QPointF(th.right(), th.top()))
        p.drawLine(QPointF(th.left(), th.top()), QPointF(th.left(), th.bottom()))
        p.drawLine(QPointF(th.right(), th.top()), QPointF(th.right(), th.bottom()))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawLine(QPointF(th.left(), th.bottom()), QPointF(th.right(), th.bottom()))


class SeekBar(QWidget):
    """Media seek bar: dark well, gradient fill. Click or drag to seek (emits a 0..1 fraction on release / drag)."""
    seeked = pyqtSignal(float)
    dragged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.f = 0.0
        self.dur = 0.0  # seconds, set by the player row: the bubble shows the time under the handle
        self.drag = False
        self._bub = None
        self.setFixedHeight(14)
        self.setMinimumWidth(60)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_fraction(self, f):
        if not self.drag:
            self.f = max(0.0, min(1.0, f))
            self.update()

    @staticmethod
    def _ft(s):
        s = int(max(0, s))
        return f"{s // 60}:{s % 60:02d}"

    def _set(self, x):
        self.f = max(0.0, min(1.0, x / max(1, self.width())))
        self.update()
        self.dragged.emit(self.f)
        self._bubble()

    def _bubble(self):
        t = theme.cur()
        if self._bub is None:
            self._bub = QLabel(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            self._bub.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self._bub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bub.setStyleSheet(f"QLabel {{ background: {t['panel']}; color: {t['accenthi']}; border: 1px solid {t['accent']}; "
                                f"padding: 2px 8px; font-weight: bold; }}")
        self._bub.setText(f"{self._ft(self.f * self.dur)} / {self._ft(self.dur)}")
        self._bub.adjustSize()
        g = self.mapToGlobal(QPoint(int((self.width() - 2) * self.f), 0))
        self._bub.move(g.x() - self._bub.width() // 2, g.y() - self._bub.height() - 4)
        self._bub.show()

    def mousePressEvent(self, e):
        self.drag = True
        self._set(e.position().x())

    def mouseMoveEvent(self, e):
        if self.drag:
            self._set(e.position().x())

    def mouseReleaseEvent(self, e):
        if self.drag:
            self.drag = False
            if self._bub is not None:
                self._bub.hide()
            self.seeked.emit(self.f)

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor("#050508"))
        w = int((W - 2) * self.f)
        if w > 0:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, _c("accentdk"))
            g.setColorAt(1, _c("accent"))
            p.fillRect(1, 1, w, H - 2, g)
        if self.drag:  # handle while scrubbing
            p.fillRect(max(0, w - 1), 0, 3, H, _c("accenthi"))
        p.setPen(QPen(_c("border"), 1))
        p.drawRect(QRectF(.5, .5, W - 1, H - 1))


class Pad(QWidget):
    """Intellipan (kind 'ip', 116x74, hardware strips) or 5.1 pan pot (kind 'pan', 116x70, virtual strips).
    ip modes: color / 3d.  pan modes: pan / 3d.  Right-click switches mode, double-click centers."""
    changed = pyqtSignal(float, float)
    modeChanged = pyqtSignal(str)
    NAMES = {"color": "COLOR PANEL", "3d": "3D PANEL (binaural)", "pan": "NORMAL", "mod": "MODULATION"}

    def __init__(self, x=0.0, y=1.0, mode="pan", modes=("pan", "3d"), kind="pan", parent=None):
        super().__init__(parent)
        self.MODES = tuple(modes)
        self.kind = kind
        if mode not in self.MODES:
            mode = self.MODES[0]
        self.x, self.y, self.mode = x, y, mode
        self.setFixedSize(116, 74 if kind == "ip" else 70)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("Intellipan. Drag the dot. Right-click: switch mode. Double-click = center.\n"
                        "3D panel: top = in front of you, right = right ear only." if kind == "ip" else
                        "Pan pot. Drag. Right-click: Normal / 3D (binaural). Double-click = center.\n"
                        "Left edge = left speaker only, right edge = right speaker only.")

    def set_state(self, x, y, mode):
        self.x, self.y, self.mode = x, y, mode
        self.update()

    def _from(self, p):
        if self.kind == "ip":
            self.x = max(-1.0, min(1.0, (p.x() - 58) / 48))
            self.y = max(-1.0, min(1.0, -(p.y() - 41) / 26))
        else:
            self.x = max(-1.0, min(1.0, (p.x() - 58) / 40))
            self.y = max(-1.0, min(1.0, -(p.y() - 34) / 20))
        if self.mode == "pan":
            self.y = 1.0
        self.changed.emit(self.x, self.y)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.mode = self.MODES[(self.MODES.index(self.mode) + 1) % len(self.MODES)]
            self.modeChanged.emit(self.mode)  # the strip loads that mode's saved position via set_state()
            self.update()
            return
        self._from(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._from(e.position())

    def mouseDoubleClickEvent(self, e):
        self.x, self.y = (-1.0, -1.0) if self.mode == "mod" else (0.0, (0.0 if self.mode == "color" else 1.0))
        self.changed.emit(self.x, self.y)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor("#0a0912"))
        p.setPen(QPen(QColor("#4a4560"), 1))
        p.drawRect(QRectF(.5, .5, W - 1, H - 1))
        dim = QColor("#7d7794")
        p.setFont(_sans(8))
        if self.kind == "ip":
            p.setPen(dim)
            if self.mode == "color":
                p.drawText(4, 10, "COLOR PANEL")
                p.drawText(4, 70, "brightness")
                p.setPen(QPen(QColor("#241f33"), 1))
                p.drawLine(58, 16, 58, 66)
                p.drawLine(8, 41, 108, 41)
                p.setPen(dim)
                p.drawText(4, 41, "Lo")
                p.drawText(100, 41, "Hi")
            elif self.mode == "mod":
                p.drawText(4, 10, "MODULATION (tremolo)")
                p.drawText(4, 70, "depth")
                p.drawText(76, 70, "rate")
                p.setPen(QPen(QColor("#241f33"), 1))
                p.drawLine(8, 41, 108, 41)
                p.drawLine(58, 16, 58, 66)
            else:
                p.drawText(4, 10, "3D PANEL (binaural)")
                p.setPen(QPen(QColor("#4a4560"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(58, 41), 10, 10)
                p.setPen(dim)
                p.drawText(48, 22, "front")
                p.drawText(50, 68, "rear")
                p.drawText(6, 44, "L")
                p.drawText(104, 44, "R")
            px, py = 58 + self.x * 48, 41 - self.y * 26
            p.fillRect(QRectF(px - 4, py - 4, 8, 8), QColor("#ff5fa8") if self.mode == "color" else QColor("#e8a21c") if self.mode == "mod" else QColor("#27b7ae"))
            off = (self.y > -0.95) if self.mode == "mod" else abs(self.x) + abs(self.y if self.mode == "color" else self.y - 1) > 0.02
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#e8a21c") if off else QColor("#2a2638"))
            for i in range(3):
                p.drawEllipse(QPointF(100 + i * 5, 8), 2, 2)
        else:
            p.setPen(dim)
            for n, x, y in ((("FL", 14, 12), ("FC", 58, 8), ("FR", 102, 12), ("RL", 14, 60), ("RR", 102, 60)) if self.mode != "mod" else ()):
                p.drawText(x - 6, y + 3 + 4, n)
            p.drawText(32, 66, self.NAMES.get(self.mode, self.mode.upper()) if self.mode != "pan" else "NORMAL")
            py = 34 if self.mode == "pan" else 34 - self.y * 20
            p.fillRect(QRectF(58 + self.x * 40 - 4, py - 4, 8, 8), _c("accent"))
