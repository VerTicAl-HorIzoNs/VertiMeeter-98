"""Insert FX hosting.

VertiMeeter does not run plugins inside its own audio thread (a crashing plugin would take the whole mixer down).
Instead each strip has an insert loop in the engine: INSn_OUT_L/R -> any JACK / PipeWire effect -> INSn_IN_L/R.
This module starts that effect (jalv for LV2 plugins, or any command you give it, e.g. Carla) as its own process and
wires it into the loop. If the effect crashes, the strip is simply dry again after you turn the insert off.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import time

from . import pwutil
from .log import log

JALV_BINS = ("jalv.gtk3", "jalv.gtk", "jalv.qt6", "jalv.qt5", "jalv")


def engine_client() -> str:
    return os.environ.get("VERTIMEETER_CLIENT", "VertiMeeter")


def find_jalv() -> str | None:
    for b in JALV_BINS:
        p = shutil.which(b)
        if p:
            return p
    return None


def audio_ports(client: str) -> dict:
    """Audio ports of one JACK / PipeWire client: {'in': [names], 'out': [names]} (MIDI ports are left out)."""
    j = pwutil._json(["pw-dump"]) or []
    nodes = {o["id"]: (o.get("info", {}).get("props", {}) or {}).get("node.name", "") for o in j
             if o.get("type", "").endswith("Interface:Node")}
    res = {"in": [], "out": []}
    ports = []
    for o in j:
        if not o.get("type", "").endswith("Interface:Port"):
            continue
        p = o.get("info", {}).get("props", {}) or {}
        if nodes.get(p.get("node.id")) != client or "audio" not in str(p.get("format.dsp", "audio")):
            continue
        ports.append((int(p.get("port.id", 0)), p.get("port.direction"), p.get("port.name", "")))
    for _id, d, name in sorted(ports):
        if d in ("in", "out"):
            res[d].append(f"{client}:{name}")
    return res


def clients_with_audio() -> set[str]:
    j = pwutil._json(["pw-dump"]) or []
    nodes = {o["id"]: (o.get("info", {}).get("props", {}) or {}).get("node.name", "") for o in j
             if o.get("type", "").endswith("Interface:Node")}
    out = set()
    for o in j:
        if o.get("type", "").endswith("Interface:Port"):
            p = o.get("info", {}).get("props", {}) or {}
            if "audio" in str(p.get("format.dsp", "")) and p.get("object.path", "").find("monitor") < 0:
                out.add(nodes.get(p.get("node.id"), ""))
    out.discard("")
    return out


def link_loop(slot: int, client: str, eng: str | None = None) -> bool:
    """Connect strip `slot` (0-based) insert out -> client inputs and client outputs -> insert in."""
    eng = eng or engine_client()
    ap = audio_ports(client)
    if not ap["in"] or not ap["out"]:
        return False
    n = slot + 1
    src = (f"{eng}:INS{n}_OUT_L", f"{eng}:INS{n}_OUT_R")
    dst = (f"{eng}:INS{n}_IN_L", f"{eng}:INS{n}_IN_R")
    ins, outs = ap["in"], ap["out"]
    pairs = [(src[0], ins[0]), (src[1], ins[1] if len(ins) > 1 else ins[0]),
             (outs[0], dst[0]), (outs[1] if len(outs) > 1 else outs[0], dst[1])]
    ok = True
    for a, b in pairs:
        rc, _o, err = pwutil._run(["pw-link", a, b])
        if rc != 0 and "File exists" not in err:
            ok = False
    return ok


class FxHost:
    """One external effect process per strip id."""

    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}
        self.status: dict[str, str] = {}

    def alive(self, key: str) -> bool:
        p = self.procs.get(key)
        return p is not None and p.poll() is None

    def launch(self, key: str, slot: int, argv: list[str], client: str = "") -> bool:
        self.stop(key)
        if not argv or shutil.which(argv[0]) is None:
            self.status[key] = f"'{argv[0] if argv else ''}' not found"
            return False
        before = clients_with_audio()
        env = dict(os.environ)
        env["PIPEWIRE_PROPS"] = "{ node.autoconnect = false }"  # the effect must not wire itself to speakers
        try:
            self.procs[key] = subprocess.Popen(pwutil.die_with_parent(argv), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               start_new_session=True)
        except OSError as e:
            self.status[key] = f"cannot start: {e}"
            return False
        self.status[key] = "starting..."
        threading.Thread(target=self._wire, args=(key, slot, client, before), daemon=True).start()
        return True

    def _wire(self, key, slot, client, before):
        end = time.time() + 40
        while time.time() < end and self.alive(key):
            time.sleep(0.6)
            name = client
            if not name:  # unnamed effect: take the new client that has audio ports
                new = sorted(clients_with_audio() - before - {engine_client()})
                name = new[0] if new else ""
            if name and link_loop(slot, name):
                self.status[key] = f"running, wired to {name}"
                log("OK", f"insert FX {key}: {name} wired into strip insert {slot + 1}")
                return
        self.status[key] = "exited" if not self.alive(key) else "started, but no audio ports were found to wire"

    def stop(self, key: str) -> None:
        p = self.procs.pop(key, None)
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        if key in self.status:
            self.status[key] = "stopped"

    def stop_all(self) -> None:
        for k in list(self.procs):
            self.stop(k)


_HOST: FxHost | None = None


def host() -> FxHost:
    global _HOST
    if _HOST is None:
        _HOST = FxHost()
    return _HOST


def lv2_command(uri: str, client: str) -> list[str] | None:
    j = find_jalv()
    return [j, "-n", client, uri] if j else None


def split_cmd(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return re.split(r"\s+", cmd.strip())
