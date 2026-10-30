"""Tools menu pieces: system settings, audio map (patch bay) and the bus recorder."""
from __future__ import annotations

import os
import signal
import subprocess
import time

from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                             QVBoxLayout)

from . import pwutil
from .log import log
from .plat import IS_WIN

LATENCIES = [("20 ms (lowest, can crackle)", 20), ("30 ms", 30), ("40 ms (default)", 40), ("60 ms", 60),
             ("100 ms", 100), ("200 ms (safest)", 200)]
QUANTA = [("Auto (PipeWire decides)", 0), ("64 frames", 64), ("128 frames", 128), ("256 frames", 256),
          ("512 frames", 512), ("1024 frames", 1024), ("2048 frames", 2048)]


def set_quantum(frames: int) -> bool:
    """Force the global PipeWire quantum (0 = back to automatic)."""
    if IS_WIN:
        return False
    r = subprocess.run(["pw-metadata", "-n", "settings", "0", "clock.force-quantum", str(frames)],
                       capture_output=True, text=True)
    return r.returncode == 0


def current_quantum() -> int:
    if IS_WIN:
        return 0
    r = subprocess.run(["pw-metadata", "-n", "settings", "0", "clock.force-quantum"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "value:'" in line:
            try:
                return int(line.split("value:'")[1].split("'")[0])
            except ValueError:
                return 0
    return 0


class SystemDialog(QDialog):
    def __init__(self, eng, parent=None):
        super().__init__(parent)
        self.setWindowTitle("System settings")
        self.eng = eng
        m = eng.meters or {}
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Engine: {m.get('sr', '?')} Hz, {m.get('buf', '?')} frames per cycle"))
        self.cfg = getattr(parent, "cfg", None)
        if IS_WIN:
            v.addWidget(QLabel("Playback buffer. Smaller = lower latency, larger = safer (Windows shared mode,\n"
                               "no ASIO). Applies at once; the engine keeps its own 5 ms mixing blocks."))
        else:
            v.addWidget(QLabel("Buffer size (PipeWire quantum). Smaller = lower latency, larger = safer.\n"
                               "This is a system-wide PipeWire setting; Auto is the safe choice."))
        self.q = QComboBox()
        if IS_WIN:
            for name, val in LATENCIES:
                self.q.addItem(name, val)
            self.q.setCurrentIndex(max(0, self.q.findData((self.cfg.d.get("engine", {}) if self.cfg else {}).get("lat_ms", 40))))
        else:
            cur = current_quantum()
            for name, val in QUANTA:
                self.q.addItem(name, val)
            self.q.setCurrentIndex(max(0, self.q.findData(cur)))
        v.addWidget(self.q)
        r = QHBoxLayout()
        ok = QPushButton("APPLY")
        ok.clicked.connect(self._apply)
        cl = QPushButton("CLOSE")
        cl.clicked.connect(self.accept)
        r.addWidget(ok)
        r.addWidget(cl)
        v.addLayout(r)
        self.msg = QLabel("")
        v.addWidget(self.msg)
        self.setMinimumWidth(380)

    def _apply(self):
        if IS_WIN:
            ms = int(self.q.currentData())
            self.eng.send(c="latency", ms=ms)
            if self.cfg is not None:
                self.cfg.d.setdefault("engine", {})["lat_ms"] = ms
            self.msg.setText(f"playback buffer {ms} ms")
            self.eng.send(c="reset_stats")
            return
        ok = set_quantum(self.q.currentData())
        self.msg.setText("applied" if ok else ("not available on Windows: the engine uses 5 ms blocks; set VERTIMEETER_LAT_MS (10-500) for the playback buffer" if IS_WIN else "pw-metadata failed - is PipeWire running?"))
        if self.eng:
            self.eng.send(c="reset_stats")


class PatchDialog(QDialog):
    """Audio map: connect any PipeWire output port to any input port. Links you add here are remembered
    (config 'patches') and kept by the linker, so EXT1/EXT2 sends and RET1/RET2 returns survive restarts."""

    def __init__(self, cfg, linker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio map / patch bay")
        self.cfg, self.linker = cfg, linker
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Engine external FX: send out of VertiMeeter:EXT1_*/EXT2_*, bring back into RET1_*/RET2_*.\n"
                           "Pick an output port and an input port, then LINK."))
        self.f = QLineEdit()
        self.f.setPlaceholderText("filter (e.g. EXT, RET, carla)")
        self.f.textChanged.connect(self._fill)
        v.addWidget(self.f)
        row = QHBoxLayout()
        self.outs, self.ins = QListWidget(), QListWidget()
        for w, t in ((self.outs, "OUTPUT ports"), (self.ins, "INPUT ports")):
            col = QVBoxLayout()
            col.addWidget(QLabel(t))
            col.addWidget(w)
            row.addLayout(col)
        v.addLayout(row)
        b = QPushButton("LINK selected output -> selected input")
        b.clicked.connect(self._link)
        v.addWidget(b)
        v.addWidget(QLabel("Your saved links:"))
        self.mine = QListWidget()
        v.addWidget(self.mine)
        r = QHBoxLayout()
        rm = QPushButton("REMOVE selected link")
        rm.clicked.connect(self._remove)
        cl = QPushButton("CLOSE")
        cl.clicked.connect(self.accept)
        r.addWidget(rm)
        r.addWidget(cl)
        v.addLayout(r)
        self._all_out = pwutil.list_ports("out")
        self._all_in = pwutil.list_ports("in")
        self._fill()
        self._mine()
        self.resize(720, 560)

    def _fill(self):
        t = self.f.text().lower()
        for lst, src in ((self.outs, self._all_out), (self.ins, self._all_in)):
            lst.clear()
            lst.addItems([p for p in src if t in p.lower()])

    def _mine(self):
        self.mine.clear()
        self.mine.addItems([f"{a}  ->  {b}" for a, b in self.cfg.d.get("patches", [])])

    def _link(self):
        o, i = self.outs.currentItem(), self.ins.currentItem()
        if not o or not i:
            return
        pair = [o.text(), i.text()]
        pat = self.cfg.d.setdefault("patches", [])
        if pair not in pat:
            pat.append(pair)
        pwutil.link(*pair)
        self.linker.patches = [tuple(p) for p in pat]
        self._mine()

    def _remove(self):
        r = self.mine.currentRow()
        pat = self.cfg.d.setdefault("patches", [])
        if 0 <= r < len(pat):
            a, b = pat.pop(r)
            pwutil.unlink(a, b)
            self.linker.patches = [tuple(p) for p in pat]
            self._mine()


class Recorder:
    """Records one bus to a WAV file with pw-record. The link from the engine port is registered with the
    linker (extra) so its tidy-up does not remove it."""

    def __init__(self, linker):
        self.linker = linker
        self.proc: subprocess.Popen | None = None
        self.bus = ""
        self.path = ""
        self.links: set[tuple[str, str]] = set()
        self.t0 = 0.0

    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, bus: str, path: str) -> bool:
        if self.active:
            return False
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.proc = subprocess.Popen(pwutil.die_with_parent(["pw-record", "--rate", "48000", "--channels", "2", "--format", "f32",
                                      "-P", "{ node.name = vm-rec node.autoconnect = false }", path]),
                                     stderr=subprocess.DEVNULL)
        self.bus, self.path, self.t0 = bus, path, time.time()
        self.links = {(f"VertiMeeter:{bus}_L", "vm-rec:input_FL"), (f"VertiMeeter:{bus}_R", "vm-rec:input_FR")}
        self.linker.extra |= self.links
        for _ in range(20):  # wait for the recorder node to appear, then link
            time.sleep(0.15)
            if all(pwutil.link(a, b) for a, b in self.links):
                break
        log("OK", f"recording {bus} -> {path}")
        return True

    def stop(self) -> str:
        if not self.proc:
            return ""
        self.linker.extra -= self.links
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)  # lets pw-record finish the WAV header
            try:
                self.proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        log("OK", f"recording saved: {self.path}")
        return self.path

    def elapsed(self) -> str:
        s = int(time.time() - self.t0)
        return f"{s // 60:02d}:{s % 60:02d}"


if IS_WIN:  # the engine writes the WAV itself (no pw-record)
    from .winutil import WinRecorder as Recorder  # noqa: F401,E402
