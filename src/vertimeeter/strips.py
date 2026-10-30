"""Strip and bus widgets (one column each). Pixel layout follows mockup 5 (strip 136 px, bus 98 px)."""
from __future__ import annotations

import copy
import ipaddress
import math
import re
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QMenu, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from . import pwutil, theme
from .plat import IS_WIN
from .config import BUS_IDS, net_feeds, out_matrix, row_key, row_label, rows_of
from .dialogs import DynDialog, PeqDialog, default_dyn, default_eq
from .mapping import comp_cmd, gate_cmd, tone_cmd
from . import fxdialogs
from .fxhost import host as _fxhost
from .widgets import ClickLabel, Fader, Knob, Meter, Pad

W = 158   # strip width in pixels (mockup 7: routing buttons are two columns, A left / B right)
BW = 98   # bus width
BUS_MODES = ["Normal", "Mono", "Swap L/R", "Invert L", "Invert R", "Left only", "Right only", "Side only",
             "Phase flip", "Side (mono)", "LFE only", "High-pass 120", "Widen", "Narrow"]
KAR_MODES = ["K off", "K1 vocal cut", "K2 cut + bass"]
FT = "{:+.1f}"


def _btn(text, name="", checked=False, tip="", h=18):
    b = QPushButton(text)
    b.setCheckable(True)
    b.setChecked(checked)
    if name:
        b.setObjectName(name)
    if tip:
        b.setToolTip(tip)
    if h:
        b.setFixedHeight(h)
    return b


def _db_text(v: float) -> str:
    if v <= 1e-4:
        return "-inf"
    return f"{20 * math.log10(v):+.1f}"


def _knob_row(*knobs, gap=1):
    r = QHBoxLayout()
    r.setSpacing(gap)
    r.setContentsMargins(0, 0, 0, 0)
    r.addStretch(1)
    for k in knobs:
        r.addWidget(k)
    r.addStretch(1)
    return r


def _hrow(*widgets, spacing=2):
    r = QHBoxLayout()
    r.setSpacing(spacing)
    r.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        r.addWidget(w, 1)
    return r


def _live_header(n, noun):
    return f"●  LIVE   {n} {noun} found   scanned {time.strftime('%H:%M:%S')}"


class DevBox(ClickLabel):
    """Device selector drawn like the mockup .dev box. Click opens a menu that is read live."""

    def __init__(self, height):
        super().__init__("")
        self.setObjectName("dev")
        self.setFixedHeight(height)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setProperty("info", False)

    def show_device(self, dev: str, names: dict[str, str], noun: str):
        t = theme.cur()
        if not dev:
            self.setText(f"<span style='color:{t['red']}'><b>NO DEVICE</b></span><br><small style='color:{t['dim']}'>click to pick {noun}</small>")
            self.setToolTip(f"No {noun} device selected. Click to choose (list reads live).")
        elif dev in names:
            self.setText(names[dev])
            self.setToolTip(names[dev])
        else:
            self.setText(f"<span style='color:{t['amber']}'><b>MISSING</b></span><br><small style='color:{t['dim']}'>{dev[:22]}</small>")
            self.setToolTip(f"{dev} is not connected. It re-links when it comes back.")

    def info(self, html: str, tip: str = ""):
        self.setProperty("info", True)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(html)
        self.setToolTip(tip)


def pick_device(anchor: QWidget, live_fn, current: str, on_pick, noun: str) -> None:
    items = live_fn()  # read right now
    m = QMenu(anchor)
    h = m.addAction(_live_header(len(items), noun))
    h.setEnabled(False)
    m.addAction("(none)").triggered.connect(lambda: on_pick(""))
    m.addSeparator()
    for n, d in items:
        a = m.addAction(d)
        a.setCheckable(True)
        a.setChecked(n == current)
        a.triggered.connect(lambda _c, nn=n: on_pick(nn))
    m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))


class StripBase(QFrame):
    removed = pyqtSignal(str)
    changed = pyqtSignal()
    nameClicked = pyqtSignal(str)
    renamed = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, cfg: dict, eng, width: int = W):
        super().__init__()
        self.cfg, self.eng = cfg, eng
        for k, v in (("post", False), ("lim", False), ("lim_db", -6.0), ("kar", 0), ("mc", False),
                     ("col_x", 0.0), ("col_y", 0.0)):
            cfg.setdefault(k, v)
        for k in ("ext1", "ext2"):
            cfg["knobs"].setdefault(k, 0.0)
        self.setObjectName("strip")
        self.setFixedWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(5, 5, 5, 5)
        self.lay.setSpacing(3)
        self.name = ClickLabel(cfg["label"].upper())
        self.name.setObjectName("sname")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setWordWrap(True)
        self.name.setFixedHeight(28)
        self.name.setToolTip("Click to select (then [ - ] removes it). Double-click to rename.")
        self.name.clicked.connect(lambda: self.nameClicked.emit(self.cfg["id"]))
        self.name.doubleClicked.connect(self.rename)
        self.name.context.connect(self._name_menu)
        self.lay.addWidget(self.name)
        self.fader = Fader(cfg["gain_db"])
        self.meter = Meter()
        self.fader.changed.connect(self._gain)
        self.readout = QLabel(self._gain_text(cfg["gain_db"]))
        self.readout.setObjectName("readout")
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.readout.setFixedHeight(19)
        self.mute = _btn("MUTE", "mute", cfg["mute"])
        self.solo = _btn("SOLO", "solo", cfg["solo"])
        self.mono = _btn("MONO", "mono", cfg["mono"])
        self.mute.toggled.connect(lambda v: self._flag("mute", v))
        self.solo.toggled.connect(lambda v: self._flag("solo", v))
        self.mono.toggled.connect(lambda v: self._flag("mono", v))
        self.route_col = QGridLayout()
        self.route_col.setHorizontalSpacing(2)
        self.route_col.setVerticalSpacing(2)
        self.route_col.setContentsMargins(0, 0, 0, 0)
        self.routes: dict[str, QPushButton] = {}
        self.knob_w: dict[str, Knob] = {}
        self._ticks = 0

    # ---- naming ----------------------------------------------------------
    def set_selected(self, on: bool) -> None:
        self.setProperty("sel", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def rename(self):
        t, ok = QInputDialog.getText(self, "Rename strip", "Name:", text=self.cfg["label"])
        if ok and t.strip():
            self.cfg["label"] = t.strip()[:24]
            self.name.setText(self.cfg["label"].upper())
            self.renamed.emit(self.cfg["id"])

    def _name_menu(self):
        m = QMenu(self)
        m.addAction("Rename...").triggered.connect(self.rename)
        m.exec(self.name.mapToGlobal(self.name.rect().bottomLeft()))

    # ---- shared pieces ---------------------------------------------------
    def build_routes(self, bus_ids: list[str]) -> None:
        while self.route_col.count():
            it = self.route_col.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.routes = {}
        for b in bus_ids:  # A buses in the left column, B buses in the right one (8 rows max each)
            on = self.cfg["sends"].get(b, False)
            btn = _btn(b, "rtB" if b.startswith("B") else "rt", on, f"Send this strip to {b}", h=20)
            btn.setFixedWidth(26)
            btn.toggled.connect(lambda v, bid=b: self._route(bid, v))
            self.routes[b] = btn
            self.route_col.addWidget(btn, int(b[1:]) - 1, 0 if b.startswith("A") else 1)
        self.route_col.setRowStretch(8, 1)

    def add_tail(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(3)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.fader)
        row.addWidget(self.meter)
        row.addLayout(self.route_col)
        row.addStretch(1)
        host = QWidget()
        host.setMinimumHeight(130)
        host.setLayout(row)
        self.lay.addWidget(host, 1)
        self.lay.addWidget(self.readout)
        for b in (self.mono, self.solo, self.mute):
            self.lay.addWidget(b)

    def make_knob(self, key, label, lo=0.0, hi=10.0, default=0.0, on_ctx=None, fmt="{:.1f}", bip=None):
        k = Knob(label, lo, hi, self.cfg["knobs"].get(key, default), default, fmt, bip)

        def ch(v, kk=key):
            self.cfg["knobs"][kk] = v
            self._send_knob(kk, v)

        k.changed.connect(ch)
        if on_ctx:
            k.context.connect(on_ctx)
            k.setToolTip(k.toolTip() + "\nRight-click: detailed parameters")
        self.knob_w[key] = k
        return k

    def make_pan_knob(self):
        k = Knob("PAN", -1.0, 1.0, self.cfg["pan"], 0.0, FT, True)
        k.changed.connect(self._pan_knob)
        self.pan_knob = k
        return k

    def make_ext_row(self):
        """EXT1 / EXT2 sends to external FX (JACK ports EXT1_*/EXT2_*), POST fader, strip limiter."""
        post = _btn("POST", "post", self.cfg["post"], "Send knobs: after the fader (off = before the fader)", h=20)
        post.toggled.connect(lambda v: self._flag2("post", v, ext_post=v))
        lim = _btn("LIM off" if not self.cfg["lim"] else f"LIM {self.cfg['lim_db']:.0f}", "lim", self.cfg["lim"],
                   "Peak limiter on this strip.\nClick: on/off.  Right-click: set the ceiling (dB).", h=20)
        lim.toggled.connect(self._lim_toggled)
        lim.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lim.customContextMenuRequested.connect(lambda _p: self._lim_dialog())
        self.lim_btn = lim
        col = QVBoxLayout()
        col.setSpacing(3)
        col.setContentsMargins(2, 10, 2, 0)
        col.addWidget(post)
        col.addWidget(lim)
        col.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(1)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.make_knob("ext1", "EXT 1"))
        row.addWidget(self.make_knob("ext2", "EXT 2"))
        cw = QWidget()
        cw.setFixedWidth(40)
        cw.setLayout(col)
        row.addWidget(cw)
        row.addStretch(1)
        return row

    def make_fx_row(self):
        b = _btn("FX", "", False, "Voice modeler (pitch / robot) and insert FX: host any LV2 plugin or JACK effect in this strip", h=18)
        b.setCheckable(False)
        b.setStyleSheet("font-size: 10px; padding: 0;")
        b.clicked.connect(self._open_fx)
        self.fx_btn = b
        self._fx_mark()
        return b

    def _fx_mark(self):
        v, i = self.cfg.get("voice", {}), self.cfg.get("ins", {})
        self.fx_btn.setText("FX *" if (v.get("on") or i.get("on")) else "FX")

    def _open_fx(self):
        fxdialogs.StripFxDialog(self.cfg, self.eng, self).exec()
        self._fx_mark()

    def make_pad(self, modes, kind, default_mode):
        c = self.cfg
        mode = c.get("sp_mode", default_mode)
        if mode not in modes:
            mode = default_mode
        pad = Pad(0.0, 1.0, mode, modes, kind)
        self._pad_load(pad, mode)
        pad.changed.connect(self._pad)
        pad.modeChanged.connect(self._pad_mode)
        self.pad = pad
        return pad

    def _pad_load(self, pad, mode):
        c = self.cfg
        if mode == "color":
            pad.set_state(c.get("col_x", 0.0), c.get("col_y", 0.0), mode)
        elif mode == "mod":
            pad.set_state(c.get("mod_x", 0.0), c.get("mod_y", -1.0), mode)
        else:
            pad.set_state(c.get("sp_x", c["pan"]), c.get("sp_y", 1.0) if mode == "3d" else 1.0, mode)

    def _pad_mode(self, m):
        self.cfg["sp_mode"] = m
        self._pad_load(self.pad, m)
        self._send_pad()

    def _pad(self, x, y):
        c = self.cfg
        if self.pad.mode == "color":
            c["col_x"], c["col_y"] = x, y
            self._send_color()
            return
        if self.pad.mode == "mod":
            c["mod_x"], c["mod_y"] = x, y
            self._send_mod()
            return
        c["sp_x"], c["sp_y"] = x, y
        c["pan"] = x
        if hasattr(self, "pan_knob"):
            self.pan_knob.set_value(x)
        self._send_pad()

    def _pan_knob(self, v):
        c = self.cfg
        c["pan"] = v
        c["sp_x"] = v
        if self.pad.mode == "pan":
            self.pad.set_state(v, 1.0, "pan")
        self._send_pad()

    def _send_pad(self):
        c = self.cfg
        on = c.get("sp_mode", "pan") == "3d"
        self.eng.send(c="strip", i=c["slot"], sp_on=on, sp_x=c.get("sp_x", c["pan"]), sp_y=c.get("sp_y", 1.0), pan=c["pan"])
        self._send_mod()

    def _send_mod(self):
        """MODULATION pad: x = tremolo rate 0.5..12 Hz, y = depth (bottom = off). Only active while the pad is in that mode."""
        c = self.cfg
        depth = (c.get("mod_y", -1.0) + 1.0) / 2.0
        on = c.get("sp_mode", "pan") == "mod" and depth > 0.03
        rate = 0.5 + (c.get("mod_x", 0.0) + 1.0) / 2.0 * 11.5
        self.eng.send(c="strip", i=c["slot"], mod_on=on, mod_rate=rate, mod_depth=depth)

    def _send_color(self):
        """COLOR pad (hardware strips): x = dark..bright (bass down / treble up), y = presence (mid)."""
        c = self.cfg
        x, y, slot = c.get("col_x", 0.0), c.get("col_y", 0.0), c["slot"]
        for key, v in (("bass", -x * 10.0), ("treb", x * 10.0), ("mid", y * 6.0)):
            self.eng.send(**tone_cmd("strip", slot, key, v))

    def _lim_toggled(self, on):
        self.cfg["lim"] = on
        self.lim_btn.setText(f"LIM {self.cfg['lim_db']:.0f}" if on else "LIM off")
        self.eng.send(c="strip", i=self.cfg["slot"], lim_on=on, lim_db=self.cfg["lim_db"])

    def _lim_dialog(self):
        v, ok = QInputDialog.getDouble(self, "Strip limiter", "Ceiling (dB, -40 .. 0):", self.cfg["lim_db"], -40.0, 0.0, 1)
        if ok:
            self.cfg["lim_db"] = v
            self.lim_btn.setChecked(True)
            self._lim_toggled(True)

    def open_peq(self):
        eq = self.cfg.setdefault("eq", default_eq())
        slot = self.cfg["slot"]
        PeqDialog(f"Parametric EQ - {self.cfg['label']}", eq,
                  lambda i, **kw: self.eng.send(c="eq", t="strip", i=slot, cell=i, **kw), self).exec()

    def open_dyn(self):
        d = self.cfg.setdefault("dyn", default_dyn())
        slot = self.cfg["slot"]
        DynDialog(f"Comp / Gate - {self.cfg['label']}", d, lambda k, **kw: self.eng.send(c=k, i=slot, **kw), self).exec()

    def _send_knob(self, key, v):
        slot = self.cfg["slot"]
        if key == "comp":
            cmd = comp_cmd(slot, v)
            self.cfg.setdefault("dyn", default_dyn())["comp"].update({k: x for k, x in cmd.items() if k not in ("c", "i")})
            self.eng.send(**cmd)
        elif key == "gate":
            cmd = gate_cmd(slot, v)
            self.cfg.setdefault("dyn", default_dyn())["gate"].update({k: x for k, x in cmd.items() if k not in ("c", "i")})
            self.eng.send(**cmd)
        elif key == "dns":
            self.eng.send(c="strip", i=slot, dns=v / 10.0)
        elif key in ("rvb", "dly", "ext1", "ext2"):
            self.eng.send(c="strip", i=slot, **{key: v / 10.0})
        elif key in ("bass", "mid", "treb") and self.cfg["kind"] != "hw":
            self.eng.send(**tone_cmd("strip", slot, key, v))  # hardware strips drive these from the COLOR pad

    # ---- engine sync -----------------------------------------------------
    def push(self) -> None:
        c = self.cfg
        self.eng.send(c="strip", i=c["slot"], active=True, gain_db=c["gain_db"], pan=c["pan"],
                      mute=c["mute"], solo=c["solo"], mono=c["mono"], ext_post=c["post"],
                      lim_on=c["lim"], lim_db=c["lim_db"], kar=self._kar_value(),
                      sends=[bool(c["sends"].get(b, False)) for b in BUS_IDS])
        self._send_pad()
        if c.get("voice"):
            fxdialogs.send_voice(self.eng, c["slot"], c["voice"])
        if c.get("ins"):
            ins = dict(c["ins"])
            ins["on"] = bool(ins["on"]) and _fxhost().alive(c["id"])  # a dead / not started effect must never leave the strip silent
            fxdialogs.send_ins(self.eng, c["slot"], ins)
        for k, v in c["knobs"].items():
            self._send_knob(k, v)
        if c["kind"] == "hw":
            self._send_color()
        for i, cell in enumerate(c.get("eq", [])):
            self.eng.send(c="eq", t="strip", i=c["slot"], cell=i, **cell)
        for k, d in c.get("dyn", {}).items():
            self.eng.send(c=k, i=c["slot"], **d)

    def _kar_value(self):
        return 3 if self.cfg.get("mc") else int(self.cfg.get("kar", 0))

    @staticmethod
    def _gain_text(db):
        return "-inf" if db <= -59.9 else f"{db:+.1f} dB"

    def _gain(self, db):
        self.cfg["gain_db"] = db
        self.readout.setText(self._gain_text(db))
        self.eng.send(c="strip", i=self.cfg["slot"], gain_db=db)

    def _flag(self, k, v):
        self.cfg[k] = v
        self.eng.send(c="strip", i=self.cfg["slot"], **{k: v})

    def _flag2(self, k, v, **eng_kw):
        self.cfg[k] = v
        self.eng.send(c="strip", i=self.cfg["slot"], **eng_kw)

    def _route(self, bid, v):
        self.cfg["sends"][bid] = v
        self.eng.send(c="strip", i=self.cfg["slot"], sends=[bool(self.cfg["sends"].get(b, False)) for b in BUS_IDS])

    def update_meter(self, lr):
        self.meter.set_levels(lr[0], lr[1])


class HwStrip(StripBase):
    deviceChanged = pyqtSignal(str)

    def __init__(self, cfg, eng):
        super().__init__(cfg, eng)
        self.devs: list[tuple[str, str]] = []
        self.dev = DevBox(40)
        self.dev.clicked.connect(self._pick)
        self.lay.addWidget(self.dev)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        ip = QLabel("INTELLIPAN")
        ip.setObjectName("iplab")
        eq = QPushButton("EQ")
        eq.setObjectName("chip")
        eq.setFixedHeight(14)
        eq.setToolTip("6-cell parametric EQ")
        eq.clicked.connect(self.open_peq)
        head.addWidget(ip, 1)
        head.addWidget(eq)
        hw = QWidget()
        hw.setFixedHeight(14)
        hw.setLayout(head)
        self.lay.addWidget(hw)
        pad = self.make_pad(("color", "3d", "mod"), "ip", "color")
        self.lay.addWidget(pad, 0, Qt.AlignmentFlag.AlignHCenter)
        self.lay.addLayout(_knob_row(*(self.make_knob(k, lab, on_ctx=self.open_dyn if k != "dns" else None)
                                       for k, lab in (("comp", "COMP"), ("gate", "GATE"), ("dns", "DNS")))))
        self.lay.addLayout(_knob_row(self.make_pan_knob(), self.make_knob("rvb", "RVB"), self.make_knob("dly", "DLY")))
        self.lay.addLayout(self.make_ext_row())
        self.lay.addWidget(self.make_fx_row())
        self.stat = QFrame()
        self.stat.setObjectName("hwstat")
        self.stat.setFixedHeight(76)
        sl = QVBoxLayout(self.stat)
        sl.setContentsMargins(0, 0, 0, 0)
        self.stat_l = QLabel("")
        self.stat_l.setTextFormat(Qt.TextFormat.RichText)
        self.stat_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.stat_l)
        self.lay.addWidget(self.stat)
        self.add_tail()
        self.refresh()

    def set_devices(self, devs: list[tuple[str, str]]) -> None:
        self.devs = devs
        self.refresh()

    def refresh(self):
        self.dev.show_device(self.cfg["device"], dict(self.devs), "input")

    def _pick(self):
        pick_device(self.dev, pwutil.list_sources, self.cfg["device"], self._set_dev, "inputs")

    def _set_dev(self, name: str):
        self.cfg["device"] = name
        self.refresh()
        self.deviceChanged.emit(name)

    def update_meter(self, lr):
        super().update_meter(lr)
        self._ticks += 1
        if self._ticks % 8:
            return
        t = theme.cur()
        dev = self.cfg["device"]
        if not dev:
            self.stat_l.setText(f"<span style='color:{t['red']}'><b>NO DEVICE</b></span><br><span style='color:{t['dim']}'>pick one above</span>")
        elif dev not in dict(self.devs):
            self.stat_l.setText(f"<span style='color:{t['amber']}'><b>MISSING</b></span><br><span style='color:{t['dim']}'>will re-link when<br>it comes back</span>")
        else:
            pk = self.meter.peak_db()
            live = pk > -50
            self.stat_l.setText(
                f"<span style='color:{t['accent'] if live else t['dim']}'>{'●' if live else '○'}</span> {'SIGNAL' if live else 'silent'}<br>"
                f"<span style='font-size:14px;color:{t['accenthi']}'><b>{'-inf' if pk <= -89 else f'{pk:+.1f}'}</b></span><br>"
                f"<span style='color:{t['dim']}'>dB peak</span>")


class VirtStrip(StripBase):
    peers: list = []  # [(sink node name, label)] of all virtual strips, set by the main window
    deviceChanged = pyqtSignal(str)

    def __init__(self, cfg, eng):
        super().__init__(cfg, eng)
        self.inputs: list = []
        self.devs: list[tuple[str, str]] = []
        self.dev = DevBox(40)
        if self._win_dev():
            self.dev.clicked.connect(self._pick_win)
        self.lay.addWidget(self.dev)
        eqh = QLabel("EQUALIZER")
        eqh.setObjectName("iplab")
        eqh.setFixedHeight(14)
        self.lay.addWidget(eqh)
        self.lay.addLayout(_knob_row(*(self.make_knob(k, lab, -12.0, 12.0, 0.0, fmt=FT)
                                       for k, lab in (("bass", "BASS"), ("mid", "MID"), ("treb", "TREB")))))
        self.lay.addWidget(self.make_pad(("pan", "3d", "mod"), "pan", "pan"), 0, Qt.AlignmentFlag.AlignHCenter)
        mc = _btn("M.C.", "", self.cfg["mc"], "Center only (mono centre channel of the signal)", h=18)
        mc.setFixedWidth(46)
        mc.toggled.connect(self._mc)
        self.kar = QComboBox()
        self.kar.addItems(KAR_MODES)
        self.kar.setCurrentIndex(min(2, int(self.cfg["kar"])))
        self.kar.setToolTip("Karaoke: K1 removes the centre (vocals), K2 removes it but keeps the bass")
        self.kar.setFixedHeight(18)
        self.kar.currentIndexChanged.connect(self._kar)
        r = QHBoxLayout()
        r.setSpacing(3)
        r.setContentsMargins(0, 0, 0, 0)
        r.addStretch(1)
        r.addWidget(mc)
        r.addWidget(self.kar)
        r.addStretch(1)
        self.lay.addLayout(r)
        self.lay.addLayout(_knob_row(self.make_knob("rvb", "RVB"), self.make_knob("dly", "DLY"), self.make_pan_knob()))
        self.lay.addLayout(self.make_ext_row())
        self.lay.addWidget(self.make_fx_row())
        self.slot = QWidget()
        self.slot.setFixedHeight(76)
        self.slot_l = QVBoxLayout(self.slot)
        self.slot_l.setContentsMargins(0, 0, 0, 0)
        self.slot_l.setSpacing(1)
        self.lay.addWidget(self.slot)
        self.extra_block()
        self.add_tail()
        self.refresh_names()

    # ---- cable naming + "what plays into me" tooltip ------------------------
    def sink_name(self):
        return f"VertiMeeter_{self.cfg['id']}"

    def desc(self):
        return f"VertiMeeter {self.cfg['label']}"

    # ---- Windows: a virtual strip captures a device (a capture device or "Loopback: <playback device>") ----
    def _win_dev(self) -> bool:
        return IS_WIN and self.cfg["kind"] == "virt"

    def _pick_win(self):
        pick_device(self.dev, pwutil.list_sources, self.cfg["device"], self._set_dev_win, "inputs (capture / loopback)")

    def _set_dev_win(self, name: str):
        self.cfg["device"] = name
        self.refresh_names()
        self.deviceChanged.emit(name)

    def set_devices(self, devs: list[tuple[str, str]]) -> None:
        self.devs = devs
        self.refresh_names()

    def refresh_names(self):
        if self._win_dev():
            self.dev.show_device(self.cfg["device"], dict(self.devs), "a capture or loopback device")
            self.dev.setToolTip((self.dev.toolTip() + "\n\n" if self.dev.toolTip() else "") +
                                "Loopback: <device> records what that playback device plays.\n"
                                "Windows cannot create virtual cables without a driver: send an app to a cable device in\n"
                                "Settings > Sound > Volume mixer, then pick that cable's capture side (or its loopback) here.")
            return
        self.dev.info(self.desc().replace("VertiMeeter ", "VertiMeeter<br>", 1) if len(self.desc()) > 26 else self.desc(),
                      self._cable_tip())

    def _cable_tip(self):
        here = [i["app"] for i in self.inputs if i["sink"] == self.sink_name()]
        head = f"Virtual cable: '{self.desc()}'\nIn your apps / system sound settings pick this as the output device.\n"
        body = "Playing into it right now:\n" + "\n".join("  ♪ " + a for a in here) if here else "Nothing is playing into it right now."
        return head + body

    def rename(self):
        super().rename()
        self.refresh_names()

    def set_apps(self, inputs: list) -> None:
        self.inputs = inputs
        tip = self._cable_tip()
        self.name.setToolTip(tip + "\n\nClick to select (then [ - ] removes it). Double-click to rename.")
        self.dev.setToolTip(tip)
        self._fill_slot([i for i in inputs if i["sink"] == self.sink_name()])

    def _fill_slot(self, here):
        if self._win_dev():
            while self.slot_l.count():
                it = self.slot_l.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            hint = QLabel("capture / loopback<br>device above")
            hint.setObjectName("dim")
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setTextFormat(Qt.TextFormat.RichText)
            self.slot_l.addWidget(hint)
            self.slot_l.addStretch(1)
            return
        while self.slot_l.count():
            it = self.slot_l.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i in here[:3]:
            lab = ClickLabel("♪ " + i["app"])
            lab.setObjectName("app")
            lab.setFixedHeight(15)
            lab.setToolTip(i["app"] + "\nClick: move to another input / output\nRight-click or scroll: this app's own volume")
            lab.clicked.connect(lambda ix=i["index"], a=i["app"], w=lab: self._move_menu(w, ix, a))
            lab.context.connect(lambda ix=i["index"], a=i["app"], w=lab: self._app_vol_menu(w, ix, a))
            lab.wheelEvent = lambda e, ix=i["index"], w=lab: self._app_wheel(w, ix, e)
            self.slot_l.addWidget(lab)
        add = ClickLabel("+ APP")
        add.setObjectName("appadd")
        add.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add.setFixedHeight(15)
        add.setToolTip("Pull a playing app into this input")
        add.clicked.connect(lambda: self._add_menu(add))
        self.slot_l.addWidget(add)
        self.slot_l.addStretch(1)

    def extra_block(self):
        self._fill_slot([])

    def _add_menu(self, anchor):
        me = self.sink_name()
        m = QMenu(anchor)
        other = [i for i in pwutil.list_sink_inputs() if i["sink"] != me]  # read live, right now
        if not other:
            m.addAction("No other apps are playing right now").setEnabled(False)
        for i in other:
            m.addAction(f"{i['app']}").triggered.connect(lambda _c, ix=i["index"]: pwutil.move_sink_input(ix, me))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _bg(self, fn, *a):
        import threading  # noqa: PLC0415  (pactl is slow: never run it on the GUI thread)
        threading.Thread(target=fn, args=a, daemon=True).start()

    def _app_vol_menu(self, anchor, index, app):
        m = QMenu(anchor)
        m.addAction(f"Volume of '{app}' (only this app):").setEnabled(False)
        for p in (150, 100, 75, 50, 25, 10):
            m.addAction(f"   {p}%").triggered.connect(lambda _c, v=p: self._bg(pwutil.set_input_volume, index, v))
        m.addAction("   Mute / unmute").triggered.connect(lambda: self._bg(pwutil.toggle_input_mute, index))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _app_wheel(self, lab, index, e):
        step = 5 if e.angleDelta().y() > 0 else -5
        base = getattr(lab, "_pct", None) or pwutil.get_input_volume(index)
        lab._pct = min(150, max(0, base + step))
        self._bg(pwutil.set_input_volume, index, lab._pct)
        self.message.emit(f"{lab.text()[2:]} volume {lab._pct}%")
        e.accept()

    def _move_menu(self, anchor, index, app):
        m = QMenu(anchor)
        m.addAction(f"Move '{app}' to:").setEnabled(False)
        for sink, label in VirtStrip.peers:
            if sink != self.sink_name():
                m.addAction(f"VertiMeeter {label}").triggered.connect(lambda _c, s=sink: pwutil.move_sink_input(index, s))
        m.addSeparator()
        for n, d in pwutil.list_sinks()[:4]:
            m.addAction(d).triggered.connect(lambda _c, s=n: pwutil.move_sink_input(index, s))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _mc(self, on):
        self.cfg["mc"] = on
        self.eng.send(c="strip", i=self.cfg["slot"], kar=self._kar_value())

    def _kar(self, i):
        self.cfg["kar"] = i
        self.eng.send(c="strip", i=self.cfg["slot"], kar=self._kar_value())


SR_CHOICES = [8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000]
PCM_FORMATS = [("8-bit", 0), ("16-bit", 1), ("24-bit", 2), ("32-bit int", 3), ("32-bit float", 4), ("64-bit float", 5)]
CODECS = [("vban_pcm", "VBAN PCM (Voicemeeter compatible)"), ("oban_pcm", "OBAN PCM"), ("opus_music", "OBAN Opus - music"),
          ("opus_voice", "OBAN Opus - voice"), ("lossless", "OBAN VFLAC (lossless)"), ("mulaw", "OBAN mu-law (low-bandwidth voice)")]
USE_PCM_NOTE = "Please set your VBAN / OBAN sender to PCM for this device: the codec you are sending is not supported here."


class NetDialog(QDialog):
    """Settings of a network input strip (VBAN or OBAN). Nothing here has a default host: the sender decides."""

    def __init__(self, cfg: dict, parent=None, oban: bool = False):
        super().__init__(parent)
        self.setWindowTitle(f"{'OBAN' if oban else 'VBAN'} input - {cfg['label']}")
        n = cfg["net"]
        v = QVBoxLayout(self)
        g = QGridLayout()
        self.stream = QLineEdit(n.get("stream", ""))
        self.stream.setPlaceholderText("stream name (empty = any)")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(n.get("port", 6990 if oban else 6980)))
        self.allow = QLineEdit(n.get("allow", ""))
        self.allow.setPlaceholderText("only from this sender IP (optional; empty = first sender wins)")
        self.mode = QComboBox()
        self.mode.addItem("Auto (PCM and coded streams)", "auto")
        self.mode.addItem("PCM only", "pcm")
        self.mode.addItem("Coded streams only (Opus, VFLAC, mu-law)", "coded")
        self.mode.setCurrentIndex(max(0, self.mode.findData(n.get("mode", "auto"))))
        self.buf = QSpinBox()
        self.buf.setRange(0, 500)
        self.buf.setSuffix(" ms")
        self.buf.setValue(int(n.get("buf_ms", 40 if oban else 20)))
        self.buf.setToolTip("Jitter / reorder buffer. Bigger = survives a rough network, adds delay. 0 = lowest delay.")
        rows = [("Stream", self.stream), ("UDP port", self.port), ("Allow only", self.allow)]
        if oban:
            rows.append(("Accept", self.mode))
        rows.append(("Jitter buffer", self.buf))
        for i, (lab, w) in enumerate(rows):
            g.addWidget(QLabel(lab), i, 0)
            g.addWidget(w, i, 1)
        v.addLayout(g)
        self.ping = QCheckBox("Answer VBAN device scans (PING)")
        self.ping.setChecked(bool(n.get("ping", False)))
        self.ping.setToolTip("Lets VBAN tools find this input. The reply is never larger than the scan, only sent to the allowed sender if one is set.")
        self.text = QCheckBox("Accept VBAN-TEXT remote commands (needs 'Allow only')")
        self.text.setChecked(bool(n.get("text", False)))
        self.text.setToolTip("Strip[n].Gain=-3;  Strip[n].Mute=1;  Bus[n].Gain=0;  also .Solo / .Mono. Lines starting with // or # are ignored.")
        v.addWidget(self.ping)
        v.addWidget(self.text)
        self.err = QLabel("")
        self.err.setObjectName("dim")
        v.addWidget(self.err)
        self.on = QPushButton("LISTEN")
        self.on.setCheckable(True)
        self.on.setChecked(n.get("on", False))
        v.addWidget(self.on)
        self.open_channels = False
        chb = QPushButton("Channels...")
        chb.setToolTip("Multichannel PCM streams (3 to 8 channels): split channels or pairs out into their own strips.")
        chb.clicked.connect(self._channels)
        v.addWidget(chb)
        note = QPushButton("Copy 'use PCM' note")
        note.setToolTip("Copies a ready-made message you can paste to whoever is sending an unsupported codec.")
        note.clicked.connect(lambda: QApplication.clipboard().setText(USE_PCM_NOTE))
        v.addWidget(note)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        v.addWidget(ok)
        self.setMinimumWidth(400)

    def _channels(self):
        self.open_channels = True
        self.accept()

    def accept(self):
        a = self.allow.text().strip()
        if a:
            try:
                ipaddress.ip_address(a)
            except ValueError:
                self.open_channels = False
                self.err.setText("'Allow only' must be an IP address (or empty).")
                return
        super().accept()

    def result_cfg(self):
        a = self.allow.text().strip()
        return dict(on=self.on.isChecked(), port=self.port.value(), stream=self.stream.text().strip()[:16], allow=a,
                    mode=self.mode.currentData(), buf_ms=self.buf.value(), ping=self.ping.isChecked(),
                    text=self.text.isChecked() and bool(a))


class NetStrip(VirtStrip):
    """VBAN network input: the engine's UDP receiver feeds this strip's slot (no PipeWire node involved)."""

    OBAN = False
    channelsRequested = pyqtSignal(str)
    all_cfg: list = []  # every strip cfg, set by the main window (the receiver map needs the child strips)

    def __init__(self, cfg, eng):
        base = dict(on=False, port=6990 if self.OBAN else 6980, stream="", allow="", mode="auto",
                    buf_ms=40 if self.OBAN else 20, ping=False, text=False, ch=2, split=[])
        n = cfg.setdefault("net", {})
        for k, val in base.items():
            n.setdefault(k, val)
        self.state_txt = ""
        super().__init__(cfg, eng)

    def refresh_names(self):
        if self.OBAN:
            tip = "Open VBAN-compatible networking"
            self.dev.info("Network Input<br>(OBAN)", tip + ".\nReceives VBAN PCM and OBAN streams (Opus, VFLAC, mu-law).\nClick the box below to set stream name, port and sender.")
            self.name.setToolTip(tip + "\n\nClick to select (then [ - ] removes it). Double-click to rename.")
        else:
            self.dev.info("Network input<br>(VBAN)", "VBAN network input. Click the box below to set stream name, port and sender.")

    def set_apps(self, inputs):
        pass

    def extra_block(self):
        self.box = QFrame()
        self.box.setObjectName("netbox")
        self.box.setFixedHeight(76)
        self.box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.box.setToolTip("Click to edit stream name / port / sender.")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(3, 3, 3, 3)
        self.box_l = QLabel("")
        self.box_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box_l.setTextFormat(Qt.TextFormat.RichText)
        self.box_l.setWordWrap(True)
        bl.addWidget(self.box_l)
        self.box.mousePressEvent = lambda e: self._edit()
        self.slot_l.addWidget(self.box)
        self._paint_box()

    def _short(self):
        s = self.state_txt
        if not self.cfg["net"]["on"]:
            return "off", "", ""
        note, main = s.split("  |  ", 1) if "  |  " in s else ("", s)
        p = [x.strip() for x in main.split("|")]
        if p and p[0].startswith("receiving"):
            state = "receiving"
            m = re.match(r"^(.*?) (\d+) Hz (\d+) ch$", p[1]) if len(p) > 1 else None
            codec = f"{m.group(1)} {int(m.group(2)) / 1000:g}k {m.group(3)}ch" if m else (p[1] if len(p) > 1 else "")
            rest = (p[2] + " · " + p[3].replace("loss ", "")) if len(p) > 3 else ""
        elif p and p[0].startswith("silent"):
            state, codec, rest = "silent", "", ""
        else:
            state, codec, rest = (p[0][:22] if p and p[0] else "waiting"), "", ""
        if note:
            state = "! " + note[:20]
        return state, codec, rest

    def _paint_box(self):
        t = theme.cur()
        n = self.cfg["net"]
        state, codec, rest = self._short()
        led = f"<span style='color:{t['accent']}'>●</span>"
        dim = f"<span style='color:{t['dim']}'>"
        small = "font-size:10px;"
        self.box_l.setText(f"<b>{n.get('stream') or '(any stream)'}</b><br>:{n['port']} {led} {state}"
                           f"<br><span style='{small}color:{t['dim']}'>{codec}<br>{rest}</span>")
        self.box.setToolTip((self.state_txt + "\n\n" if self.state_txt and n["on"] else "") + "Click to edit stream name / port / sender.")

    def _edit(self):
        d = NetDialog(self.cfg, self, self.OBAN)
        if d.exec():
            self.cfg["net"].update(d.result_cfg())
            self._paint_box()
            self._send_net()
            if d.open_channels:
                self.channelsRequested.emit(self.cfg["id"])

    def _send_net(self):
        n = self.cfg["net"]
        slots, feeds = net_feeds(NetStrip.all_cfg or [self.cfg], self.cfg)
        self.eng.send(c="oban_rx" if self.OBAN else "vban_rx", k=self.cfg.get("net_k", 0), on=n["on"], slots=slots, feeds=feeds,
                      port=n["port"], stream=n["stream"], allow=n["allow"], mode=n.get("mode", "auto"),
                      buf_ms=n.get("buf_ms", 20), ping=n.get("ping", False), text=n.get("text", False))

    def push(self):
        super().push()
        self._send_net()

    def set_status(self, text: str):
        if text != self.state_txt:
            self.state_txt = text
            self._paint_box()


class ObanStrip(NetStrip):
    """Network Input (OBAN): VBAN-compatible, plus Opus, VFLAC and mu-law streams."""

    OBAN = True


class NetChStrip(VirtStrip):
    """A strip fed by one channel or pair of a multichannel network input (see ChannelsDialog). No device, no PipeWire node."""

    channelsRequested = pyqtSignal(str)

    def __init__(self, cfg, eng):
        self.state = ""
        super().__init__(cfg, eng)

    def refresh_names(self):
        self.dev.info("Network channel<br>(from a stream)", "A channel or pair taken out of a multichannel VBAN / OBAN input.\nClick the box below to open the channel list of that input.")

    def set_apps(self, inputs):
        pass

    def extra_block(self):
        self.box = QFrame()
        self.box.setObjectName("netbox")
        self.box.setFixedHeight(76)
        self.box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.box.setToolTip("Click to open the channel list of the network input this comes from.")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(3, 3, 3, 3)
        self.box_l = QLabel("")
        self.box_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box_l.setTextFormat(Qt.TextFormat.RichText)
        self.box_l.setWordWrap(True)
        bl.addWidget(self.box_l)
        self.box.mousePressEvent = lambda e: self.channelsRequested.emit(self.cfg["src"]["parent"])
        self.slot_l.addWidget(self.box)
        self._paint_box()

    def _paint_box(self):
        t = theme.cur()
        src = self.cfg["src"]
        parent = next((c["label"] for c in NetStrip.all_cfg if c["id"] == src["parent"]), src["parent"])
        chs = src["chs"]
        led = f"<span style='color:{t['accent'] if self.state == 'receiving' else t['dim']}'>●</span>"
        self.box_l.setText(f"<span style='color:{t['dim']}'>from</span> <b>{parent}</b><br><b>{row_label(chs)}</b> "
                           f"({'stereo' if len(chs) == 2 else 'mono, both sides'})<br>{led} {self.state or 'waiting'}")

    def set_status(self, text: str):
        st = "receiving" if "receiving" in text else ("off" if not text else "waiting")
        if st != self.state:
            self.state = st
            self._paint_box()


class ChannelsDialog(QDialog):
    """Split a multichannel network input into strips: one row per channel pair (or single channel), each can feed its own strip."""

    def __init__(self, config, pcfg: dict, detected: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Channels - {pcfg['label']}")
        self.config, self.p = config, pcfg
        net = pcfg["net"]
        self.n = int(detected or net.get("ch", 2) or 2)
        self.split = set(net.get("split", []))
        self.checked = {tuple(c["src"]["chs"]) for c in config.net_children(pcfg["id"])}
        self.cells: dict[tuple, QCheckBox] = {}
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.info = QLabel("")
        top.addWidget(self.info, 1)
        top.addWidget(QLabel("Show"))
        self.count = QComboBox()
        for i in range(1, 9):
            self.count.addItem(f"{i} channel" + ("s" if i > 1 else ""), i)
        self.count.setCurrentIndex(self.n - 1)
        self.count.currentIndexChanged.connect(self._count_changed)
        top.addWidget(self.count)
        v.addLayout(top)
        self.host = QWidget()
        v.addWidget(self.host)
        note = QLabel("Ticking a row adds a strip to the Virtual Inputs pane (it has its own fader, EQ and A / B routing). Unticking removes it. "
                      "[ split ] turns a pair into two mono rows (they play on both sides of their strip) and back. "
                      "Only PCM streams carry more than 2 channels.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        v.addWidget(note)
        self.err = QLabel("")
        self.err.setObjectName("dim")
        v.addWidget(self.err)
        ok = QPushButton("OK")
        ok.clicked.connect(self._ok)
        v.addWidget(ok)
        self.setMinimumWidth(460)
        self.detected = detected
        self._fill()

    def _rows(self):
        return rows_of(self.n, self.split)

    def _collect(self):
        for key, cb in self.cells.items():
            (self.checked.add if cb.isChecked() else self.checked.discard)(key)

    def _fill(self):
        old = self.host.layout()
        if old is not None:
            while old.count():
                w = old.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            QWidget().setLayout(old)
        g = QGridLayout(self.host)
        g.setContentsMargins(0, 0, 0, 0)
        self.cells = {}
        rows = self._rows()
        self.info.setText(f"Stream has {self.detected} channels (read from the sender)" if self.detected else
                          "No stream seen yet: showing the last known / chosen channel count")
        for i, chs in enumerate(rows):
            g.addWidget(QLabel(row_label(chs)), i, 0)
            if i == 0:
                g.addWidget(QLabel(f"This strip ({self.p['label']})"), i, 1)
            else:
                cb = QCheckBox("Own strip")
                cb.setChecked(tuple(chs) in self.checked)
                self.cells[tuple(chs)] = cb
                g.addWidget(cb, i, 1)
            pair = chs[0] // 2
            if (len(chs) == 2) or (pair in self.split):
                b = QPushButton("split")
                b.setCheckable(True)
                b.setChecked(pair in self.split)
                b.clicked.connect(lambda _=False, p=pair: self._toggle(p))
                if len(chs) == 1 and chs[0] % 2 == 1:
                    b.hide()  # the second single of a split pair: its partner above carries the button
                g.addWidget(b, i, 2)

    def _toggle(self, pair):
        self._collect()
        (self.split.discard if pair in self.split else self.split.add)(pair)
        self.checked = {c for c in self.checked if c[0] // 2 != pair}
        self._fill()

    def _count_changed(self):
        self._collect()
        self.n = self.count.currentData()
        self.split = {p for p in self.split if 2 * p + 1 < self.n}
        self.checked = {c for c in self.checked if max(c) < self.n}
        self._fill()

    def _ok(self):
        self._collect()
        rows = self._rows()
        want = [r for r in rows[1:] if tuple(r) in self.checked]
        net = self.p["net"]
        net["ch"], net["split"] = self.n, sorted(self.split)
        if not self.config.sync_net_children(self.p, want):
            self.err.setText("No free strip in the Virtual Inputs pane: untick a row or remove a strip, then press OK again.")
            return
        self.accept()


class ChannelMapDialog(QDialog):
    """Output channel matrix of a multichannel network sender: tick the buses that feed each channel (ticked buses sum)."""

    def __init__(self, m, nch: int, bus_ids: list, own_bus: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Channel map - {own_bus} ({nch} ch)")
        m = m or {}
        self.n, self.buses, self.own = nch, [b for b in BUS_IDS if b in bus_ids] or [own_bus], own_bus
        self.split = set(m.get("split", []))
        self.rows = {k: dict(v) for k, v in m.get("rows", {}).items()}
        self.cells: dict = {}
        v = QVBoxLayout(self)
        self.host = QWidget()
        v.addWidget(self.host)
        note = QLabel("Ticked buses are summed into that channel. A stereo row sends the bus left on its first channel and right on its "
                      "second. A mono row sends L, R or L+R (average). An empty row sends silence. Summing can pass full scale: the "
                      "sender status then counts clipped samples (nothing is limited for you).")
        note.setObjectName("dim")
        note.setWordWrap(True)
        v.addWidget(note)
        ok = QPushButton("OK")
        ok.clicked.connect(self.accept)
        v.addWidget(ok)
        self._fill()

    def _row_state(self, chs):
        r = self.rows.get(row_key(chs))
        if r is None:
            return ([self.own] if chs[0] == 0 else []), "LR"
        return r.get("buses", []), r.get("src", "LR")

    def _collect(self):
        for key, (cbs, combo) in self.cells.items():
            self.rows[key] = dict(buses=[b for b, cb in cbs if cb.isChecked()],
                                  src=("pair" if combo is None else ("L", "R", "LR")[combo.currentIndex()]))

    def _fill(self):
        old = self.host.layout()
        if old is not None:
            while old.count():
                w = old.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            QWidget().setLayout(old)
        g = QGridLayout(self.host)
        g.setContentsMargins(0, 0, 0, 0)
        self.cells = {}
        g.addWidget(QLabel("Channel"), 0, 0)
        for j, b in enumerate(self.buses):
            g.addWidget(QLabel(b), 0, 1 + j)
        g.addWidget(QLabel("Mono as"), 0, 1 + len(self.buses))
        for i, chs in enumerate(rows_of(self.n, self.split), start=1):
            g.addWidget(QLabel(row_label(chs, "OUT")), i, 0)
            buses, mode = self._row_state(chs)
            cbs = []
            for j, b in enumerate(self.buses):
                cb = QCheckBox()
                cb.setChecked(b in buses)
                g.addWidget(cb, i, 1 + j)
                cbs.append((b, cb))
            combo = None
            if len(chs) == 1:
                combo = QComboBox()
                combo.addItems(["L", "R", "L+R"])
                combo.setCurrentIndex({"L": 0, "R": 1}.get(mode, 2))
                g.addWidget(combo, i, 1 + len(self.buses))
            else:
                g.addWidget(QLabel("stereo"), i, 1 + len(self.buses))
            self.cells[row_key(chs)] = (cbs, combo)
            pair = chs[0] // 2
            if len(chs) == 2 or (pair in self.split and chs[0] % 2 == 0):
                b = QPushButton("split")
                b.setCheckable(True)
                b.setChecked(pair in self.split)
                b.clicked.connect(lambda _=False, p=pair: self._toggle(p))
                g.addWidget(b, i, 2 + len(self.buses))

    def _toggle(self, pair):
        self._collect()
        a, b = 2 * pair, 2 * pair + 1
        if pair in self.split:
            first = self.rows.get(row_key([a]))
            self.split.discard(pair)
            self.rows[row_key([a, b])] = dict(buses=list(first["buses"]) if first else [], src="pair")
            self.rows.pop(row_key([a]), None)
            self.rows.pop(row_key([b]), None)
        else:
            cur = self._row_state([a, b])[0]
            self.split.add(pair)
            self.rows.pop(row_key([a, b]), None)
            self.rows[row_key([a])] = dict(buses=list(cur), src="LR")
            self.rows[row_key([b])] = dict(buses=list(cur), src="LR")
        self._fill()

    def result(self) -> dict:
        self._collect()
        keys = {row_key(r) for r in rows_of(self.n, self.split)}
        return dict(split=sorted(self.split), rows={k: v for k, v in self.rows.items() if k in keys})


class NetOutDialog(QDialog):
    """Send a bus over the network (VBAN PCM, OBAN PCM, Opus, VFLAC, mu-law). Target host/port come only from what you type."""

    bus_ids: list = []  # buses that exist, set by the main window (the channel map offers these)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Network out (OBAN / VBAN) - {cfg['id']}")
        self.cfg = cfg
        vb = cfg["vban"]
        v = QVBoxLayout(self)
        g = QGridLayout()
        self.addr = QLineEdit(vb.get("addr", ""))
        self.addr.setPlaceholderText("host or IP address of the receiver")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(vb.get("port", 6980)))
        self.stream = QLineEdit(vb.get("stream", ""))
        self.stream.setPlaceholderText("stream name (max 16 chars)")
        self.proto = QComboBox()
        self.proto.addItem("VBAN (Voicemeeter / VBAN receivers)", "VBAN")
        self.proto.addItem("OBAN (VertiMeeter receivers, more codecs)", "OBAN")
        self.proto.setCurrentIndex(0 if vb.get("codec", "vban_pcm") == "vban_pcm" else 1)
        self.codec = QComboBox()
        self._fill_codecs(vb.get("codec", "vban_pcm"))
        self.sr = QComboBox()
        for s in SR_CHOICES:
            self.sr.addItem(f"{s} Hz", s)
        self.sr.setCurrentIndex(max(0, self.sr.findData(int(vb.get("sr", 48000)))))
        self.ch = QComboBox()
        self.ch.addItem("Stereo", 2)
        self.ch.addItem("Mono", 1)
        for i in range(3, 9):
            self.ch.addItem(f"{i} channels (PCM only)", i)
        self.ch.setCurrentIndex(max(0, self.ch.findData(int(vb.get("ch", 2)))))
        self.map = copy.deepcopy(vb.get("map") or {})
        self.mapbtn = QPushButton("Channel map...")
        self.mapbtn.setToolTip("Choose which buses feed each output channel (3 or more channels).")
        self.mapbtn.clicked.connect(self._open_map)
        chw = QWidget()
        chl = QHBoxLayout(chw)
        chl.setContentsMargins(0, 0, 0, 0)
        chl.addWidget(self.ch, 1)
        chl.addWidget(self.mapbtn)
        self.fmt = QComboBox()
        for name, dt in PCM_FORMATS:
            self.fmt.addItem(name, dt)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(int(vb.get("dt", 4 if vb.get("f32") else 1)))))
        self.kbps = QSpinBox()
        self.kbps.setRange(6, 510)
        self.kbps.setSuffix(" kbps")
        self.kbps.setValue(int(vb.get("kbps", 128)))
        self.ms = QComboBox()
        for m in (5, 10, 20, 40):
            self.ms.addItem(f"{m} ms", m)
        self.ms.setCurrentIndex(max(0, self.ms.findData(int(vb.get("ms", 20)))))
        self.hint = QLabel("")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        rows = (("Protocol", self.proto), ("Host", self.addr), ("Port", self.port), ("Stream", self.stream), ("Codec", self.codec),
                ("Sample rate", self.sr), ("Channels", chw), ("PCM format", self.fmt), ("Opus bitrate", self.kbps),
                ("Opus frame", self.ms))
        for i, (lab, w) in enumerate(rows):
            g.addWidget(QLabel(lab), i, 0)
            g.addWidget(w, i, 1)
        v.addLayout(g)
        v.addWidget(self.hint)
        note = QLabel("IP to IP: this bus is sent only to the host above, and only while SEND is on. On the receiving "
                      "VertiMeeter, set its strip's sender IP to this PC's address. No encryption or login: use a "
                      "private network or Tailscale.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        v.addWidget(note)
        self.stat = QLabel("")
        self.stat.setObjectName("dim")
        v.addWidget(self.stat)
        self.on = QPushButton("SEND")
        self.on.setCheckable(True)
        self.on.setChecked(vb.get("on", False))
        v.addWidget(self.on)
        b = QPushButton("OK")
        b.clicked.connect(self._ok)
        v.addWidget(b)
        self.setMinimumWidth(400)
        self._last_codec = self.codec.currentData()
        self.codec.currentIndexChanged.connect(self._codec_changed)
        self.proto.currentIndexChanged.connect(self._proto_changed)
        self.addr.textChanged.connect(lambda *_: self._sync())
        self.stream.textChanged.connect(lambda *_: self._sync())
        self.port.valueChanged.connect(lambda *_: self._sync())
        self.on.toggled.connect(lambda *_: self._sync())
        for w in (self.sr, self.ch, self.fmt, self.kbps):
            (w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged).connect(lambda *_: self._sync())
        self._sync()

    def _open_map(self):
        d = ChannelMapDialog(self.map, self.ch.currentData(), NetOutDialog.bus_ids, self.cfg["id"], self)
        if d.exec():
            self.map = d.result()

    def _fill_codecs(self, want):
        vban = self.proto.currentData() == "VBAN"
        self.codec.blockSignals(True)
        self.codec.clear()
        for key, label in CODECS:
            if (key == "vban_pcm") == vban:
                self.codec.addItem(label, key)
        self.codec.setCurrentIndex(max(0, self.codec.findData(want)))
        self.codec.blockSignals(False)

    def _proto_changed(self):
        self._fill_codecs("vban_pcm" if self.proto.currentData() == "VBAN" else "oban_pcm")
        self._codec_changed()

    def _ok(self):
        if self.on.isChecked() and not (self.addr.text().strip() and self.port.value() > 0):
            self.stat.setText("address and port required")
            return
        self.accept()

    def _codec_changed(self):
        c, old = self.codec.currentData(), self._last_codec
        self._last_codec = c
        if self.port.value() in (6980, 6990):
            self.port.setValue(6980 if c == "vban_pcm" else 6990)
        if c == "mulaw" and old != "mulaw":
            self.sr.setCurrentIndex(max(0, self.sr.findData(8000)))
            self.ch.setCurrentIndex(self.ch.findData(1))
        if c == "lossless" and self.fmt.currentData() not in (1, 2):
            self.fmt.setCurrentIndex(self.fmt.findData(1))
        if c in ("opus_music", "opus_voice") and old not in ("opus_music", "opus_voice"):
            self.sr.setCurrentIndex(self.sr.findData(48000))
        self._sync()

    def _sync(self):
        c = self.codec.currentData()
        pcm, opus = c in ("vban_pcm", "oban_pcm"), c in ("opus_music", "opus_voice")
        if not pcm and self.ch.currentData() > 2:  # more than 2 channels is PCM only
            self.ch.blockSignals(True)
            self.ch.setCurrentIndex(self.ch.findData(2))
            self.ch.blockSignals(False)
        self.mapbtn.setEnabled(pcm and self.ch.currentData() > 2)
        self.sr.setEnabled(not opus)
        self.fmt.setEnabled(pcm or c == "lossless")
        self.kbps.setEnabled(opus)
        self.ms.setEnabled(opus)
        sr, ch, dt = self.sr.currentData(), self.ch.currentData(), self.fmt.currentData()
        bits = {0: 8, 1: 16, 2: 24, 3: 32, 4: 32, 5: 64}[dt]
        if pcm:
            k, txt = sr * ch * bits / 1000, "PCM, works with Voicemeeter / VBAN receivers" if c == "vban_pcm" else "PCM, OBAN receivers"
        elif opus:
            k, txt = self.kbps.value(), "OBAN receivers only. Voice mode adds loss protection."
        elif c == "lossless":
            k, txt = sr * ch * (24 if dt == 2 else 16) * 0.6 / 1000, "OBAN receivers only. Bit-exact audio, roughly 40% smaller than PCM (about)."
        else:
            k, txt = sr * ch * 8 / 1000, "OBAN receivers only. 8-bit companded voice, very small."
        self.hint.setText(f"about {k:.0f} kbps. {txt}")
        host = self.addr.text().strip()
        self.stat.setText(f"sending '{self.stream.text().strip() or 'Stream1'}' to {host}:{self.port.value()} | about {k:.0f} kbps"
                          if self.on.isChecked() and host else "")

    def result_cfg(self) -> dict:
        c = self.codec.currentData()
        dt = self.fmt.currentData()
        if c == "lossless" and dt not in (1, 2):
            dt = 1
        if c not in ("vban_pcm", "oban_pcm", "lossless"):
            dt = 1
        sr = 48000 if c in ("opus_music", "opus_voice") else self.sr.currentData()
        return dict(on=self.on.isChecked(), addr=self.addr.text().strip(), port=self.port.value(),
                    stream=self.stream.text().strip()[:16], codec=c, sr=sr, ch=self.ch.currentData(), dt=dt,
                    kbps=self.kbps.value(), ms=self.ms.currentData(), f32=(c in ("vban_pcm", "oban_pcm") and dt == 4), map=self.map)


VbanOutDialog = NetOutDialog  # old name


class BusStrip(QFrame):
    changed = pyqtSignal()
    deviceChanged = pyqtSignal(str)
    selected = pyqtSignal(str)
    nameClicked = pyqtSignal(str)
    renamed = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, cfg: dict, eng):
        super().__init__()
        self.cfg, self.eng = cfg, eng
        self.idx = BUS_IDS.index(cfg["id"])
        self.devs: list[tuple[str, str]] = []
        for k, v in (("sel", False), ("mon", False), ("mode", "Normal"), ("eq_on", True)):
            cfg.setdefault(k, v)
        vb = cfg.setdefault("vban", dict(on=False, addr="", port=6980, stream="", f32=False, k=-1))
        for k, val in (("codec", "vban_pcm"), ("sr", 48000), ("ch", 2), ("dt", 4 if vb.get("f32") else 1), ("kbps", 128), ("ms", 20)):
            vb.setdefault(k, val)
        self._txs = ""
        for k, v in (("delay", 0.0), ("limit", 0.0), ("rvb_ret", 1.0), ("dly_ret", 1.0)):
            cfg["knobs"].setdefault(k, v)
        self.setObjectName("strip")
        self.setFixedWidth(BW)
        L = QVBoxLayout(self)
        L.setContentsMargins(5, 5, 5, 5)
        L.setSpacing(3)
        self.name = ClickLabel(cfg["label"].upper())
        self.name.setObjectName("bname")
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name.setFixedHeight(28)
        self.name.setToolTip("Click to select (then [ - ] removes it). Double-click to rename. Right-click: more.")
        self.name.clicked.connect(lambda: self.nameClicked.emit(self.cfg["id"]))
        self.name.doubleClicked.connect(self.rename)
        self.name.context.connect(self._name_menu)
        L.addWidget(self.name)
        self.dev = DevBox(52)
        if cfg["id"].startswith("B") and not IS_WIN:
            pass
        else:
            self.dev.clicked.connect(self._pick)
        L.addWidget(self.dev)
        g = QGridLayout()
        g.setSpacing(0)
        g.setContentsMargins(0, 0, 0, 0)
        ks = [self._knob("bass", "BASS", -12.0, 12.0, 0.0, FT, self._tone), self._knob("mid", "MID", -12.0, 12.0, 0.0, FT, self._tone),
              self._knob("treb", "TREB", -12.0, 12.0, 0.0, FT, self._tone), self._knob("delay", "DELAY", 0.0, 500.0, 0.0, "{:.0f}ms", self._delay),
              self._knob("rvb_ret", "R.RET", 0.0, 2.0, 1.0, "{:.2f}", self._ret), self._knob("dly_ret", "D.RET", 0.0, 2.0, 1.0, "{:.2f}", self._ret),
              self._knob("limit", "LIMIT", -40.0, 0.0, 0.0, "{:+.0f}", self._limit)]
        for i, k in enumerate(ks):
            g.addWidget(k, i // 2, i % 2)
        peq = QPushButton("PEQ")
        peq.setFixedSize(32, 22)
        peq.setStyleSheet("font-size: 10px; padding: 0;")
        peq.setToolTip("Master 6-cell parametric EQ for this bus")
        peq.clicked.connect(self.open_peq)
        g.addWidget(peq, 3, 1, Qt.AlignmentFlag.AlignCenter)
        gw = QWidget()
        gw.setFixedWidth(80)
        gw.setLayout(g)
        r0 = QHBoxLayout()
        r0.setContentsMargins(0, 0, 0, 0)
        r0.addStretch(1)
        r0.addWidget(gw)
        r0.addStretch(1)
        L.addLayout(r0)
        self.mode = QComboBox()
        self.mode.addItems(BUS_MODES)
        self.mode.setCurrentIndex(max(0, BUS_MODES.index(cfg["mode"]) if cfg["mode"] in BUS_MODES else 0))
        self.mode.setToolTip("Bus mode: how the left/right channels are combined")
        self.mode.setFixedHeight(20)
        self.mode.currentIndexChanged.connect(self._mode)
        L.addWidget(self.mode)
        self.sel = _btn("SEL", "", cfg["sel"], "Select this bus (highlight)", h=18)
        self.mon = _btn("MON", "", cfg["mon"], "Also hear this bus on A1" if self.idx else "A1 is the monitor bus", h=18)
        if self.idx == 0:
            self.mon.setEnabled(False)
        self.sel.toggled.connect(self._sel)
        self.mon.toggled.connect(lambda v: self._flag("mon", v))
        for b in (self.sel, self.mon):
            b.setStyleSheet("font-size: 10px; padding: 0;")
        L.addLayout(_hrow(self.sel, self.mon))
        fx = _btn("C5", "", False, "C5 multiband compressor for this bus (5 bands, each with its own compressor)", h=18)
        fx.setCheckable(False)
        fx.setStyleSheet("font-size: 10px; padding: 0;")
        fx.clicked.connect(self.open_c5)
        self.c5_btn = fx
        L.addWidget(fx)
        self._c5_mark()
        self.net_btn = None
        if cfg["id"].startswith("B"):
            nb = _btn("NET", "", False, "", h=22)
            nb.setStyleSheet("font-size: 10px; padding: 0;")
            nb.clicked.connect(self._net_click)
            self.net_btn = nb
            L.addWidget(nb)
            self._net_mark()
        self.fader = Fader(cfg["gain_db"])
        self.meter = Meter()
        self.fader.changed.connect(self._gain)
        row = QHBoxLayout()
        row.setSpacing(3)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self.fader)
        row.addWidget(self.meter)
        row.addStretch(1)
        host = QWidget()
        host.setMinimumHeight(130)
        host.setLayout(row)
        L.addWidget(host, 1)
        self.gain_l = QLabel(StripBase._gain_text(cfg["gain_db"]))
        self.gain_l.setObjectName("readout")
        self.gain_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gain_l.setFixedHeight(19)
        L.addWidget(self.gain_l)
        self.eq = _btn("EQ", "", cfg["eq_on"], "Master EQ on / off. Right-click = open the 6-cell parametric EQ")
        self.mono = _btn("MONO", "mono", cfg["mono"])
        self.mute = _btn("MUTE", "mute", cfg["mute"])
        self.eq.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.eq.customContextMenuRequested.connect(lambda _p: self.open_peq())
        self.eq.toggled.connect(self._eq_on)
        self.mono.toggled.connect(lambda v: self._flag("mono", v))
        self.mute.toggled.connect(lambda v: self._flag("mute", v))
        for b in (self.eq, self.mono, self.mute):
            L.addWidget(b)
        self.refresh()

    # ---- naming / device display ------------------------------------------
    def set_selected(self, on: bool) -> None:
        self.setProperty("sel", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def mic_name(self):
        return f"VertiMeeter Out {self.cfg['label']}"

    def refresh(self):
        if self.cfg["id"].startswith("B") and not IS_WIN:
            self.dev.info(f"Virtual mic:<br><b>VertiMeeter<br>Out {self.cfg['label']}</b>",
                          f"Pick '{self.mic_name()}' as the microphone in Discord / VRChat / OBS")
        else:
            self.dev.show_device(self.cfg["device"], dict(self.devs), "output")

    def rename(self):
        t, ok = QInputDialog.getText(self, "Rename bus", "Name:", text=self.cfg["label"])
        if ok and t.strip():
            self.cfg["label"] = t.strip()[:10]
            self.name.setText(self.cfg["label"].upper())
            self.refresh()
            self.renamed.emit(self.cfg["id"])

    def _name_menu(self):
        m = QMenu(self)
        m.addAction("Rename...").triggered.connect(self.rename)
        m.addAction("Master EQ...").triggered.connect(self.open_peq)
        m.addAction("Network out (OBAN / VBAN)...").triggered.connect(self.open_vban)
        m.exec(self.name.mapToGlobal(self.name.rect().bottomLeft()))

    def _pick(self):
        pick_device(self.dev, pwutil.list_sinks, self.cfg["device"], self._set_dev, "outputs")

    def _set_dev(self, name):
        self.cfg["device"] = name
        self.refresh()
        self.deviceChanged.emit(name)

    def _knob(self, key, label, lo, hi, default, fmt, slot):
        k = Knob(label, lo, hi, self.cfg["knobs"].get(key, default), default, fmt)
        k.changed.connect(lambda v, kk=key: slot(kk, v))
        return k

    def open_peq(self):
        eq = self.cfg.setdefault("eq", default_eq())
        PeqDialog(f"Master EQ - {self.cfg['label']}", eq,
                  lambda i, **kw: self.eng.send(c="eq", t="bus", i=self.idx, cell=i, **kw), self).exec()

    def _net_click(self):
        self._net_mark()  # a click toggles the button, but it only means "sending": put it back, then open the dialog
        self.open_vban()

    def _net_mark(self):
        b = self.net_btn
        if b is None:
            return
        v = self.cfg.get("vban", {})
        on = bool(v.get("on") and v.get("addr"))
        b.blockSignals(True)
        b.setChecked(on)
        b.blockSignals(False)
        if on:
            codec = v.get("codec", "vban_pcm")
            tip = f"Sending to {v.get('addr')}:{v.get('port')} ({'VBAN' if codec == 'vban_pcm' else 'OBAN'}, {dict(CODECS).get(codec, codec)}). Click to edit"
            if self._txs:
                tip += f"\n{self._txs}"
        else:
            tip = "Network out (OBAN / VBAN) is off. Click to set the target host"
        b.setToolTip(tip)

    def open_vban(self):
        d = NetOutDialog(self.cfg, self)
        if d.exec():
            v = d.result_cfg()
            if v["on"] and (not v["addr"]):
                v["on"] = False
            old_k = self.cfg["vban"].get("k", -1)
            self.cfg["vban"] = {**v, "k": old_k}
            self.changed.emit()  # MainWindow assigns a free VBAN sender slot, then calls push()
            self._net_mark()

    def _eq_on(self, on):
        self.cfg["eq_on"] = on
        self._push_eq()

    def _push_eq(self):
        c = self.cfg
        on = c.get("eq_on", True)
        for k in ("bass", "mid", "treb"):
            self.eng.send(**tone_cmd("bus", self.idx, k, c["knobs"].get(k, 0.0) if on else 0.0))
        for i, cell in enumerate(c.get("eq", [])):
            self.eng.send(c="eq", t="bus", i=self.idx, cell=i, **({**cell, "gain": 0.0} if not on and "gain" in cell else cell))

    def _tone(self, key, v):
        self.cfg["knobs"][key] = v
        if self.cfg.get("eq_on", True):
            self.eng.send(**tone_cmd("bus", self.idx, key, v))

    def _ret(self, key, v):
        self.cfg["knobs"][key] = v
        self.eng.send(c="bus", i=self.idx, **{key: v})

    def _delay(self, key, v):
        self.cfg["knobs"][key] = v
        self.eng.send(c="bus", i=self.idx, dly_ms=v)

    def _limit(self, key, v):
        self.cfg["knobs"][key] = v
        self.eng.send(c="bus", i=self.idx, lim_on=v < -0.5, lim_db=v)

    def _mode(self, i):
        self.cfg["mode"] = BUS_MODES[i]
        self.eng.send(c="bus", i=self.idx, mode=i)

    def _sel(self, on):
        self.cfg["sel"] = on
        self.selected.emit(self.cfg["id"] if on else "")

    def push(self):
        c = self.cfg
        self.eng.send(c="bus", i=self.idx, gain_db=c["gain_db"], mute=c["mute"], mono=c["mono"], mon=c["mon"] and self.idx > 0,
                      mode=BUS_MODES.index(c["mode"]) if c["mode"] in BUS_MODES else 0)
        kn = c["knobs"]
        self.eng.send(c="bus", i=self.idx, rvb_ret=kn.get("rvb_ret", 1.0), dly_ret=kn.get("dly_ret", 1.0),
                      dly_ms=kn.get("delay", 0.0), lim_on=kn.get("limit", 0.0) < -0.5, lim_db=kn.get("limit", 0.0))
        self._push_eq()
        if c.get("c5"):
            fxdialogs.send_c5(self.eng, self.idx, c["c5"])
        v = c.get("vban", {})
        if v.get("k", -1) >= 0:
            extra = {}
            if v.get("ch", 2) > 2 and v.get("codec", "vban_pcm") in ("vban_pcm", "oban_pcm"):
                mx, src = out_matrix(v, c["id"])  # 3+ channels: the channel map decides what is sent, from any buses
                extra = dict(mx=mx, src=src)
            self.eng.send(c="oban_tx", k=v["k"], i=self.idx, on=v.get("on", False), addr=v.get("addr", ""),
                          port=v.get("port", 6980), stream=v.get("stream", ""), codec=v.get("codec", "vban_pcm"),
                          sr=v.get("sr", 48000), ch=v.get("ch", 2), dt=v.get("dt", 1), kbps=v.get("kbps", 128),
                          ms=v.get("ms", 20), **extra)

    def set_tx_status(self, text: str) -> None:
        if text != self._txs:
            self._txs = text
            self._net_mark()
            base = "Click to select (then [ - ] removes it). Double-click to rename. Right-click: more."
            self.name.setToolTip(base + (f"\n\nNetwork out: {text}" if text else ""))

    def _gain(self, db):
        self.cfg["gain_db"] = db
        self.gain_l.setText(StripBase._gain_text(db))
        self.eng.send(c="bus", i=self.idx, gain_db=db)

    def _c5_mark(self):
        self.c5_btn.setText("C5 *" if self.cfg.get("c5", {}).get("on") else "C5")

    def open_c5(self):
        fxdialogs.C5Dialog(self.cfg, self.idx, self.eng, self).exec()
        self._c5_mark()

    def _flag(self, k, v):
        self.cfg[k] = v
        self.eng.send(c="bus", i=self.idx, **{k: v})

    def set_devices(self, devs):
        self.devs = devs
        self.refresh()

    def update_meter(self, lr):
        self.meter.set_levels(lr[0], lr[1])
