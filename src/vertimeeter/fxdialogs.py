"""Strip FX dialog (voice modeler + insert FX host) and the C5 multiband compressor dialog for buses."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QVBoxLayout)

from . import fxhost, lv2scan
from .widgets import HSlider

PRESETS = {
    "Custom": None,
    "Chipmunk (+7 semitones)": (7.0, 0.0),
    "Deep voice (-6 semitones)": (-6.0, 0.0),
    "Giant (-12 semitones)": (-12.0, 0.0),
    "Robot (ring 55 Hz)": (0.0, 55.0),
    "Alien (+4, ring 28 Hz)": (4.0, 28.0),
}


def default_voice() -> dict:
    return dict(on=False, pitch=0.0, mix=1.0, ring=0.0)


def default_ins() -> dict:
    return dict(on=False, mix=1.0, uri="", cmd="", client="", launched=False)


def default_c5() -> dict:
    return dict(on=False, xo=[120.0, 500.0, 2000.0, 6000.0], thr=[-20.0] * 5, ratio=[3.0] * 5, gain=[0.0] * 5)


def send_voice(eng, slot, v):
    eng.send(c="strip", i=slot, vm_on=bool(v["on"]), vm_pitch=float(v["pitch"]), vm_mix=float(v["mix"]),
             vm_ring=float(v["ring"]))


def send_ins(eng, slot, ins):
    eng.send(c="strip", i=slot, ins_on=bool(ins["on"]), ins_mix=float(ins["mix"]))


def send_c5(eng, idx, c):
    eng.send(c="bus", i=idx, c5_on=bool(c["on"]), c5_xo=c["xo"], c5_thr=c["thr"], c5_ratio=c["ratio"], c5_gain=c["gain"])


def _cap(text):
    lab = QLabel(text)
    lab.setObjectName("cap")
    return lab


def _slider_row(label, lo, hi, val, default, fmt, on_change, width=70):
    row = QHBoxLayout()
    lab = QLabel(label)
    lab.setFixedWidth(width)
    sl = HSlider(lo, hi, val, default)
    sl.setMinimumWidth(220)
    out = QLabel(fmt(val))
    out.setObjectName("mono")
    out.setFixedWidth(78)
    out.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def ch(v):
        out.setText(fmt(v))
        on_change(v)
    sl.changed.connect(ch)
    row.addWidget(lab)
    row.addWidget(sl, 1)
    row.addWidget(out)
    return row, sl, out


class StripFxDialog(QDialog):
    def __init__(self, cfg: dict, eng, parent=None):
        super().__init__(parent)
        self.cfg, self.eng = cfg, eng
        self.setWindowTitle(f"FX - {cfg['label']}")
        self.setMinimumWidth(520)
        cfg.setdefault("voice", default_voice())
        cfg.setdefault("ins", default_ins())
        self.v, self.ins = cfg["voice"], cfg["ins"]
        lay = QVBoxLayout(self)
        lay.addWidget(_cap("VOICE MODELER"))
        box = QFrame()
        box.setObjectName("fxbox")
        bl = QVBoxLayout(box)
        top = QHBoxLayout()
        self.v_on = QPushButton("ON")
        self.v_on.setCheckable(True)
        self.v_on.setChecked(self.v["on"])
        self.v_on.toggled.connect(lambda x: self._v("on", x))
        self.preset = QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.activated.connect(self._preset)
        top.addWidget(self.v_on)
        top.addWidget(QLabel("Preset"))
        top.addWidget(self.preset, 1)
        bl.addLayout(top)
        r, self.s_pitch, _ = _slider_row("PITCH", -12, 12, self.v["pitch"], 0.0, lambda x: f"{x:+.1f} st", lambda x: self._v("pitch", round(x, 1)))
        bl.addLayout(r)
        r, self.s_ring, _ = _slider_row("RING", 0, 400, self.v["ring"], 0.0, lambda x: "off" if x < 1 else f"{x:.0f} Hz", lambda x: self._v("ring", round(x)))
        bl.addLayout(r)
        r, self.s_mix, _ = _slider_row("MIX", 0, 100, self.v["mix"] * 100, 100.0, lambda x: f"{x:.0f} %", lambda x: self._v("mix", round(x) / 100))
        bl.addLayout(r)
        note = QLabel("Pitch shifting changes the pitch only (chipmunk / deep voice, not a natural voice change) and adds "
                      "about 20 ms of delay to this strip while it is on. Ring modulation makes robot voices.")
        note.setWordWrap(True)
        note.setObjectName("dim")
        bl.addWidget(note)
        lay.addWidget(box)

        lay.addWidget(_cap("INSERT FX (plugin host)"))
        box2 = QFrame()
        box2.setObjectName("fxbox")
        b2 = QVBoxLayout(box2)
        t2 = QHBoxLayout()
        self.i_on = QPushButton("INSERT ON")
        self.i_on.setCheckable(True)
        self.i_on.setChecked(self.ins["on"])
        self.i_on.toggled.connect(self._ins_on)
        t2.addWidget(self.i_on)
        t2.addStretch(1)
        b2.addLayout(t2)
        r, self.s_imix, _ = _slider_row("WET", 0, 100, self.ins["mix"] * 100, 100.0, lambda x: f"{x:.0f} %", lambda x: self._i("mix", round(x) / 100))
        b2.addLayout(r)
        self.fx_combo = QComboBox()
        self.fx_combo.addItem("Custom command (any JACK / PipeWire effect)", "")
        self.plugins = lv2scan.scan()
        for name, uri, _b in self.plugins:
            self.fx_combo.addItem(f"LV2: {name}", uri)
        idx = self.fx_combo.findData(self.ins.get("uri", ""))
        self.fx_combo.setCurrentIndex(max(0, idx))
        self.fx_combo.currentIndexChanged.connect(self._pick)
        e = QHBoxLayout()
        e.addWidget(QLabel("Effect"))
        e.addWidget(self.fx_combo, 1)
        b2.addLayout(e)
        self.cmd = QLineEdit(self.ins.get("cmd", ""))
        self.cmd.setPlaceholderText("command to start the effect, e.g.  carla --no-gui   or   my-effect --jack")
        self.cmd.editingFinished.connect(lambda: self._i("cmd", self.cmd.text().strip()))
        b2.addWidget(self.cmd)
        self.client = QLineEdit(self.ins.get("client", ""))
        self.client.setPlaceholderText("JACK client name of the effect (leave empty: the new client is found automatically)")
        self.client.editingFinished.connect(lambda: self._i("client", self.client.text().strip()))
        b2.addWidget(self.client)
        bt = QHBoxLayout()
        self.go = QPushButton("LAUNCH")
        self.go.clicked.connect(self._launch)
        self.stop = QPushButton("STOP")
        self.stop.clicked.connect(self._stop)
        bt.addWidget(self.go)
        bt.addWidget(self.stop)
        bt.addStretch(1)
        b2.addLayout(bt)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        b2.addWidget(self.status)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("dim")
        self.hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b2.addWidget(self.hint)
        lay.addWidget(box2)
        ok = QPushButton("CLOSE")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        self._pick()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(500)
        self._poll()

    # ---- voice
    def _v(self, k, val):
        self.v[k] = val
        send_voice(self.eng, self.cfg["slot"], self.v)

    def _preset(self, _i):
        p = PRESETS[self.preset.currentText()]
        if p is None:
            return
        self.s_pitch.set_value(p[0], True)
        self.s_ring.set_value(p[1], True)
        self.s_mix.set_value(100.0, True)
        self.v_on.setChecked(True)

    # ---- insert
    def _i(self, k, val):
        self.ins[k] = val
        send_ins(self.eng, self.cfg["slot"], self.ins)

    def _key(self):
        return self.cfg["id"]

    def _pick(self):
        uri = self.fx_combo.currentData() or ""
        self.ins["uri"] = uri
        self.cmd.setVisible(not uri)
        self.client.setVisible(not uri)
        if uri and not fxhost.find_jalv():
            self.hint.setText("Hosting LV2 plugins needs jalv. Install it with:  sudo pacman -S jalv")
        elif not self.plugins and not uri:
            self.hint.setText("No LV2 plugins found. Install some (for example  sudo pacman -S jalv calf lsp-plugins) "
                              "and reopen this window, or use a custom command.")
        else:
            self.hint.setText("")

    def _launch(self):
        uri = self.fx_combo.currentData() or ""
        client = f"VMFX_{self.cfg['id']}"
        if uri:
            argv = fxhost.lv2_command(uri, client)
            if not argv:
                self.status.setText("jalv is not installed (sudo pacman -S jalv)")
                return
        else:
            argv, client = fxhost.split_cmd(self.cmd.text().strip()), self.client.text().strip()
        self.ins["launched"] = fxhost.host().launch(self._key(), self.cfg["slot"], argv, client)

    def _stop(self):
        self.i_on.setChecked(False)
        fxhost.host().stop(self._key())
        self.ins["launched"] = False

    def _ins_on(self, on):
        if on and not fxhost.host().alive(self._key()):
            if QMessageBox.question(self, "Insert FX", "No effect started from here is running.\n\nThe strip stays silent "
                                    "while the insert is on unless something sends audio back to it (INS"
                                    f"{self.cfg['slot'] + 1}_IN).\n\nTurn it on anyway?") != QMessageBox.StandardButton.Yes:
                self.i_on.blockSignals(True)
                self.i_on.setChecked(False)
                self.i_on.blockSignals(False)
                return
        self._i("on", on)

    def _poll(self):
        h = fxhost.host()
        self.status.setText(h.status.get(self._key(), "not running"))
        alive = h.alive(self._key())
        self.go.setEnabled(not alive)
        self.stop.setEnabled(alive)
        if not alive and self.i_on.isChecked() and self.ins.get("launched"):
            self.i_on.setChecked(False)  # the effect died: never leave the strip silent

    def done(self, r):
        self.timer.stop()
        super().done(r)


class C5Dialog(QDialog):
    NAMES = ["LOW", "LOW-MID", "MID", "HIGH-MID", "HIGH"]

    def __init__(self, cfg: dict, idx: int, eng, parent=None):
        super().__init__(parent)
        self.cfg, self.idx, self.eng = cfg, idx, eng
        self.setWindowTitle(f"C5 multiband compressor - {cfg['id']}")
        c = cfg.setdefault("c5", default_c5())
        self.c = c
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.on = QPushButton("C5 ON")
        self.on.setCheckable(True)
        self.on.setChecked(c["on"])
        self.on.toggled.connect(lambda x: self._set("on", x))
        reset = QPushButton("RESET")
        reset.clicked.connect(self._reset)
        top.addWidget(self.on)
        top.addWidget(reset)
        top.addStretch(1)
        lay.addLayout(top)
        g = QGridLayout()
        g.setSpacing(6)
        self.xo = []
        g.addWidget(QLabel("crossover Hz"), 1, 0)
        for k in range(4):
            sp = QDoubleSpinBox()
            sp.setRange(40, 16000)
            sp.setDecimals(0)
            sp.setValue(c["xo"][k])
            sp.valueChanged.connect(lambda v, kk=k: self._xo(kk, v))
            g.addWidget(sp, 1, k + 1, Qt.AlignmentFlag.AlignRight)
            self.xo.append(sp)
        self.sl = {}
        for b, nm in enumerate(self.NAMES):
            g.addWidget(_cap(nm), 0, b + 1 if b < 4 else 5)
        rows = (("thr", "threshold", -60, 0, -20.0, lambda x: f"{x:.0f} dB"),
                ("ratio", "ratio", 1, 20, 3.0, lambda x: f"{x:.1f}:1"),
                ("gain", "makeup", -12, 12, 0.0, lambda x: f"{x:+.1f} dB"))
        for r, (key, label, lo, hi, dflt, fmt) in enumerate(rows):
            g.addWidget(QLabel(label), 2 + r * 2, 0)
            for b in range(5):
                row, sl, _o = _slider_row("", lo, hi, c[key][b], dflt, fmt, lambda v, kk=key, bb=b: self._arr(kk, bb, v), width=0)
                w = QFrame()
                w.setLayout(row)
                row.setContentsMargins(0, 0, 0, 0)
                g.addWidget(w, 2 + r * 2, b + 1)
                self.sl[(key, b)] = sl
        lay.addLayout(g)
        note = QLabel("Five bands, each with its own compressor. The low bands run up to the first crossover, the high band "
                      "from the last one. With every ratio at 1:1 the bands add back to the original signal. "
                      "Applies to this bus's output.")
        note.setWordWrap(True)
        note.setObjectName("dim")
        lay.addWidget(note)
        ok = QPushButton("CLOSE")
        ok.clicked.connect(self.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)

    def _push(self):
        send_c5(self.eng, self.idx, self.c)

    def _set(self, k, v):
        self.c[k] = v
        self._push()

    def _xo(self, k, v):
        self.c["xo"][k] = float(v)
        self.c["xo"] = sorted(self.c["xo"])
        self._push()

    def _arr(self, key, b, v):
        self.c[key][b] = round(float(v), 1)
        self._push()

    def _reset(self):
        d = default_c5()
        d["on"] = self.c["on"]
        self.c.update(d)
        for k, sp in enumerate(self.xo):
            sp.setValue(d["xo"][k])
        for (key, b), sl in self.sl.items():
            sl.set_value(d[key][b], True)
        self._push()
