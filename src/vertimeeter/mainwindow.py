"""Main window: 5 panes + media section, themes, layouts, 16:9 lock."""
from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMenuBar,
                             QSplitter, QStatusBar, QVBoxLayout, QWidget)

from . import autostart, controls, fxhost, hotkeys, midi, pwutil, scenes, theme
from .config import BUS_IDS, MAX_STRIPS, MEDIA_SLOT, Config, VERSION
from .customlayout import Canvas, CustomContainer, default_rects
from .log import log
from .pane import Pane
from . import netctl
from .strips import BusStrip, ChannelsDialog, HwStrip, NetChStrip, NetOutDialog, NetStrip, ObanStrip, VirtStrip
from .tools import PatchDialog, Recorder, SystemDialog


from .plat import IS_WIN  # noqa: E402

# strips that have a device picker: hardware inputs everywhere; on Windows the "virtual" strips too (a capture or
# loopback device, because Windows cannot create virtual cable devices without a driver)
DEV_KINDS = ("hw", "virt") if IS_WIN else ("hw",)


class DeviceWatcher(QObject):
    updated = pyqtSignal(list, list)
    apps = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self._last = None
        self._run = True
        self._ev = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def poke(self):
        self._ev.set()

    def stop(self):
        self._run = False
        self._ev.set()

    def _loop(self):
        while self._run:
            cur = (pwutil.list_sources(), pwutil.list_sinks())
            if cur != self._last:
                self._last = cur
                self.updated.emit(*cur)
            a = pwutil.list_sink_inputs()
            if a != getattr(self, "_apps", None):
                self._apps = a
                self.apps.emit(a)
            self._ev.wait(4.0)
            self._ev.clear()


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, eng, linker):
        super().__init__()
        self.cfg, self.eng, self.linker = cfg, eng, linker
        self.setWindowTitle(f"VertiMeeter '98 {VERSION}")
        self.sources: list = []
        self.sinks: list = []
        self.inputs: list = []
        self.rec = Recorder(linker)
        self.linker.patches = [tuple(p) for p in cfg.d.get("patches", [])]
        self.strip_w: dict[str, object] = {}
        self.bus_w: dict[str, BusStrip] = {}
        self._fixing = False
        self.panes = {
            "hw": Pane("hw", "HARDWARE INPUTS"), "vi": Pane("vi", "VIRTUAL INPUTS"),
            "fx": Pane("fx", "SPECIAL FX", with_controls=False),
            "ha": Pane("ha", "HARDWARE OUT"), "vb": Pane("vb", "VIRTUAL OUT"),
        }
        from .media import MediaSection  # noqa: PLC0415
        self.media = MediaSection(cfg, eng)
        self.media.changed.connect(self._relink)
        self.sel_strip = self.sel_bus = None
        names = cfg.d.setdefault("names", {})
        for k, p in self.panes.items():
            if names.get(k):
                p.set_title(names[k])
            p.renamed.connect(self._pane_renamed)
        if names.get("media"):
            self.media.set_title(names["media"])
        self.root = QWidget()
        self.root.setObjectName("root")
        self.setCentralWidget(self.root)
        self.rl = QVBoxLayout(self.root)
        self.rl.setContentsMargins(6, 6, 6, 6)
        self.rl.setSpacing(4)
        self._header()
        self.container: QWidget | None = None
        self._wire_panes()
        self._menus()
        self.setStatusBar(QStatusBar())
        self.statusBar().setFixedHeight(20)
        self.perf = QLabel("")
        self.statusBar().addPermanentWidget(self.perf)
        self.statusBar().showMessage("Click a strip name to select it ([ - ] removes it), double-click to rename. Hover a virtual input name to see what plays into it. "
                                     "Drag the purple borders to resize panes.")
        self.rebuild()
        self.apply_layout()
        self.dw = DeviceWatcher()
        self.dw.updated.connect(self._devices)
        self.dw.apps.connect(self._apps)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)
        self._autosave = QTimer(self)  # 0.12.1: every setting is saved within ~2 s of changing it, not only on a clean exit
        self._autosave.timeout.connect(self._autosave_tick)
        self._autosave.start(2000)
        self._inputs_init()
        self.resize(1600, 900)

    def _autosave_tick(self):
        try:
            self.cfg.save_if_changed()
        except Exception as ex:  # noqa: BLE001  (a full disk must not take the mixer down)
            log("ERROR", f"autosave: {ex}")

    # ------------------------------------------------------------ header
    def _header(self):
        """Gradient title bar (logo + engine status) on top of the menu bar, like mockup 5. Lives in the menu area."""
        host = QWidget()
        hv = QVBoxLayout(host)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        h = QFrame()
        h.setObjectName("titlebar")
        h.setFixedHeight(34)
        hl = QHBoxLayout(h)
        hl.setContentsMargins(6, 0, 12, 0)
        logo = QFrame()
        logo.setObjectName("logo")
        ll = QHBoxLayout(logo)
        ll.setContentsMargins(8, 2, 8, 2)
        self.logo_l = QLabel("")
        self.logo_l.setTextFormat(Qt.TextFormat.RichText)
        ll.addWidget(self.logo_l)
        self._logo_text()
        self.hstat = QLabel("")
        self.hstat.setObjectName("engstat")
        self.hstat.setTextFormat(Qt.TextFormat.RichText)
        self.hrec = QLabel("")
        self.hrec.setObjectName("engstat")
        hl.addWidget(logo)
        hl.addStretch(1)
        hl.addWidget(self.hrec)
        hl.addSpacing(12)
        hl.addWidget(self.hstat)
        self.mbar = QMenuBar()
        self.mbar.setNativeMenuBar(False)
        self.mbar.setFixedHeight(22)
        hv.addWidget(h)
        hv.addWidget(self.mbar)
        self.setMenuWidget(host)
        self.header = h

    def _logo_text(self):
        t = theme.cur()
        self.logo_l.setText(f"<span style='color:#fff;font-size:14px'><b>VERTI</b></span>"
                            f"<span style='color:{t['accenthi']};font-size:14px'><b>MEETER</b></span> "
                            f"<span style='color:{t['accent']};font-size:14px'><i>'98</i></span> "
                            f"<span style='color:{t['dim']};font-size:9px'>v{VERSION}</span>")

    def _apps(self, inputs):
        self.inputs = inputs
        for w in self.strip_w.values():
            if hasattr(w, "set_apps"):
                w.set_apps(inputs)

    def open_patch(self):
        PatchDialog(self.cfg, self.linker, self).exec()

    # ------------------------------------------------------------ panes
    def _wire_panes(self):
        p = self.panes
        p["hw"].menuAboutToShow.connect(lambda m: self._menu_hw(m))
        p["vi"].menuAboutToShow.connect(lambda m: self._menu_vi(m))
        p["ha"].menuAboutToShow.connect(lambda m: self._menu_bus(m, "A"))
        p["vb"].menuAboutToShow.connect(lambda m: self._menu_bus(m, "B"))
        p["hw"].removeRequested.connect(lambda: self._remove_last(lambda s: s["kind"] == "hw"))
        p["vi"].removeRequested.connect(lambda: self._remove_last(lambda s: s["kind"] != "hw"))
        p["ha"].removeRequested.connect(lambda: self._remove_bus("A"))
        p["vb"].removeRequested.connect(lambda: self._remove_bus("B"))
        from .fxpanel import FxPanel  # noqa: PLC0415
        self.fxpanel = FxPanel(self.cfg, self.eng, self.open_patch)
        self.fxpanel.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        p["fx"].row.insertWidget(0, self.fxpanel)
        p["fx"].setMinimumWidth(150)

    def _menu_hw(self, m):
        m.addAction("Empty hardware strip").triggered.connect(lambda: self._add_strip("hw", ""))
        m.addSeparator()
        for n, d in pwutil.list_sources():  # read live, right now
            m.addAction(d).triggered.connect(lambda _c, nn=n, dd=d: self._add_strip("hw", nn, dd))

    def _menu_vi(self, m):
        m.addAction("Virtual strip (apps play into it)").triggered.connect(lambda: self._add_strip("virt", ""))
        m.setToolTipsVisible(True)
        m.addAction("Network (VBAN) strip").triggered.connect(lambda: self._add_strip("net", ""))
        ob = m.addAction("Network Input (OBAN) strip")
        ob.setToolTip("Open VBAN-compatible networking")
        ob.setStatusTip("Open VBAN-compatible networking")
        ob.triggered.connect(lambda: self._add_strip("oban", ""))

    def _menu_bus(self, m, pre):
        free = self.cfg.free_buses(pre)
        if not free:
            m.addAction("(all %s buses in use)" % pre).setEnabled(False)
            return
        m.addAction(f"Add {free[0]} (no device)").triggered.connect(lambda: self._add_bus(free[0], ""))
        if pre == "A":
            m.addSeparator()
            for n, d in pwutil.list_sinks():
                m.addAction(d).triggered.connect(lambda _c, nn=n: self._add_bus(free[0], nn))

    def _add_strip(self, kind, device, label=None):
        s = self.cfg.add_strip(kind)
        if s is None:
            full = self.cfg.pane_full(kind)
            pane = "Hardware Inputs" if kind == "hw" else "Virtual Inputs"
            self.statusBar().showMessage(f"{pane} is full (8 strips max)" if full else "No free network receiver (8 max)", 4000)
            return
        s["device"] = device
        if label and kind == "hw":
            s["label"] = label[:14]
        self.rebuild()

    def _add_bus(self, bid, device):
        b = self.cfg.add_bus(bid)
        b["device"] = device
        self.rebuild()

    def _remove_last(self, pred):
        c = [s for s in self.cfg.d["strips"] if pred(s)]
        if c:
            pick = next((s for s in c if s["id"] == self.sel_strip), c[-1])
            self.eng.send(c="strip", i=pick["slot"], active=False)
            self.cfg.remove_strip(pick["id"])
            self.sel_strip = None
            self.rebuild()

    def _remove_bus(self, pre):
        c = [b for b in self.cfg.d["buses"] if b["id"].startswith(pre)]
        if len(c) > 0:
            pick = next((b for b in c if b["id"] == self.sel_bus), c[-1])
            self.cfg.remove_bus(pick["id"])
            self.sel_bus = None
            self.rebuild()

    # ------------------------------------------------------------ build
    def rebuild(self):
        d = self.cfg.d
        bus_ids = [b["id"] for b in d["buses"]]
        self.strip_w, self.bus_w = {}, {}
        VirtStrip.peers = [(f"VertiMeeter_{s['id']}", s["label"]) for s in d["strips"] if s["kind"] == "virt"]
        NetStrip.all_cfg = d["strips"]
        NetOutDialog.bus_ids = bus_ids
        hw, vi = [], []
        for s in d["strips"]:
            w = {"hw": HwStrip, "net": NetStrip, "oban": ObanStrip, "netch": NetChStrip}.get(s["kind"], VirtStrip)(s, self.eng)
            if hasattr(w, "channelsRequested"):
                w.channelsRequested.connect(self._open_channels)
            if hasattr(w, "set_apps"):
                w.set_apps(self.inputs)
            w.build_routes(bus_ids)
            w.nameClicked.connect(self._strip_clicked)
            w.renamed.connect(self._renamed)
            w.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
            w.set_selected(s["id"] == self.sel_strip)
            if s["kind"] in DEV_KINDS:
                w.set_devices(self.sources)
                w.deviceChanged.connect(lambda _x: self._relink())
            self.strip_w[s["id"]] = w
            (hw if s["kind"] == "hw" else vi).append(w)
        ha, vb = [], []
        for b in d["buses"]:
            w = BusStrip(b, self.eng)
            w.nameClicked.connect(self._bus_clicked)
            w.renamed.connect(self._renamed)
            w.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
            w.set_selected(b["id"] == self.sel_bus)
            w.set_devices(self.sinks)
            w.deviceChanged.connect(lambda _x: self._relink())
            w.changed.connect(lambda bid=b["id"]: self._bus_changed(bid))
            self.bus_w[b["id"]] = w
            (ha if b["id"].startswith("A") else vb).append(w)
        self.panes["hw"].set_widgets(hw)
        self.panes["vi"].set_widgets(vi)
        self.panes["ha"].set_widgets(ha)
        self.panes["vb"].set_widgets(vb)
        for w in list(self.strip_w.values()) + list(self.bus_w.values()):
            w.push()
        for sid in range(MAX_STRIPS):
            if not any(s["slot"] == sid for s in d["strips"]):
                self.eng.send(c="strip", i=sid, active=False)
        for i, bid in enumerate(BUS_IDS):
            if bid not in bus_ids:
                self.eng.send(c="bus", i=i, mute=True)
        self._relink()

    def _open_channels(self, pid):
        """Channel list of a multichannel network input: split channels / pairs out into strips."""
        pcfg = next((s for s in self.cfg.d["strips"] if s["id"] == pid), None)
        if pcfg is None:
            return
        vbch = (self.eng.meters or {}).get("vbch", [])
        k = pcfg.get("net_k", 0)
        count = vbch[k] if k < len(vbch) else 0
        if ChannelsDialog(self.cfg, pcfg, count, self).exec():
            self.rebuild()

    def _strip_clicked(self, sid):
        self.sel_strip = None if self.sel_strip == sid else sid
        for k, w in self.strip_w.items():
            w.set_selected(k == self.sel_strip)

    def _bus_clicked(self, bid):
        self.sel_bus = None if self.sel_bus == bid else bid
        for k, w in self.bus_w.items():
            w.set_selected(k == self.sel_bus)

    def _renamed(self, _id):
        VirtStrip.peers = [(f"VertiMeeter_{s['id']}", s["label"]) for s in self.cfg.d["strips"] if s["kind"] == "virt"]
        self.cfg.save()
        self._relink()  # the linker renames the virtual cable / virtual mic that other apps see

    def _pane_renamed(self, key, title):
        self.cfg.d.setdefault("names", {})[key] = title
        self.cfg.save()

    def _bus_changed(self, bid):
        w = self.bus_w.get(bid)
        if w is None:
            return
        v = w.cfg["vban"]
        if v["on"] and v.get("k", -1) < 0:
            k = self.cfg.free_vban_tx()
            if k is None:
                v["on"] = False
                self.statusBar().showMessage("All four network senders are in use", 4000)
            else:
                v["k"] = k
        elif not v["on"] and v.get("k", -1) >= 0:
            self.eng.send(c="oban_tx", k=v["k"], on=False, i=w.idx)
            v["k"] = -1
        w.push()

    def _relink(self):
        self.linker.update(self.cfg.d["strips"] + [self.cfg.d["mp"]], self.cfg.d["buses"])
        self.media.refresh_routes()

    def _devices(self, sources, sinks):
        self.sources, self.sinks = sources, sinks
        for s in self.cfg.d["strips"]:
            w = self.strip_w.get(s["id"])
            if w is not None and s["kind"] in DEV_KINDS:
                w.set_devices(sources)
        for b in self.bus_w.values():
            b.set_devices(sinks)
        self._relink()

    def _tick(self):
        m = self.eng.meters
        if not m:
            return
        for s in self.cfg.d["strips"]:
            w = self.strip_w.get(s["id"])
            if w is not None:
                w.update_meter(m["strips"][s["slot"]])
        for i, bid in enumerate(BUS_IDS):
            w = self.bus_w.get(bid)
            if w is not None:
                w.update_meter(m["buses"][i])
        self.media.update_meter(m["strips"][MEDIA_SLOT])
        vb = m.get("vban", [])
        vbch = m.get("vbch", [])
        for s in self.cfg.d["strips"]:
            w = self.strip_w.get(s["id"])
            if s["kind"] in ("net", "oban") and w is not None:
                k = s.get("net_k", 0)
                w.set_status(vb[k] if k < len(vb) else "")
                if k < len(vbch) and vbch[k] > 0 and s["net"].get("ch") != vbch[k]:
                    s["net"]["ch"] = vbch[k]  # remembered: the channel list is filled from it next time, before any sender connects
            elif s["kind"] == "netch" and w is not None:
                par = next((p for p in self.cfg.d["strips"] if p["id"] == s["src"]["parent"]), None)
                k = par.get("net_k", 0) if par else 0
                w.set_status(vb[k] if par and k < len(vb) else "")
        for t in m.get("text", []):
            for line in netctl.apply_text(self, t):
                self.statusBar().showMessage(line, 3000)
        vt = m.get("vbtx", [])
        for w in self.bus_w.values():
            vk = w.cfg.get("vban", {}).get("k", -1)
            w.set_tx_status(vt[vk] if 0 <= vk < len(vt) and w.cfg["vban"].get("on") else "")
        budget = max(1, int(m["buf"] / m["sr"] * 1e6))
        dsp = 100.0 * m["max_us"] / budget
        t = theme.cur()
        ok = self.eng.ok
        self.hstat.setText(
            f"<span style='color:{t['green'] if ok else t['red']}'>&#9679;</span> "
            f"{'ENGINE OK' if ok else 'ENGINE DOWN'} &nbsp; {m['sr']} Hz / {m['buf']} "
            f"&nbsp; xruns {m['xruns']} &nbsp; DSP {dsp:.0f}%")
        self.perf.setText(f"worst callback {m['max_us']} us of {budget} us")
        self.hrec.setText(f"<span style='color:{t['red']}'>&#9679; REC {self.rec.bus} {self.rec.elapsed()}</span>"
                          if self.rec.active else "")

    # ------------------------------------------------------------ layout
    def apply_layout(self):
        from .layout import build_container  # noqa: PLC0415
        if self.container is not None:
            for w in list(self.panes.values()) + [self.media]:
                w.setParent(None)  # keep the panes alive when the old container is dropped
            self.rl.removeWidget(self.container)
            self.container.setParent(None)
        self.container = build_container(self)
        self.rl.addWidget(self.container, 1)

    def set_theme(self, key):
        t = theme.set_cur(key)
        QApplication.instance().setStyleSheet(theme.stylesheet(t))
        self.cfg.d["theme"] = key
        self._logo_text()
        self.rebuild()
        self.apply_layout()
        self.root.update()

    def _menus(self):
        mb = self.mbar
        f = mb.addMenu("File")
        f.addAction("Save config").triggered.connect(self.cfg.save)
        f.addSeparator()
        f.addAction("Exit").triggered.connect(self.close)
        en = mb.addMenu("Engine")
        en.addAction("Reset xrun / DSP counters").triggered.connect(lambda: self.eng.send(c="reset_stats"))
        en.addAction("System settings (buffer size)...").triggered.connect(lambda: SystemDialog(self.eng, self).exec())
        en.addSeparator()
        self.as_act = en.addAction("Start VertiMeeter at login")
        self.as_act.setCheckable(True)
        self.as_act.setChecked(autostart.is_enabled())
        self.am_act = en.addAction("   ...start minimized")
        self.am_act.setCheckable(True)
        self.am_act.setChecked(bool(self.cfg.d.get("start_minimized")))
        if not autostart.supported():
            for a in (self.as_act, self.am_act):
                a.setEnabled(False)
                a.setToolTip("Start at login is Linux only for now (not verified on Windows).")
        self.as_act.toggled.connect(lambda _v: self._autostart_changed())
        self.am_act.toggled.connect(lambda _v: self._autostart_changed())
        self.rec_menu = mb.addMenu("Record")
        self.rec_menu.aboutToShow.connect(self._fill_rec_menu)
        tl = mb.addMenu("Tools")
        am = tl.addAction("Audio map / patch bay...")
        am.triggered.connect(self.open_patch)
        if IS_WIN:
            am.setEnabled(False)
            am.setToolTip("Port patching is PipeWire / JACK only. On Windows each strip and bus picks its device directly.")
        tl.addSeparator()
        scenes.ensure(self.cfg.d)
        self._dlg = {}
        tl.addAction("Preset scenes (64)...").triggered.connect(lambda: self._open_dlg("scn", scenes.ScenesDialog))
        tl.addAction("Macro buttons (80)...").triggered.connect(lambda: self._open_dlg("mac", scenes.MacroDialog))
        tl.addAction("MIDI mapping...").triggered.connect(self.open_midi)
        self.hk_act = tl.addAction("Global hotkeys (macro keys)")
        self.hk_act.setCheckable(True)
        self.hk_act.setChecked(bool(self.cfg.d.get("hotkeys_on")))
        self.hk_act.toggled.connect(self.set_hotkeys)
        tl.addAction("Streamer view...").triggered.connect(self.open_streamer)
        for n in range(1, 13):  # F1-F12 recall scenes 1-12 (while this window is focused)
            QShortcut(QKeySequence(f"F{n}"), self, activated=lambda k=n - 1: self.statusBar().showMessage(self.scene_recall(k), 4000))
        th = mb.addMenu("Theme")
        th.addSection("THEME")
        g1 = QActionGroup(self)
        for key, name in (("purple", "VertiMeeter '98 (purple)"), ("potato", "Voicemeeter Potato")):
            a = th.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.cfg.d["theme"] == key)
            g1.addAction(a)
            a.triggered.connect(lambda _c, k=key: self.set_theme(k))
        th.addSection("LAYOUT")
        g2 = QActionGroup(self)
        self.layout_actions = {}
        for key, name in (("default", "Default"), ("potato", "Potato"), ("custom", "Custom")):
            a = th.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.cfg.d["layout"]["preset"] == key)
            g2.addAction(a)
            a.triggered.connect(lambda _c, k=key: self.set_preset(k))
            self.layout_actions[key] = a
        self.custom_menu = th.addMenu("Custom layout")
        self.custom_menu.aboutToShow.connect(self._fill_custom_menu)
        th.addSeparator()
        self.lock_act = th.addAction("Lock 16:9")
        self.lock_act.setCheckable(True)
        self.lock_act.setChecked(self.cfg.d["layout"]["lock169"])
        self.lock_act.toggled.connect(self._lock)
        h = mb.addMenu("Help")
        h.addAction("About").triggered.connect(
            lambda: self.statusBar().showMessage(f"VertiMeeter '98 {VERSION}", 5000))

    def _autostart_changed(self):
        """Menu toggles: write or remove the login autostart entry and remember the 'minimized' choice."""
        self.cfg.d["start_minimized"] = self.am_act.isChecked()
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        ok = autostart.set_enabled(root, self.as_act.isChecked(), self.am_act.isChecked())
        self.statusBar().showMessage(
            ("Start at login: " + ("on" if self.as_act.isChecked() else "off")) if ok else "Could not change the login autostart entry", 5000)

    # ------------------------------------------------------------ inputs (MIDI, hotkeys), streamer, insert watchdog
    def _inputs_init(self):
        self._hk, self._mi = hotkeys.HotkeyListener(), midi.MidiListener()
        self._hk_cap = self._mi_cap = None
        self._hk.combo.connect(self._on_combo)
        self._mi.message.connect(self._on_midi)
        for lst in (self._hk, self._mi):
            lst.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.streamer = None
        if self.cfg.d.get("hotkeys_on"):
            self._hk.start()
        if self.cfg.d.get("midi_on"):
            self._mi.start()
        self.fxpanel.c5_requested.connect(self._pick_c5)
        self._wd = QTimer(self)
        self._wd.timeout.connect(self._fx_watch)
        self._wd.start(1000)

    def set_hotkeys(self, on):
        self.cfg.d["hotkeys_on"] = bool(on)
        if on:
            self._hk.start()
        elif self._hk_cap is None:
            self._hk.stop()

    def set_midi(self, on):
        self.cfg.d["midi_on"] = bool(on)
        if on:
            self._mi.start()
        elif self._mi_cap is None:
            self._mi.stop()

    def hotkey_capture(self, cb):
        self._hk_cap = cb
        self._hk.start()

    def midi_learn(self, cb):
        self._mi_cap = cb
        self._mi.start()

    def _on_combo(self, combo):
        if self._hk_cap is not None:
            cb, self._hk_cap = self._hk_cap, None
            if not self.cfg.d.get("hotkeys_on"):
                self._hk.stop()
            cb(combo)
            return
        for k, m in enumerate(self.cfg.d["macros"]):
            if m.get("key") and hotkeys.normalize(m["key"]) == combo:
                self.run_macro(k)

    def _on_midi(self, kind, ch, num, val):
        dlg = self._dlg.get("midi")
        if dlg is not None:
            dlg.show_msg(kind, ch, num, val)
        if self._mi_cap is not None:
            cb, self._mi_cap = self._mi_cap, None
            if not self.cfg.d.get("midi_on"):
                self._mi.stop()
            cb(midi.token(kind, ch, num))
            return
        pressed = val >= 64 if kind == "cc" else val > 0
        for k, m in enumerate(self.cfg.d["macros"]):
            if m.get("midi") and pressed and midi.matches(m["midi"], kind, ch, num):
                self.run_macro(k)
        for row in self.cfg.d.get("midimap", []):
            if row.get("msg") and midi.matches(row["msg"], kind, ch, num):
                txt = controls.apply_row(self, row, val)
                if txt:
                    self.statusBar().showMessage("MIDI: " + txt, 2500)

    def open_midi(self):
        self._open_dlg("midi", lambda w: controls.MidiDialog(w))

    def open_streamer(self):
        if self.streamer is None:
            self.streamer = controls.StreamerView(self)
        self.streamer.show()
        self.streamer.raise_()

    def _pick_c5(self):
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        ids = list(self.bus_w)
        bid, ok = QInputDialog.getItem(self, "C5 multiband compressor", "Bus:", ids, 0, False)
        if ok and bid in self.bus_w:
            self.bus_w[bid].open_c5()

    def _fx_watch(self):
        """A crashed insert host must never leave a strip silent: turn that insert off."""
        h = fxhost.host()
        for sid, p in list(h.procs.items()):
            if p.poll() is None:
                continue
            h.procs.pop(sid, None)
            c = next((s for s in self.cfg.d["strips"] if s["id"] == sid), None)
            if sid == "MEDIA":
                c = self.cfg.d["mp"].get("dsp")
            if c is not None and c.get("ins", {}).get("on"):
                c["ins"]["on"] = False
                self.eng.send(c="strip", i=c["slot"], ins_on=False)
                w = self.media.dsp if sid == "MEDIA" else self.strip_w.get(sid)
                if w is not None:
                    w._fx_mark()
                self.statusBar().showMessage(f"Insert effect on {sid} stopped - insert switched off", 6000)

    # ------------------------------------------------------------ scenes / macros
    def _open_dlg(self, key, cls):
        d = self._dlg.get(key)
        if d is None:
            d = self._dlg[key] = cls(self)
        d.refresh()
        d.show()
        d.raise_()

    def refresh_mix(self):
        """Config values changed behind the widgets' backs (scene / macro): rebuild them and re-send everything."""
        self.rebuild()
        self.media.reload_mp()

    def scene_store(self, k):
        sc = self.cfg.d["scenes"]
        sc["data"][str(k)] = scenes.snapshot(self.cfg.d)
        self.cfg.save()
        self.statusBar().showMessage(f"Stored current mix in scene {k + 1}", 4000)

    def scene_recall(self, k):
        snap = self.cfg.d["scenes"]["data"].get(str(k))
        if snap is None:
            return f"Scene {k + 1} is empty"
        scenes.apply_snapshot(self.cfg.d, snap)
        self.refresh_mix()
        name = self.cfg.d["scenes"]["names"][k]
        msg = f"Scene {k + 1}{' ' + name if name else ''} recalled"
        self.statusBar().showMessage(msg, 4000)
        return msg

    def run_macro(self, k):
        m = self.cfg.d["macros"][k]
        out = [scenes.run_action(self, a) for a in m["actions"]]
        self.statusBar().showMessage(f"Macro {m['name'] or k + 1}: " + ("; ".join(out) if out else "no actions"), 5000)

    # ------------------------------------------------------------ recorder
    def _fill_rec_menu(self):
        m = self.rec_menu
        m.clear()
        if self.rec.active:
            m.addAction(f"Stop recording {self.rec.bus} ({self.rec.elapsed()})").triggered.connect(self._stop_rec)
            return
        for b in self.cfg.d["buses"]:
            m.addAction(f"Record {b['id']} to WAV...").triggered.connect(lambda _c, bid=b["id"]: self._start_rec(bid))

    def _start_rec(self, bid):
        d = os.path.expanduser("~/Music")
        name = time.strftime(f"VertiMeeter_{bid}_%Y%m%d_%H%M%S.wav")
        path, _ = QFileDialog.getSaveFileName(self, f"Record {bid}", os.path.join(d, name), "WAV (*.wav)")
        if path:
            if not self.rec.start(bid, path):
                self.statusBar().showMessage("Already recording", 3000)

    def _stop_rec(self):
        p = self.rec.stop()
        if p:
            self.statusBar().showMessage(f"Saved {p}", 6000)

    # ------------------------------------------------------------ custom layout
    def build_custom(self):
        lay = self.cfg.d["layout"]
        if not hasattr(self, "canvas"):
            self.canvas = Canvas({**self.panes, "media": self.media})
            self.custom = CustomContainer(self.canvas)
            self.canvas.changed.connect(self._canvas_changed)
            self.custom.save.connect(lambda: self.save_custom(False))
            self.custom.save_as.connect(lambda: self.save_custom(True))
            self.custom.revert.connect(self.revert_custom)
            self.custom.done.connect(self.done_custom)
        if not lay.get("rects"):
            lay["rects"] = default_rects(widths=dict(hw=300, vi=450, fx=215), media_h=lay.get("media_h", 190))  # starts from Default
        self.canvas.rects = {k: list(v) for k, v in lay["rects"].items()}
        self.canvas.hidden = set(lay.get("hidden", []))
        self.canvas.attach()
        self.custom.set_edit(bool(lay.get("edit")), lay.get("custom", ""))
        return self.custom

    def _canvas_changed(self):
        self.cfg.d["layout"]["rects"] = {k: list(v) for k, v in self.canvas.rects.items()}

    def save_custom(self, ask: bool):
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        lay = self.cfg.d["layout"]
        name = lay.get("custom", "")
        if ask or not name:
            name, ok = QInputDialog.getText(self, "Save custom layout", "Layout name:", text=name or "My layout")
            if not ok or not name.strip():
                return
            name = name.strip()
        self._canvas_changed()
        lay["saved"][name] = dict(rects={k: list(v) for k, v in self.canvas.rects.items()}, hidden=sorted(self.canvas.hidden))
        lay["custom"] = name
        self.custom.set_edit(bool(lay.get("edit")), name)
        self.cfg.save()
        self.statusBar().showMessage(f"Saved custom layout '{name}'", 4000)

    def load_custom(self, name: str):
        lay = self.cfg.d["layout"]
        s = lay["saved"].get(name)
        if not s:
            return
        lay["rects"] = {k: list(v) for k, v in s["rects"].items()}
        lay["hidden"] = list(s.get("hidden", []))
        lay["custom"] = name
        lay["preset"] = "custom"
        self.layout_actions["custom"].setChecked(True)
        self.apply_layout()

    def revert_custom(self):
        lay = self.cfg.d["layout"]
        name = lay.get("custom", "")
        if name and name in lay["saved"]:
            self.load_custom(name)
        else:
            lay["rects"] = {}
            lay["hidden"] = []
            self.apply_layout()

    def done_custom(self):
        self.cfg.d["layout"]["edit"] = False
        self.apply_layout()

    def _fill_custom_menu(self):
        m = self.custom_menu
        m.clear()
        lay = self.cfg.d["layout"]
        e = m.addAction("Edit layout (drag panes, drag edges)")
        e.setCheckable(True)
        e.setChecked(bool(lay.get("edit")) and lay["preset"] == "custom")
        e.triggered.connect(self._toggle_edit)
        m.addAction("Save").triggered.connect(lambda: self.save_custom(False))
        m.addAction("Save as...").triggered.connect(lambda: self.save_custom(True))
        m.addAction("Revert to Default arrangement").triggered.connect(self._reset_custom)
        pv = m.addMenu("Show panes")
        for k in ("hw", "vi", "fx", "ha", "vb", "media"):
            a = pv.addAction(k.upper())
            a.setCheckable(True)
            a.setChecked(k not in lay.get("hidden", []))
            a.triggered.connect(lambda on, kk=k: self._pane_visible(kk, on))
        m.addSeparator()
        sv = m.addMenu("Load saved")
        names = sorted(lay["saved"])
        if not names:
            sv.addAction("(none saved yet)").setEnabled(False)
        for n in names:
            sv.addAction(n).triggered.connect(lambda _c, nn=n: self.load_custom(nn))
        dv = m.addMenu("Delete saved")
        for n in names:
            dv.addAction(n).triggered.connect(lambda _c, nn=n: lay["saved"].pop(nn, None))

    def _toggle_edit(self, on):
        self.cfg.d["layout"]["edit"] = bool(on)
        if on:
            self.cfg.d["layout"]["preset"] = "custom"
            self.layout_actions["custom"].setChecked(True)
        self.apply_layout()

    def _reset_custom(self):
        self.cfg.d["layout"]["rects"] = {}
        self.cfg.d["layout"]["hidden"] = []
        self.apply_layout()

    def _pane_visible(self, key, on):
        lay = self.cfg.d["layout"]
        h = set(lay.get("hidden", []))
        (h.discard if on else h.add)(key)
        lay["hidden"] = sorted(h)
        if lay["preset"] == "custom":
            self.apply_layout()

    def set_preset(self, key):
        self.cfg.d["layout"]["preset"] = key
        if key != "custom":
            self.cfg.d["layout"]["edit"] = False
        self.apply_layout()

    def _lock(self, on):
        self.cfg.d["layout"]["lock169"] = on
        if on:
            self._fit()

    def _fit(self):
        if not self.cfg.d["layout"]["lock169"] or self._fixing or self.isMaximized() or self.isFullScreen():
            return
        w = self.width()
        h = round(w * 9 / 16)
        if abs(h - self.height()) > 1:
            self._fixing = True
            self.resize(w, h)
            self._fixing = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def closeEvent(self, e):
        for step in (self.rec.stop, self._hk.stop, self._mi.stop, fxhost.host().stop_all, self.media.shutdown,
                     self.cfg.save, self.dw.stop):
            try:
                step()
            except Exception as ex:  # noqa: BLE001  (one failing step must not leave the others running)
                log("ERROR", f"shutdown: {ex}")
        super().closeEvent(e)
