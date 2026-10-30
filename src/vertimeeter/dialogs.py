"""Detail dialogs: parametric EQ with live response curve, compressor + gate."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from . import theme
from .widgets import HSlider

KINDS = ["Peak", "Low shelf", "High shelf", "Low-pass", "High-pass", "Notch", "Band-pass"]
PEQ_DEFAULT = [(1, 100.0), (0, 250.0), (0, 1000.0), (0, 3000.0), (0, 6000.0), (2, 10000.0)]


def default_eq() -> list[dict]:
    return [dict(on=False, kind=k, f=f, g=0.0, q=0.707) for k, f in PEQ_DEFAULT]


def bq_mag_db(kind: int, f0: float, gain_db: float, q: float, f: float, sr: float = 48000.0) -> float:
    """RBJ biquad magnitude response (same maths as the engine)."""
    f0 = min(max(f0, 10.0), sr * 0.45)
    q = min(max(q, 0.1), 18.0)
    a = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    al = sw / (2 * q)
    if kind == 1:
        s = 2 * math.sqrt(a) * al
        b = (a * ((a + 1) - (a - 1) * cw + s), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - s))
        c = ((a + 1) + (a - 1) * cw + s, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - s)
    elif kind == 2:
        s = 2 * math.sqrt(a) * al
        b = (a * ((a + 1) + (a - 1) * cw + s), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - s))
        c = ((a + 1) - (a - 1) * cw + s, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - s)
    elif kind == 3:
        b = ((1 - cw) / 2, 1 - cw, (1 - cw) / 2)
        c = (1 + al, -2 * cw, 1 - al)
    elif kind == 4:
        b = ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2)
        c = (1 + al, -2 * cw, 1 - al)
    elif kind == 5:
        b = (1.0, -2 * cw, 1.0)
        c = (1 + al, -2 * cw, 1 - al)
    elif kind == 6:
        b = (al, 0.0, -al)
        c = (1 + al, -2 * cw, 1 - al)
    else:
        b = (1 + al * a, -2 * cw, 1 - al * a)
        c = (1 + al / a, -2 * cw, 1 - al / a)
    w = 2 * math.pi * f / sr
    z1 = complex(math.cos(-w), math.sin(-w))
    z2 = z1 * z1
    num = b[0] + b[1] * z1 + b[2] * z2
    den = c[0] + c[1] * z1 + c[2] * z2
    return 20 * math.log10(max(abs(num / den), 1e-9))


class Curve(QWidget):
    def __init__(self, cells):
        super().__init__()
        self.cells = cells
        self.setMinimumSize(420, 160)

    def paintEvent(self, _):
        t = theme.cur()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(28, 6, -6, -16)
        p.fillRect(self.rect(), QColor(t["well"]))
        p.setPen(QPen(QColor(t["border"]), 1))
        p.drawRect(r)
        lo, hi = math.log10(20), math.log10(20000)
        for db in (-18, -12, -6, 0, 6, 12, 18):
            y = r.center().y() - db / 18.0 * r.height() / 2
            p.setPen(QPen(QColor(t["accent"] if db == 0 else t["border"]), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(t["dim"]))
            p.drawText(QRectF(0, y - 7, 26, 14), Qt.AlignmentFlag.AlignRight, f"{db:+d}")
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = r.left() + (math.log10(f) - lo) / (hi - lo) * r.width()
            p.setPen(QPen(QColor(t["border"]), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(t["dim"]))
            p.drawText(QRectF(x - 20, r.bottom() + 1, 40, 14), Qt.AlignmentFlag.AlignCenter, f"{f // 1000}k" if f >= 1000 else str(f))
        pts = []
        for i in range(0, 240):
            f = 10 ** (lo + (hi - lo) * i / 239)
            tot = sum(bq_mag_db(c["kind"], c["f"], c["g"], c["q"], f) for c in self.cells if c["on"])
            tot = max(-18.0, min(18.0, tot))
            pts.append(QPointF(r.left() + i / 239 * r.width(), r.center().y() - tot / 18.0 * r.height() / 2))
        p.setPen(QPen(QColor(t["accenthi"]), 2))
        p.drawPolyline(QPolygonF(pts))


def _row(grid, r, label, widget, val_label=None):
    grid.addWidget(QLabel(label), r, 0)
    grid.addWidget(widget, r, 1)
    if val_label is not None:
        val_label.setMinimumWidth(70)
        grid.addWidget(val_label, r, 2)


class PeqDialog(QDialog):
    """6-cell parametric EQ. `cells` is a list of dicts that lives in the config; `send(cell, **kw)` pushes to the engine."""

    def __init__(self, title: str, cells: list[dict], send, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.cells, self.send = cells, send
        v = QVBoxLayout(self)
        self.curve = Curve(cells)
        v.addWidget(self.curve)
        g = QGridLayout()
        g.setSpacing(3)
        for j, h in enumerate(("ON", "TYPE", "FREQ", "", "GAIN", "", "Q", "")):
            g.addWidget(QLabel(h), 0, j)
        self.rows = []
        for i, c in enumerate(cells):
            on = QCheckBox()
            on.setChecked(c["on"])
            kd = QComboBox()
            kd.addItems(KINDS)
            kd.setCurrentIndex(c["kind"])
            fs = HSlider(math.log10(20), math.log10(20000), math.log10(c["f"]))
            fl = QLabel(f"{c['f']:.0f} Hz")
            gs = HSlider(-18.0, 18.0, c["g"])
            gl = QLabel(f"{c['g']:+.1f} dB")
            qs = HSlider(math.log10(0.1), math.log10(18.0), math.log10(c["q"]))
            ql = QLabel(f"{c['q']:.2f}")
            for w in (fl, gl, ql):
                w.setMinimumWidth(62)
            for w in (fs, gs, qs):
                w.setMinimumWidth(110)
            for j, w in enumerate((on, kd, fs, fl, gs, gl, qs, ql)):
                g.addWidget(w, i + 1, j)
            on.toggled.connect(lambda x, k=i: self._set(k, on=x))
            kd.currentIndexChanged.connect(lambda x, k=i: self._set(k, kind=x))
            fs.changed.connect(lambda x, k=i, l=fl: (l.setText(f"{10 ** x:.0f} Hz"), self._set(k, f=10 ** x)))
            gs.changed.connect(lambda x, k=i, l=gl: (l.setText(f"{x:+.1f} dB"), self._set(k, g=round(x, 1))))
            qs.changed.connect(lambda x, k=i, l=ql: (l.setText(f"{10 ** x:.2f}"), self._set(k, q=10 ** x)))
            self.rows.append((on, kd, fs, gs, qs))
        v.addLayout(g)
        bar = QHBoxLayout()
        rs = QPushButton("RESET")
        rs.clicked.connect(self._reset)
        ok = QPushButton("CLOSE")
        ok.clicked.connect(self.accept)
        bar.addStretch(1)
        bar.addWidget(rs)
        bar.addWidget(ok)
        v.addLayout(bar)

    def _set(self, i, **kw):
        self.cells[i].update(kw)
        self.send(i, **{("kind" if k == "kind" else k): v for k, v in kw.items()})
        self.curve.update()

    def _reset(self):
        for i, d in enumerate(default_eq()):
            self.cells[i].update(d)
            self.send(i, **d)
        self.accept()


class DynDialog(QDialog):
    """Compressor + noise gate with exact values. `d` holds comp/gate dicts; send(kind, **kw)."""

    def __init__(self, title: str, d: dict, send, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.d, self.send = d, send
        v = QVBoxLayout(self)
        for key, name, fields in (
            ("comp", "COMPRESSOR", [("thr", "Threshold", -60.0, 0.0, "{:.0f} dB"), ("ratio", "Ratio", 1.0, 20.0, "{:.1f} : 1"),
                                    ("att", "Attack", 0.5, 200.0, "{:.1f} ms"), ("rel", "Release", 10.0, 1000.0, "{:.0f} ms"),
                                    ("makeup", "Makeup", 0.0, 24.0, "{:.1f} dB")]),
            ("gate", "NOISE GATE", [("thr", "Threshold", -80.0, 0.0, "{:.0f} dB"), ("att", "Attack", 0.5, 100.0, "{:.1f} ms"),
                                    ("rel", "Release", 10.0, 1000.0, "{:.0f} ms")]),
        ):
            t = QLabel(name)
            t.setObjectName("paneTitle")
            v.addWidget(t)
            on = QCheckBox("enabled")
            on.setChecked(d[key]["on"])
            on.toggled.connect(lambda x, k=key: self._set(k, on=x))
            v.addWidget(on)
            g = QGridLayout()
            for r, (f, lab, lo, hi, fmt) in enumerate(fields):
                s = HSlider(lo, hi, d[key][f])
                s.setMinimumWidth(240)
                l = QLabel(fmt.format(d[key][f]))
                s.changed.connect(lambda x, k=key, ff=f, ll=l, fm=fmt: (ll.setText(fm.format(x)), self._set(k, **{ff: x})))
                _row(g, r, lab, s, l)
            v.addLayout(g)
        ok = QPushButton("CLOSE")
        ok.clicked.connect(self.accept)
        v.addWidget(ok)

    def _set(self, k, **kw):
        self.d[k].update(kw)
        self.send(k, **kw)


def default_dyn() -> dict:
    return dict(comp=dict(on=False, thr=-20.0, ratio=3.0, att=10.0, rel=100.0, makeup=0.0),
                gate=dict(on=False, thr=-50.0, att=2.0, rel=150.0))
