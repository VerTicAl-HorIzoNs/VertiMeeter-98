"""PipeWire / PulseAudio helpers (pactl + pw-link)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time

from .log import log


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


def list_sinks():
    """[(node_name, description)] excluding our own virtual devices."""
    j = _json(["pactl", "--format=json", "list", "sinks"])
    res = []
    if j is not None:
        for s in j:
            if s["name"].startswith("VertiMeeter"):
                continue
            res.append((s["name"], s.get("description") or s["name"]))
        return res
    rc, out, _ = _run(["pactl", "list", "short", "sinks"])
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) > 1 and not p[1].startswith("VertiMeeter"):
            res.append((p[1], p[1]))
    return res


def list_sources():
    j = _json(["pactl", "--format=json", "list", "sources"])
    res = []
    if j is not None:
        for s in j:
            n = s["name"]
            if n.startswith("VertiMeeter") or n.endswith(".monitor") or s.get("monitor_of_sink") is not None:
                continue
            res.append((n, s.get("description") or n))
        return res
    rc, out, _ = _run(["pactl", "list", "short", "sources"])
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) > 1 and not p[1].endswith(".monitor") and not p[1].startswith("VertiMeeter"):
            res.append((p[1], p[1]))
    return res


def die_with_parent(argv, sig="TERM"):
    """Prefix a command so the kernel stops it when VertiMeeter dies, even on a crash or kill -9."""
    if shutil.which("setpriv"):
        return ["setpriv", "--pdeathsig", sig, *argv]
    return list(argv)


def _argv(pid: int) -> list[str]:
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").split("\0")[:-1]
    except OSError:
        return []


def _under_live_app(pid: int) -> bool:
    """True when this process belongs to a running VertiMeeter (never touch those)."""
    try:
        ppid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return False
    a = _argv(ppid)
    return len(a) >= 3 and a[-2:] == ["-m", "vertimeeter"]


def sweep_stale(sock: str) -> int:
    """Remove what an earlier VertiMeeter left behind after a crash or kill: its engine, its media player pw-cat
    and the pw-cli that owns the virtual devices. Only call this once the single-instance lock is held."""
    n = 0
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == os.getpid():
            continue
        pid, a = int(d), _argv(int(d))
        if not a or _under_live_app(pid):
            continue
        exe = os.path.basename(a[0])
        if exe == "vertimeeter-engine" and len(a) > 1 and a[1] == sock:
            sig = signal.SIGTERM
        elif exe == "pw-cat" and "--target" in a and a[a.index("--target") + 1 : a.index("--target") + 2] \
                and a[a.index("--target") + 1].startswith("VertiMeeter_"):
            sig = signal.SIGKILL
        else:
            continue
        try:
            os.kill(pid, sig)
            n += 1
        except OSError:
            pass
    dump = _json(["pw-dump"]) or []
    clients = {o["id"]: (o.get("info", {}).get("props", {}) or {}).get("application.process.id")
               for o in dump if o.get("type") == "PipeWire:Interface:Client"}
    for o in dump:
        pr = (o.get("info", {}).get("props", {}) or {}) if o.get("type") == "PipeWire:Interface:Node" else {}
        if not str(pr.get("node.name", "")).startswith("VertiMeeter_"):
            continue
        pid = clients.get(pr.get("client.id"))
        a = _argv(pid) if isinstance(pid, int) else []
        if a and os.path.basename(a[0]) == "pw-cli":
            if _under_live_app(pid):
                continue
            try:
                os.kill(pid, signal.SIGKILL)  # the devices die with the pw-cli that owns them
            except OSError:
                pass
        else:
            _run(["pw-cli", "destroy", str(o["id"])])
        n += 1
    if n:
        time.sleep(0.4)
        log("INFO", f"removed {n} leftover VertiMeeter process(es)/device(s) from an earlier run")
    return n


class NodeHolder:
    """Owns a long-lived pw-cli process; nodes it creates die with it (no leftovers on crash)."""

    def __init__(self) -> None:
        self.p = None

    def start(self) -> bool:
        try:
            self.p = subprocess.Popen(
                die_with_parent(["pw-cli"], "KILL"), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, text=True,
            )
            return True
        except OSError as e:
            log("ERROR", f"cannot start pw-cli: {e}")
            return False

    def create(self, name: str, desc: str, media_class: str) -> None:
        desc = desc.replace('"', "")
        cmd = (
            "create-node adapter { factory.name=support.null-audio-sink "
            f'node.name={name} node.description="{desc}" media.class={media_class} '
            "audio.position=[FL FR] monitor.channel-volumes=true }\n"
        )
        self.p.stdin.write(cmd)
        self.p.stdin.flush()

    def stop(self) -> None:
        if self.p is not None:
            try:
                self.p.terminate()
                self.p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
            self.p = None


def list_sink_inputs():
    """Playing app streams: [{index, app, sink (node name), corked}]."""
    j = _json(["pactl", "--format=json", "list", "sink-inputs"])
    sinks = _json(["pactl", "--format=json", "list", "sinks"])
    if j is None or sinks is None:
        return []
    idx = {s["index"]: s["name"] for s in sinks}
    out = []
    for si in j:
        pr = si.get("properties", {})
        app = pr.get("application.name") or pr.get("application.process.binary") or pr.get("node.name") or "app"
        out.append({"index": si["index"], "app": app, "sink": idx.get(si.get("sink"), ""),
                    "corked": bool(si.get("corked"))})
    return out


def move_sink_input(index: int, sink: str) -> bool:
    return _run(["pactl", "move-sink-input", str(index), sink])[0] == 0


def default_sink() -> str:
    return _run(["pactl", "get-default-sink"])[1].strip()


def default_source() -> str:
    return _run(["pactl", "get-default-source"])[1].strip()


def set_default_sink(name: str) -> bool:
    return _run(["pactl", "set-default-sink", name])[0] == 0


def set_default_source(name: str) -> bool:
    return _run(["pactl", "set-default-source", name])[0] == 0


def cleanup_modules(prefix: str = "VertiMeeter_") -> None:
    rc, out, _ = _run(["pactl", "list", "short", "modules"])
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) >= 3 and prefix in line:
            _run(["pactl", "unload-module", p[0]])


def load_module(name: str, *args: str):
    rc, out, err = _run(["pactl", "load-module", name, *args])
    if rc != 0:
        log("ERROR", f"load-module {name} failed: {err.strip()}")
        return None
    return out.strip()


def unload_module(mid) -> None:
    if mid:
        _run(["pactl", "unload-module", str(mid)])


def list_ports(direction: str):
    """direction 'out' (output ports) or 'in' (input ports)."""
    rc, out, _ = _run(["pw-link", "-o" if direction == "out" else "-i"])
    return [l.strip() for l in out.splitlines() if l.strip()]


def list_links():
    rc, out, _ = _run(["pw-link", "-l"])
    links = set()
    cur = None
    for line in out.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            cur = line.strip()
            continue
        s = line.strip()
        if s.startswith("|->") and cur:
            links.add((cur, s[3:].strip()))
    return links


def link(a: str, b: str) -> bool:
    return _run(["pw-link", a, b])[0] == 0


def unlink(a: str, b: str) -> bool:
    return _run(["pw-link", "-d", a, b])[0] == 0


def pick_lr(ports):
    """Choose (left, right) from a node's port list. Mono -> same port twice."""
    if not ports:
        return None
    fl = next((p for p in ports if p.endswith(("_FL", "_L"))), None)
    fr = next((p for p in ports if p.endswith(("_FR", "_R"))), None)
    if fl and fr:
        return fl, fr
    if len(ports) == 1:
        return ports[0], ports[0]
    return ports[0], ports[1]


def set_stream_volume(tag: str, db: float) -> int:
    """Set the volume of every playback stream whose properties mention `tag` (a media-player file). Returns how many."""
    j = _json(["pactl", "--format=json", "list", "sink-inputs"])
    if not j:
        return 0
    pct = 0 if db <= -59.5 else max(0, round(100.0 * 10 ** (db / 20.0)))
    n = 0
    for si in j:
        if any(tag in str(v) for v in si.get("properties", {}).values()):
            _run(["pactl", "set-sink-input-volume", str(si["index"]), f"{pct}%"])
            n += 1
    return n


def node_id(name: str):
    j = _json(["pw-dump"]) or []
    for o in j:
        if (o.get("info", {}).get("props", {}) or {}).get("node.name") == name:
            return o.get("id")
    return None


def rename_node(holder, name: str, desc: str, media_class: str) -> bool:
    """PipeWire cannot edit a node's description after creation, so recreate it under the same name and put the
    apps that were playing into it straight back (they hear a ~0.3 s gap, nothing else changes)."""
    held = [s["index"] for s in list_sink_inputs() if s["sink"] == name]
    nid = node_id(name)
    if nid is not None:
        _run(["pw-cli", "destroy", str(nid)])
        time.sleep(0.15)
    holder.create(name, desc, media_class)
    if held:
        time.sleep(0.4)
        for i in held:
            move_sink_input(i, name)
    return True


_vol_lock = threading.Lock()
_vol_want: dict[str, float] = {}
_vol_busy = False


def set_stream_volume_async(tag: str, db: float) -> None:
    """Never blocks the GUI: the newest value per tag is applied by one background thread (pactl is slow)."""
    global _vol_busy
    with _vol_lock:
        _vol_want[tag] = db
        if _vol_busy:
            return
        _vol_busy = True

    def run():
        global _vol_busy
        while True:
            with _vol_lock:
                if not _vol_want:
                    _vol_busy = False
                    return
                tag_, db_ = _vol_want.popitem()
            try:
                set_stream_volume(tag_, db_)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=run, daemon=True).start()


def get_input_volume(index: int) -> int:
    """Current volume of one playing app stream, in percent (100 = unity)."""
    j = _json(["pactl", "--format=json", "list", "sink-inputs"]) or []
    for si in j:
        if si["index"] == index:
            v = list((si.get("volume") or {}).values())
            if v:
                return int(round(sum(int(x.get("value_percent", "100%").rstrip("%")) for x in v) / len(v)))
    return 100


def set_input_volume(index: int, pct: int) -> None:
    _run(["pactl", "set-sink-input-volume", str(index), f"{max(0, min(150, int(pct)))}%"])


def toggle_input_mute(index: int) -> None:
    _run(["pactl", "set-sink-input-mute", str(index), "toggle"])


from .plat import IS_WIN as _IS_WIN  # noqa: E402

if _IS_WIN:  # Windows: same function names, backed by the engine's own device layer
    from .winutil import *  # noqa: F401,F403,E402
    from .winutil import _json, _run, WinRecorder  # noqa: F401,E402
