"""Windows version of the Linker: tells the engine which device feeds which strip and which device each bus plays on.
Same interface as linker.Linker, so the GUI does not care. The engine reconciles the real streams (and reopens a
device that was unplugged and comes back), so this only has to send changes."""
from __future__ import annotations

import os
import threading

from .config import BUS_IDS
from .log import log

DEVICE_KINDS = ("hw", "virt")  # strips that take a capture / loopback device


class WinLinker:
    def __init__(self) -> None:
        self.eng = None  # set by __main__ once the engine is up
        self.lock = threading.Lock()
        self.strips: list[dict] = []
        self.buses: list[dict] = []
        self.holder = None
        self.extra: set = set()
        self.patches: list = []
        self._sent_in: dict[int, str] = {}
        self._sent_out: dict[int, str] = {}

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def update(self, strips: list[dict], buses: list[dict]) -> None:
        if os.environ.get("VERTIMEETER_NOLINK") or self.eng is None or not getattr(self.eng, "ok", False):
            return
        want_in = {s["slot"]: (s.get("device", "") if s["kind"] in DEVICE_KINDS else "") for s in strips}
        want_out = {BUS_IDS.index(b["id"]): b.get("device", "") for b in buses}
        with self.lock:
            for slot in range(24):
                dev = want_in.get(slot, "")
                if self._sent_in.get(slot, "") != dev:
                    self.eng.send(c="dev_in", i=slot, name=dev)
                    self._sent_in[slot] = dev
                    if dev:
                        log("INFO", f"strip slot {slot + 1} <- {dev}")
            for b in range(len(BUS_IDS)):
                dev = want_out.get(b, "")
                if self._sent_out.get(b, "") != dev:
                    self.eng.send(c="dev_out", i=b, name=dev)
                    self._sent_out[b] = dev
                    if dev:
                        log("INFO", f"bus {BUS_IDS[b]} -> {dev}")
