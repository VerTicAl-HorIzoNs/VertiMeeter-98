"""Scenes (64 saved mixes) and Macro Buttons (80). Pure logic first, dialogs below.

A scene holds the MIX only (gains, mutes, pans, EQ/dynamics/FX knobs, routes, bus modes, media master) - never
devices, names, network or system settings, same idea as Voicemeeter's preset scenes."""
from __future__ import annotations

import copy
import shlex
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .config import BUS_IDS
from .log import log

N_SCENES = 64
N_MACROS = 80

STRIP_KEYS = ("gain_db", "pan", "sp_mode", "sp_x", "sp_y", "mute", "solo", "mono", "sends", "col_x", "col_y",
              "post", "lim", "lim_db", "kar", "mc", "knobs", "eq", "dyn", "voice", "mod_x", "mod_y")
BUS_KEYS = ("gain_db", "mute", "mono", "mode", "knobs", "mon", "eq", "dyn", "c5")
MP_KEYS = ("gain_db", "mute", "sends", "send_db", "dsp")


def ensure(d: dict) -> None:
    sc = d.setdefault("scenes", {})
    sc.setdefault("names", [""] * N_SCENES)
    sc.setdefault("data", {})
    sc["names"] = (list(sc["names"]) + [""] * N_SCENES)[:N_SCENES]
    mac = d.setdefault("macros", [])
    while len(mac) < N_MACROS:
        mac.append(dict(name="", actions=[]))
    del mac[N_MACROS:]


def snapshot(d: dict) -> dict:
    return dict(
        strips={s["id"]: {k: copy.deepcopy(s[k]) for k in STRIP_KEYS if k in s} for s in d["strips"]},
        buses={b["id"]: {k: copy.deepcopy(b[k]) for k in BUS_KEYS if k in b} for b in d["buses"]},
        mp={k: copy.deepcopy(d["mp"][k]) for k in MP_KEYS if k in d["mp"]},
    )


def apply_snapshot(d: dict, snap: dict) -> None:
    for s in d["strips"]:
        for k, v in snap.get("strips", {}).get(s["id"], {}).items():
            s[k] = copy.deepcopy(v)
    for b in d["buses"]:
        for k, v in snap.get("buses", {}).get(b["id"], {}).items():
            b[k] = copy.deepcopy(v)
    for k, v in snap.get("mp", {}).items():
        d["mp"][k] = copy.deepcopy(v)


# ----------------------------------------------------------------------------- actions
VERBS = [
    "Recall scene",            # target: 1-64
    "Strip mute", "Strip solo", "Strip mono",   # target: strip id (H1, V1..), arg: toggle / on / off
    "Strip gain (dB)",         # arg: number
    "Bus mute", "Bus mono",    # target: bus id (A1, B1..)
    "Bus gain (dB)",
    "Route strip > bus",       # target: H1>A1 , arg: toggle / on / off
    "Media player",            # target: play/pause, stop, next, prev, mute, volume dB
    "Run program",             # target: command line (your own command)
]
STATE_VERBS = {"Strip mute": ("strips", "mute"), "Strip solo": ("strips", "solo"), "Strip mono": ("strips", "mono"),
               "Bus mute": ("buses", "mute"), "Bus mono": ("buses", "mono")}


def _flag(cur: bool, arg: str) -> bool:
    a = (arg or "toggle").strip().lower()
    return True if a in ("on", "1", "true") else False if a in ("off", "0", "false") else not cur


def run_action(win, act: dict) -> str:
    """Execute one macro action on the main window. Returns a short status text."""
    d = win.cfg.d
    verb, tgt, arg = act.get("verb", ""), str(act.get("target", "")).strip(), str(act.get("arg", "")).strip()
    try:
        if verb == "Recall scene":
            return win.scene_recall(int(tgt) - 1)
        if verb in STATE_VERBS:
            lst, key = STATE_VERBS[verb]
            for it in d[lst]:
                if it["id"] == tgt:
                    it[key] = _flag(bool(it.get(key)), arg)
                    win.refresh_mix()
                    return f"{tgt} {key} {'on' if it[key] else 'off'}"
            return f"no such {'strip' if lst == 'strips' else 'bus'}: {tgt}"
        if verb in ("Strip gain (dB)", "Bus gain (dB)"):
            for it in d["strips" if verb.startswith("Strip") else "buses"]:
                if it["id"] == tgt:
                    it["gain_db"] = max(-60.0, min(12.0, float(arg)))
                    win.refresh_mix()
                    return f"{tgt} gain {it['gain_db']:+.1f} dB"
            return f"no such item: {tgt}"
        if verb == "Route strip > bus":
            sid, _, bid = tgt.partition(">")
            for s in d["strips"]:
                if s["id"] == sid.strip() and bid.strip() in BUS_IDS:
                    s["sends"][bid.strip()] = _flag(bool(s["sends"].get(bid.strip())), arg)
                    win.refresh_mix()
                    return f"{sid}>{bid} {'on' if s['sends'][bid.strip()] else 'off'}"
            return f"no such route: {tgt}"
        if verb == "Media player":
            m, t = win.media, tgt.lower()
            if t in ("play", "pause", "play/pause"):
                m.play_all()
            elif t == "stop":
                m.stop_all()
            elif t == "next":
                m.next()
            elif t in ("prev", "previous"):
                m.prev()
            elif t == "mute":
                d["mp"]["mute"] = _flag(bool(d["mp"]["mute"]), arg)
                win.refresh_mix()
            elif t.startswith("vol"):
                d["mp"]["gain_db"] = max(-60.0, min(12.0, float(arg)))
                win.refresh_mix()
            else:
                return f"unknown media command: {tgt}"
            return f"media {tgt}"
        if verb == "Run program":
            if not tgt:
                return "no command"
            subprocess.Popen(shlex.split(tgt), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return f"started {shlex.split(tgt)[0]}"
    except (ValueError, OSError) as e:
        log("ERROR", f"macro action {verb}: {e}")
        return f"{verb}: {e}"
    return f"unknown action: {verb}"


# ----------------------------------------------------------------------------- dialogs
class _GridDialog(QDialog):
    cols = 8

    def __init__(self, win, title, count):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(title)
        self.setModal(False)
        self.btns: list[QPushButton] = []
        lay = QVBoxLayout(self)
        self.top = QHBoxLayout()
        lay.addLayout(self.top)
        g = QGridLayout()
        g.setSpacing(4)
        for i in range(count):
            b = QPushButton()
            b.setMinimumWidth(86)
            b.setFixedHeight(38)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.clicked.connect(lambda _c, k=i: self.left(k))
            b.customContextMenuRequested.connect(lambda _p, k=i: self.right(k))
            g.addWidget(b, i // self.cols, i % self.cols)
            self.btns.append(b)
        lay.addLayout(g)
        lay.addStretch(1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setObjectName("dim")
        lay.addWidget(self.note)

    def left(self, k):
        pass

    def right(self, k):
        pass


class ScenesDialog(_GridDialog):
    def __init__(self, win):
        super().__init__(win, "Preset scenes (64)", N_SCENES)
        self.sel = 0
        self.store_btn = QPushButton("STORE CURRENT MIX")
        self.store_btn.clicked.connect(lambda: self.store(self.sel))
        self.top.addWidget(self.store_btn)
        self.top.addStretch(1)
        self.note.setText("Click = recall. Right-click = store / rename / clear. F1-F12 recall scenes 1-12 while the mixer "
                          "window is focused. Scenes hold the mix only (gains, mutes, pans, EQ, FX, routes), never "
                          "devices, names or network settings.")
        self.refresh()

    def refresh(self):
        sc = self.win.cfg.d["scenes"]
        for i, b in enumerate(self.btns):
            have = str(i) in sc["data"]
            b.setText(f"{i + 1}\n{sc['names'][i]}" if sc["names"][i] else f"{i + 1}" + ("\nscene" if have else ""))
            b.setEnabled(True)
            b.setCheckable(True)
            b.setChecked(i == self.sel)
            f = b.font()
            f.setBold(have)
            b.setFont(f)
            b.setToolTip("stored" if have else "empty (right-click to store the current mix here)")

    def left(self, k):
        self.sel = k
        self.win.scene_recall(k)
        self.refresh()

    def store(self, k):
        self.win.scene_store(k)
        self.refresh()

    def right(self, k):
        self.sel = k
        sc = self.win.cfg.d["scenes"]
        m = QMenu(self)
        m.addAction(f"Store current mix in scene {k + 1}").triggered.connect(lambda: self.store(k))
        m.addAction("Rename...").triggered.connect(lambda: self._rename(k))
        a = m.addAction("Clear")
        a.setEnabled(str(k) in sc["data"])
        a.triggered.connect(lambda: (sc["data"].pop(str(k), None), sc["names"].__setitem__(k, ""),
                                     self.win.cfg.save(), self.refresh()))
        m.exec(self.btns[k].mapToGlobal(self.btns[k].rect().bottomLeft()))

    def _rename(self, k):
        sc = self.win.cfg.d["scenes"]
        t, ok = QInputDialog.getText(self, "Scene name", f"Name of scene {k + 1}:", text=sc["names"][k])
        if ok:
            sc["names"][k] = t.strip()[:14]
            self.win.cfg.save()
            self.refresh()


class MacroEdit(QDialog):
    def __init__(self, win, m):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Edit macro button")
        self.m = m
        self.rows: list[tuple] = []
        lay = QVBoxLayout(self)
        h = QHBoxLayout()
        h.addWidget(QLabel("Name"))
        self.name = QLineEdit(m.get("name", ""))
        self.name.setMaxLength(14)
        h.addWidget(self.name)
        lay.addLayout(h)
        tr = QHBoxLayout()
        tr.addWidget(QLabel("Hotkey"))
        self.key = QLineEdit(m.get("key", ""))
        self.key.setPlaceholderText("ctrl+alt+f5")
        cap = QPushButton("CAPTURE")
        cap.clicked.connect(lambda: (cap.setText("press keys..."),
                                     win.hotkey_capture(lambda c: (self.key.setText(c), cap.setText("CAPTURE")))))
        tr.addWidget(self.key)
        tr.addWidget(cap)
        tr.addWidget(QLabel("MIDI"))
        self.midi = QLineEdit(m.get("midi", ""))
        self.midi.setPlaceholderText("note:1:60")
        lr = QPushButton("LEARN")
        lr.clicked.connect(lambda: (lr.setText("move a control..."),
                                    win.midi_learn(lambda t: (self.midi.setText(t), lr.setText("LEARN")))))
        tr.addWidget(self.midi)
        tr.addWidget(lr)
        lay.addLayout(tr)
        lay.addWidget(QLabel("Actions (run top to bottom when you press the button)"))
        self.box = QVBoxLayout()
        lay.addLayout(self.box)
        for a in m.get("actions", []):
            self._row(a)
        add = QPushButton("+ ACTION")
        add.clicked.connect(lambda: self._row({"verb": VERBS[1], "target": "", "arg": "toggle"}))
        lay.addWidget(add)
        hint = QLabel("Target examples: strip H1 / V2, bus A1 / B1, route H1>A1, scene number 1-64, media: play, stop, "
                      "next, prev, mute, volume. Arg: toggle / on / off, or a dB number for gain.")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        lay.addWidget(hint)
        bb = QHBoxLayout()
        ok, no = QPushButton("OK"), QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        bb.addStretch(1)
        bb.addWidget(ok)
        bb.addWidget(no)
        lay.addLayout(bb)

    def _row(self, a):
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        v = QComboBox()
        v.addItems(VERBS)
        v.setCurrentText(a.get("verb", VERBS[1]))
        t = QComboBox()
        t.setEditable(True)
        t.setMinimumWidth(150)
        d = self.win.cfg.d
        t.addItems([s["id"] for s in d["strips"]] + [b["id"] for b in d["buses"]])
        t.setCurrentText(str(a.get("target", "")))
        ar = QLineEdit(str(a.get("arg", "")))
        ar.setPlaceholderText("toggle / on / off / dB")
        ar.setMaximumWidth(130)
        x = QPushButton("×")
        x.setFixedWidth(26)
        hl.addWidget(v)
        hl.addWidget(t, 1)
        hl.addWidget(ar)
        hl.addWidget(x)
        row = (w, v, t, ar)
        x.clicked.connect(lambda: (self.rows.remove(row), w.deleteLater()))
        self.rows.append(row)
        self.box.addWidget(w)

    def result(self):
        return dict(name=self.name.text().strip(), key=self.key.text().strip().lower(), midi=self.midi.text().strip().lower(),
                    actions=[dict(verb=v.currentText(), target=t.currentText().strip(), arg=ar.text().strip())
                             for _w, v, t, ar in self.rows])


class MacroDialog(_GridDialog):
    cols = 8

    def __init__(self, win):
        super().__init__(win, "Macro Buttons (80)", N_MACROS)
        self.top.addWidget(QLabel("Click = run. Right-click = edit / clear."))
        self.top.addStretch(1)
        self.note.setText("A button runs its actions in order: recall a scene, mute / solo / mono / gain a strip or bus, "
                          "toggle a route, control the media player, or start a program of your own.")
        self.refresh()

    def refresh(self):
        for i, b in enumerate(self.btns):
            m = self.win.cfg.d["macros"][i]
            b.setText(m["name"] or (f"{i + 1}" if not m["actions"] else f"{i + 1}\n{len(m['actions'])} act."))
            f = b.font()
            f.setBold(bool(m["actions"]))
            b.setFont(f)
            b.setToolTip("\n".join(f"{a['verb']} {a['target']} {a['arg']}".strip() for a in m["actions"]) or "empty")

    def left(self, k):
        self.win.run_macro(k)

    def right(self, k):
        m = QMenu(self)
        m.addAction("Edit...").triggered.connect(lambda: self._edit(k))
        m.addAction("Clear").triggered.connect(lambda: (self.win.cfg.d["macros"].__setitem__(k, dict(name="", actions=[])),
                                                        self.win.cfg.save(), self.refresh()))
        m.exec(self.btns[k].mapToGlobal(self.btns[k].rect().bottomLeft()))

    def _edit(self, k):
        dlg = MacroEdit(self.win, self.win.cfg.d["macros"][k])
        if dlg.exec():
            self.win.cfg.d["macros"][k] = dlg.result()
            self.win.cfg.save()
            self.refresh()
