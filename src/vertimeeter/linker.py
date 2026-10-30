"""Keeps PipeWire links in sync with the config. Runs in its own thread, never touches audio."""
from __future__ import annotations

import os
import threading

from . import pwutil
from .config import BUS_IDS
from .log import log

ENG = "VertiMeeter"


class Linker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.strips: list[dict] = []
        self.buses: list[dict] = []
        self.holder = pwutil.NodeHolder()
        self.made: set[str] = set()
        self.descs: dict[str, str] = {}  # what each virtual device is currently called (renames follow)
        self.extra: set = set()  # runtime links (recorder)
        self.patches: list = []  # [[out_port, in_port], ...] set by the GUI (audio map)
        self.ev = threading.Event()
        self.run = True
        self.t = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        if os.environ.get("VERTIMEETER_NOLINK"):
            return  # isolated test/screenshot instance: never touch the real graph
        self.holder.start()
        self.t.start()

    def stop(self) -> None:
        if os.environ.get("VERTIMEETER_NOLINK"):
            return
        self.run = False
        self.ev.set()
        self.holder.stop()  # virtual devices die with pw-cli

    def update(self, strips: list[dict], buses: list[dict]) -> None:
        with self.lock:
            self.strips = [dict(id=s["id"], kind=s["kind"], slot=s["slot"], device=s.get("device", ""),
                                label=s.get("label", s["id"])) for s in strips]
            self.buses = [dict(id=b["id"], device=b["device"], label=b.get("label", b["id"])) for b in buses]
        self.ev.set()

    # -------------------------------------------------------------------
    def _loop(self) -> None:
        while self.run:
            try:
                self._sync()
            except Exception as e:  # noqa: BLE001
                log("ERROR", f"linker: {e}")
            self.ev.wait(3.0)  # slow, only to catch hot-plugged devices
            self.ev.clear()

    def _ports_of(self, ports: list[str], node: str) -> list[str]:
        return [p for p in ports if p.startswith(node + ":")]

    def _sync(self) -> None:
        with self.lock:
            strips, buses = list(self.strips), list(self.buses)
        for s in strips:
            if s["kind"] in ("virt", "media"):
                desc = f"VertiMeeter {s['label']}"
                if s["id"] not in self.made:
                    self.holder.create(f"VertiMeeter_{s['id']}", desc, "Audio/Sink")
                    self.made.add(s["id"])
                    self.descs[s["id"]] = desc
                elif self.descs.get(s["id"]) != desc:  # renamed in the GUI: PipeWire can't edit it, so the node is recreated and its apps moved back
                    pwutil.rename_node(self.holder, f"VertiMeeter_{s['id']}", desc, "Audio/Sink")
                    self.descs[s["id"]] = desc
        for b in buses:
            if b["id"].startswith("B"):
                desc = f"VertiMeeter Out {b['label']}"
                if f"mic_{b['id']}" not in self.made:
                    self.holder.create(f"VertiMeeter_{b['id']}", desc, "Audio/Source/Virtual")
                    self.made.add(f"mic_{b['id']}")
                    self.descs["mic_" + b["id"]] = desc
                elif self.descs.get("mic_" + b["id"]) != desc:
                    pwutil.rename_node(self.holder, f"VertiMeeter_{b['id']}", desc, "Audio/Source/Virtual")
                    self.descs["mic_" + b["id"]] = desc
        outs, ins = pwutil.list_ports("out"), pwutil.list_ports("in")
        want: set[tuple[str, str]] = set()
        for s in strips:
            dst = (f"{ENG}:S{s['slot'] + 1}_L", f"{ENG}:S{s['slot'] + 1}_R")
            if s["kind"] in ("virt", "media"):
                src = pwutil.pick_lr(self._ports_of(outs, f"VertiMeeter_{s['id']}") and
                                     [p for p in outs if p.startswith(f"VertiMeeter_{s['id']}:monitor")])
            elif s["kind"] == "hw" and s["device"]:
                src = pwutil.pick_lr(self._ports_of(outs, s["device"]))
            else:
                src = None
            if src:
                want.add((src[0], dst[0]))
                want.add((src[1], dst[1]))
        for b in buses:
            if b["id"].startswith("B"):  # virtual microphone other apps can pick as an input device
                mic = self._ports_of(ins, f"VertiMeeter_{b['id']}")
                tgt = pwutil.pick_lr(mic)
                if tgt:
                    want.add((f"{ENG}:{b['id']}_L", tgt[0]))
                    want.add((f"{ENG}:{b['id']}_R", tgt[1]))
                continue
            if not b["device"]:
                continue
            tgt = pwutil.pick_lr(self._ports_of(ins, b["device"]))
            if tgt:
                want.add((f"{ENG}:{b['id']}_L", tgt[0]))
                want.add((f"{ENG}:{b['id']}_R", tgt[1]))
        want |= {(a, b) for a, b in self.patches} | set(self.extra)
        have = {(a, b) for a, b in pwutil.list_links() if a.startswith(ENG + ":") or b.startswith(ENG + ":")}
        for a, b in have - want:
            pwutil.unlink(a, b)
        for a, b in want - have:
            if pwutil.link(a, b):
                log("INFO", f"link {a} -> {b}")


from .plat import IS_WIN  # noqa: E402

if IS_WIN:  # no PipeWire links on Windows: the engine routes devices itself
    from .winlinker import WinLinker as Linker  # noqa: F401,E402
