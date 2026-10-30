"""MIDI mapping: turning MIDI messages into mixer controls, and the dialog to edit the mapping + the streamer view."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from . import midi, scenes
from .config import BUS_IDS

CTLS = ["Strip gain", "Strip mute", "Strip solo", "Strip mono", "Bus gain", "Bus mute", "Media volume", "Media mute",
        "Scene recall", "Macro button"]
MODES = ["absolute", "toggle", "push"]  # absolute: the knob / fader value; toggle: each press flips; push: held = on


def cc_to_db(v: int) -> float:
    """MIDI 0..127 -> -60 .. +12 dB (linear in dB, like Voicemeeter's remote gain)."""
    return -60.0 + max(0, min(127, v)) / 127.0 * 72.0


def _find(items, key):
    return next((x for x in items if x["id"] == key), None)


def apply_row(win, row: dict, val: int) -> str:
    """Do what one mapping row says for a MIDI value. Returns a short text for the status bar."""
    ctl, tgt, mode = row.get("ctl", ""), str(row.get("target", "")).strip(), row.get("mode", "absolute")
    pressed = val >= 64 if row.get("msg", "").startswith("cc") else val > 0
    if ctl in ("Scene recall", "Macro button"):
        if not pressed:
            return ""
        try:
            n = int(tgt) - 1
        except ValueError:
            return f"{ctl}: target must be a number"
        return win.scene_recall(n) if ctl == "Scene recall" else (win.run_macro(n) or "")
    if ctl in ("Strip gain", "Bus gain", "Media volume"):
        db = cc_to_db(val)
        if ctl == "Media volume":
            win.media.vol.set_value(db, True)
            return f"Media {db:+.1f} dB"
        w = (win.strip_w if ctl == "Strip gain" else win.bus_w).get(tgt)
        if w is None:
            return f"no {tgt}"
        w.fader.db = db
        w.fader.update()
        w._gain(db)
        return f"{tgt} {db:+.1f} dB"
    # buttons
    btn = None
    if ctl == "Media mute":
        btn = win.media.mute
    else:
        w = (win.strip_w if ctl.startswith("Strip") else win.bus_w).get(tgt)
        name = {"Strip mute": "mute", "Strip solo": "solo", "Strip mono": "mono", "Bus mute": "mute"}.get(ctl, "")
        btn = getattr(w, name, None) if w is not None else None
    if btn is None:
        return f"{ctl}: no {tgt or 'target'}"
    if mode == "toggle":
        if pressed:
            btn.setChecked(not btn.isChecked())
    else:
        btn.setChecked(pressed)
    return f"{tgt or ctl} {'on' if btn.isChecked() else 'off'}"


class MidiDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("MIDI mapping")
        self.setMinimumWidth(780)
        win.cfg.d.setdefault("midimap", [])
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.on = QCheckBox("Listen to MIDI devices")
        self.on.setChecked(bool(win.cfg.d.get("midi_on")))
        self.on.toggled.connect(win.set_midi)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        top.addWidget(self.on)
        top.addWidget(self.status, 1)
        lay.addLayout(top)
        self.last = QLabel("last message: -")
        self.last.setObjectName("mono")
        lay.addWidget(self.last)
        self.box = QVBoxLayout()
        self.box.setSpacing(4)
        host = QWidget()
        host.setLayout(self.box)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(host)
        sa.setMinimumHeight(240)
        lay.addWidget(sa, 1)
        bt = QHBoxLayout()
        add = QPushButton("+ ROW")
        add.clicked.connect(lambda: self._add({"ctl": CTLS[0], "target": "", "msg": "", "mode": "absolute"}, True))
        bt.addWidget(add)
        bt.addStretch(1)
        close = QPushButton("CLOSE")
        close.clicked.connect(self.accept)
        bt.addWidget(close)
        lay.addLayout(bt)
        note = QLabel("Targets: strip / bus ids (H1, V2, A1, B1), a scene or macro number, nothing for the media player. "
                      "Absolute maps the control value to -60..+12 dB. Press LEARN, then move the control on your surface. "
                      "Messages are written kind:channel:number, channel 0 = any channel.")
        note.setWordWrap(True)
        note.setObjectName("dim")
        lay.addWidget(note)
        for r in win.cfg.d["midimap"]:
            self._add(r, False)
        self.setdev()

    def refresh(self):
        self.setdev()

    def setdev(self):
        d = midi.devices()
        self.status.setText(f"{len(d)} MIDI device{'s' if len(d) != 1 else ''} found" if d else
                            "no MIDI devices found (plug in a controller; USB class-compliant devices work)")

    def show_msg(self, kind, ch, num, val):
        self.last.setText(f"last message: {midi.token(kind, ch, num)}  value {val}")

    def _add(self, row, new):
        if new:
            self.win.cfg.d["midimap"].append(row)
        w = QFrame()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        ctl = QComboBox()
        ctl.addItems(CTLS)
        ctl.setCurrentText(row.get("ctl", CTLS[0]))
        tgt = QComboBox()
        tgt.setEditable(True)
        tgt.setFixedWidth(80)
        d = self.win.cfg.d
        tgt.addItems([s["id"] for s in d["strips"]] + [b["id"] for b in d["buses"]])
        tgt.setCurrentText(str(row.get("target", "")))
        msg = QLineEdit(row.get("msg", ""))
        msg.setPlaceholderText("cc:1:7")
        msg.setFixedWidth(100)
        learn = QPushButton("LEARN")
        mode = QComboBox()
        mode.addItems(MODES)
        mode.setCurrentText(row.get("mode", "absolute"))
        x = QPushButton("×")
        x.setFixedWidth(26)
        for wdg in (ctl, tgt, msg, learn, mode, x):
            hl.addWidget(wdg)
        ctl.currentTextChanged.connect(lambda v: row.__setitem__("ctl", v))
        tgt.currentTextChanged.connect(lambda v: row.__setitem__("target", v.strip()))
        msg.textChanged.connect(lambda v: row.__setitem__("msg", v.strip()))
        mode.currentTextChanged.connect(lambda v: row.__setitem__("mode", v))
        learn.clicked.connect(lambda: self._learn(learn, msg))
        x.clicked.connect(lambda: (self.win.cfg.d["midimap"].remove(row), w.deleteLater()))
        self.box.addWidget(w)

    def _learn(self, btn, field):
        if not self.on.isChecked():
            self.on.setChecked(True)
        btn.setText("move a control...")
        self.win.midi_learn(lambda tok: (field.setText(tok), btn.setText("LEARN")))


class StreamerView(QWidget):
    """Small always-handy window: scene buttons, the macro buttons you filled in, bus mutes and two meters."""

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Window)
        self.win = win
        self.setWindowTitle("VertiMeeter - streamer view")
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        self.top = QCheckBox("always on top")
        self.top.toggled.connect(self._ontop)
        lay.addWidget(self.top)
        self.body = QVBoxLayout()
        lay.addLayout(self.body)
        self.meters: list = []
        self.rebuild()

    def _ontop(self, on):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def rebuild(self):
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        d = self.win.cfg.d
        sc = d.get("scenes", {})
        row = QHBoxLayout()
        row.addWidget(QLabel("SCENES"))
        for i in range(64):
            if str(i) in sc.get("data", {}):
                b = QPushButton(sc["names"][i] or str(i + 1))
                b.setMinimumWidth(60)
                b.clicked.connect(lambda _c, k=i: self.win.scene_recall(k))
                row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("MACROS"))
        for i, m in enumerate(d.get("macros", [])):
            if m.get("actions"):
                b = QPushButton(m["name"] or str(i + 1))
                b.setMinimumWidth(70)
                b.setFixedHeight(40)
                b.clicked.connect(lambda _c, k=i: self.win.run_macro(k))
                row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("OUTPUTS"))
        for bid, w in self.win.bus_w.items():
            b = QPushButton(f"{bid} MUTE")
            b.setCheckable(True)
            b.setObjectName("mute")
            b.setChecked(w.cfg["mute"])
            b.setFixedHeight(32)
            b.toggled.connect(lambda v, ww=w: ww.mute.setChecked(v))
            row.addWidget(b)
        row.addStretch(1)
        self.body.addLayout(row)

    def showEvent(self, e):
        self.rebuild()
        super().showEvent(e)
