"""Special FX pane: reverb, 8-tap delay. Strips feed them with RVB/DLY knobs, buses take them back with R.RET/D.RET."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QBoxLayout, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from .widgets import HSlider, Knob


def default_fx() -> dict:
    return dict(rvb_on=True, room=0.5, damp=0.5, width=1.0, dly_on=True, fb=0.3,
                taps=[dict(on=i < 2, ms=120.0 * (i + 1), gain_db=-4.0 - 3 * i, pan=(-0.5 if i % 2 == 0 else 0.5)) for i in range(8)])


class TapsDialog(QDialog):
    def __init__(self, fx: dict, eng, parent=None):
        super().__init__(parent)
        self.setWindowTitle("8-tap delay")
        self.fx, self.eng = fx, eng
        v = QVBoxLayout(self)
        g = QGridLayout()
        for j, h in enumerate(("TAP", "ON", "TIME", "", "LEVEL", "", "PAN", "")):
            g.addWidget(QLabel(h), 0, j)
        for i, t in enumerate(fx["taps"]):
            on = QCheckBox()
            on.setChecked(t["on"])
            ms = HSlider(1.0, 1990.0, t["ms"])
            msl = QLabel(f"{t['ms']:.0f} ms")
            gd = HSlider(-40.0, 6.0, t["gain_db"])
            gdl = QLabel(f"{t['gain_db']:+.1f} dB")
            pn = HSlider(-1.0, 1.0, t["pan"])
            pnl = QLabel(f"{t['pan']:+.2f}")
            for w in (ms, gd, pn):
                w.setMinimumWidth(130)
            for w in (msl, gdl, pnl):
                w.setMinimumWidth(62)
            for j, w in enumerate((QLabel(str(i + 1)), on, ms, msl, gd, gdl, pn, pnl)):
                g.addWidget(w, i + 1, j)
            on.toggled.connect(lambda x, k=i: self._set(k, on=x))
            ms.changed.connect(lambda x, k=i, l=msl: (l.setText(f"{x:.0f} ms"), self._set(k, ms=x)))
            gd.changed.connect(lambda x, k=i, l=gdl: (l.setText(f"{x:+.1f} dB"), self._set(k, gain_db=round(x, 1))))
            pn.changed.connect(lambda x, k=i, l=pnl: (l.setText(f"{x:+.2f}"), self._set(k, pan=round(x, 2))))
        v.addLayout(g)
        b = QPushButton("CLOSE")
        b.clicked.connect(self.accept)
        v.addWidget(b)

    def _set(self, i, **kw):
        self.fx["taps"][i].update(kw)
        self.eng.send(c="fx", tap=i, **kw)


class FxEdit(QDialog):
    """EDIT... dialog of the reverb / delay boxes: knobs for every parameter."""

    def __init__(self, title, fx, eng, knobs, parent=None, extra=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.fx, self.eng = fx, eng
        v = QVBoxLayout(self)
        cap = QLabel(title.upper())
        cap.setObjectName("dcap")
        v.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(2)
        for lab, key, lo, hi in knobs:
            k = Knob(lab, lo, hi, fx[key], fx[key], "{:.2f}")
            k.changed.connect(lambda x, kk=key: (fx.__setitem__(kk, x), eng.send(c="fx", **{kk: x})))
            row.addWidget(k)
        row.addStretch(1)
        v.addLayout(row)
        if extra is not None:
            v.addWidget(extra)
        b = QPushButton("CLOSE")
        b.clicked.connect(self.accept)
        v.addWidget(b)
        self.setMinimumWidth(260)


class FxPanel(QWidget):
    """Special FX pane (mockup 5): REVERB box, DELAY box, EXTERNAL FX RETURN box."""
    message = pyqtSignal(str)
    c5_requested = pyqtSignal()

    def __init__(self, cfg, eng, on_ports=None):
        super().__init__()
        self.cfg, self.eng = cfg, eng
        self.fx = cfg.d.setdefault("fx", default_fx())
        for k, v in default_fx().items():
            self.fx.setdefault(k, v)
        v = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.box = v
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(6)
        v.addWidget(self._box("REVERB", "rvb_on", self._edit_rvb), 0, Qt.AlignmentFlag.AlignTop)
        d = self._box("DELAY", "dly_on", self._edit_dly)
        c5 = QPushButton("Switch to C5")
        c5.setFixedHeight(18)
        c5.setStyleSheet("font-size: 10px; padding: 0;")
        c5.setToolTip("C5 multiband compressor: pick a bus")
        c5.clicked.connect(self.c5_requested.emit)
        d.layout().addWidget(c5)
        v.addWidget(d, 0, Qt.AlignmentFlag.AlignTop)
        ex = QFrame()
        ex.setObjectName("fxbox")
        el = QVBoxLayout(ex)
        el.setContentsMargins(4, 4, 4, 4)
        el.setSpacing(3)
        cap = QLabel("EXTERNAL FX RETURN")
        cap.setObjectName("dcap")
        el.addWidget(cap)
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch(1)
        for i, lab in enumerate(("RET 1", "RET 2")):
            key = f"ret{i + 1}"
            self.fx.setdefault(key, 1.0)
            k = Knob(lab, 0.0, 2.0, self.fx[key], 1.0, "{:.2f}")
            k.setToolTip(f"{lab}: level of what comes back into VertiMeeter:RET{i + 1}_L/R "
                         f"(joins the {'reverb' if i == 0 else 'delay'} return of every bus)")
            k.changed.connect(lambda x, kk=key, ii=i: (self.fx.__setitem__(kk, x), self.eng.send(c="ret", k=ii, gain=x)))
            row.addWidget(k)
        row.addStretch(1)
        el.addLayout(row)
        pb = QPushButton("JACK PORTS...")
        pb.setFixedHeight(22)
        pb.setToolTip("Audio map: connect EXT1/EXT2 sends and RET1/RET2 returns to Carla, an LV2 host, etc.")
        if on_ports:
            pb.clicked.connect(on_ports)
        from .plat import IS_WIN  # noqa: PLC0415
        if IS_WIN:  # EXT / RET are JACK-style ports; Windows has none, so nothing can be patched
            pb.setText("JACK PORTS (Linux)")
            pb.setEnabled(False)
            pb.setToolTip("External effect loops patch JACK / PipeWire ports, which Windows does not have.")
        el.addWidget(pb)
        v.addWidget(ex, 0, Qt.AlignmentFlag.AlignTop)
        v.addStretch(1)
        self.push()

    def set_horizontal(self, on: bool):
        """Potato layout: the three boxes sit side by side."""
        self.box.setDirection(QBoxLayout.Direction.LeftToRight if on else QBoxLayout.Direction.TopToBottom)

    def _box(self, title, on_key, edit):
        w = QFrame()
        w.setObjectName("fxbox")
        L = QVBoxLayout(w)
        L.setContentsMargins(4, 4, 4, 4)
        L.setSpacing(3)
        cap = QLabel(title)
        cap.setObjectName("dcap")
        L.addWidget(cap)
        on = QPushButton("ON" if self.fx[on_key] else "OFF")
        on.setObjectName("fxon")
        on.setCheckable(True)
        on.setChecked(self.fx[on_key])
        on.setFixedHeight(22)
        on.toggled.connect(lambda x, k=on_key, b=on: (b.setText("ON" if x else "OFF"), self._set(k, x)))
        ed = QPushButton("EDIT...")
        ed.setFixedHeight(22)
        ed.clicked.connect(edit)
        L.addWidget(on)
        L.addWidget(ed)
        return w

    def _edit_rvb(self):
        FxEdit("Reverb", self.fx, self.eng, [("ROOM", "room", 0.0, 1.0), ("DAMP", "damp", 0.0, 1.0), ("WIDTH", "width", 0.0, 1.0)], self).exec()

    def _edit_dly(self):
        tb = QPushButton("TAPS...")
        tb.setToolTip("Edit the 8 taps: time, level, pan")
        tb.clicked.connect(lambda: TapsDialog(self.fx, self.eng, self).exec())
        FxEdit("8-tap delay", self.fx, self.eng, [("FDBK", "fb", 0.0, 0.95)], self, tb).exec()

    def _set(self, key, val):
        self.fx[key] = val
        self.eng.send(c="fx", **{key: val})

    def push(self):
        f = self.fx
        self.eng.send(c="fx", rvb_on=f["rvb_on"], room=f["room"], damp=f["damp"], width=f["width"],
                      dly_on=f["dly_on"], fb=f["fb"])
        for i, t in enumerate(f["taps"]):
            self.eng.send(c="fx", tap=i, **t)
        for i in range(2):
            self.eng.send(c="ret", k=i, gain=f.get(f"ret{i + 1}", 1.0))
