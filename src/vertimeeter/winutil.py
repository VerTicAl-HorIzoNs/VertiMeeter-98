"""Windows replacement for pwutil.py (imported at the bottom of pwutil on Windows, so every `pwutil.x()` call keeps working).

There is no PipeWire here. Devices are listed and routed by the engine itself (WASAPI via cpal): the GUI asks it with
`dev_list` and tells it which device feeds which strip (`dev_in`) and which device a bus plays on (`dev_out`).
Things that only exist on PipeWire (per-app routing, virtual cable nodes, port patching) return "nothing" instead of failing.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

from .log import log

DEFAULT = "@default"  # the engine resolves this to the Windows default device
_eng = None
_cache = {"t": 0.0, "inputs": [], "outputs": []}
_lock = threading.Lock()


def _run(args, timeout=6):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def _json(args):
    rc, out, _ = _run(args)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def set_engine(eng) -> None:
    global _eng
    _eng = eng
    from . import winmedia  # noqa: PLC0415
    winmedia.FEED.attach(eng)


def _devs() -> dict:
    with _lock:
        now = time.time()
        if _eng is not None and getattr(_eng, "ok", False) and now - _cache["t"] > 1.5:
            _cache["t"] = now  # even a failed ask waits 1.5 s before the next one
            r = _eng.call(timeout=2.0, c="dev_list")
            if r.get("ok"):
                _cache["inputs"], _cache["outputs"] = list(r.get("inputs", [])), list(r.get("outputs", []))
        return _cache


def list_sources():
    """[(device name, description)]: capture devices and 'Loopback: <playback device>' (what that device plays)."""
    return [(DEFAULT, "Windows default input")] + [(n, n) for n in _devs()["inputs"]]


def list_sinks():
    return [(DEFAULT, "Windows default output")] + [(n, n) for n in _devs()["outputs"]]


def default_sink() -> str:
    return DEFAULT


def default_source() -> str:
    return DEFAULT


def set_default_sink(name: str) -> bool:
    return False


def set_default_source(name: str) -> bool:
    return False


def die_with_parent(argv, sig="TERM"):
    """Nothing to prefix: plat.apply() put VertiMeeter in a kill-on-close job, so every child dies with it."""
    return list(argv)


def sweep_stale(sock: str) -> int:
    return 0


def cleanup_modules(prefix: str = "VertiMeeter_") -> None:
    return None


class NodeHolder:
    """PipeWire creates virtual cable nodes; Windows cannot without a driver, so this holds nothing."""

    def start(self) -> bool:
        return True

    def create(self, name: str, desc: str, media_class: str) -> None:
        return None

    def stop(self) -> None:
        return None


def list_sink_inputs():
    return []  # per-app routing: use Windows Settings > Sound > Volume mixer to send an app to a virtual cable


def move_sink_input(index: int, sink: str) -> bool:
    return False


def get_input_volume(index: int) -> int:
    return 100


def set_input_volume(index: int, pct: int) -> None:
    return None


def toggle_input_mute(index: int) -> None:
    return None


def load_module(name: str, *args: str):
    return None


def unload_module(mid) -> None:
    return None


def list_ports(direction: str):
    return []


def list_links():
    return set()


def link(a: str, b: str) -> bool:
    return False


def unlink(a: str, b: str) -> bool:
    return False


def node_id(name: str):
    return None


def rename_node(holder, name: str, desc: str, media_class: str) -> bool:
    return True


def pick_lr(ports):
    if not ports:
        return None
    if len(ports) == 1:
        return ports[0], ports[0]
    return ports[0], ports[1]


def set_stream_volume(tag: str, db: float) -> int:
    from . import winmedia  # noqa: PLC0415
    winmedia.FEED.set_gain_db(tag, db)
    return 1


def set_stream_volume_async(tag: str, db: float) -> None:
    set_stream_volume(tag, db)  # an in-memory number: nothing slow to hide


class WinRecorder:
    """Bus recorder: the engine writes the WAV (32-bit float, 48 kHz stereo)."""

    def __init__(self, linker):
        self.linker = linker
        self.bus = ""
        self.path = ""
        self.t0 = 0.0
        self._on = False

    @property
    def active(self) -> bool:
        return self._on

    def start(self, bus: str, path: str) -> bool:
        import os  # noqa: PLC0415

        from .config import BUS_IDS  # noqa: PLC0415
        eng = getattr(self.linker, "eng", None)
        if self._on or eng is None or bus not in BUS_IDS:
            return False
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        r = eng.call(timeout=3.0, c="rec", i=BUS_IDS.index(bus), path=path)
        if not r.get("ok"):
            log("ERROR", f"recording failed: {r.get('err', '?')}")
            return False
        self._on, self.bus, self.path, self.t0 = True, bus, path, time.time()
        log("OK", f"recording {bus} -> {path}")
        return True

    def stop(self) -> str:
        if not self._on:
            return ""
        eng = getattr(self.linker, "eng", None)
        if eng is not None:
            eng.call(timeout=5.0, c="rec_stop")
        self._on = False
        log("OK", f"recording saved: {self.path}")
        return self.path

    def elapsed(self) -> str:
        s = int(time.time() - self.t0)
        return f"{s // 60:02d}:{s % 60:02d}"
