"""Persistent state (plain JSON, ~/.config/vertimeeter/config.json). No host/IP defaults, ever."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil

from .log import log

VERSION = "0.13.0"
from .plat import config_dir as _config_dir  # noqa: E402

CONFIG_DIR = os.environ.get("VERTIMEETER_CONFIG_DIR") or _config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

PANE_MAX = 8  # strips per input pane / buses per output pane
MAX_STRIPS = 16  # 8 hardware + 8 virtual (virtual, VBAN and OBAN strips share the virtual pane)
MEDIA_SLOT = 16  # engine slot of the media player (first slot after the strips)
BUS_IDS = [f"A{i}" for i in range(1, 9)] + [f"B{i}" for i in range(1, 9)]  # engine bus index = position here
KINDS = {"hw": "Hardware Input", "virt": "Virtual Input", "net": "Network (VBAN) Input", "oban": "Network Input (OBAN)"}


def rows_of(n: int, split) -> list[list[int]]:
    """Channel rows of a multichannel stream: pairs 1-2, 3-4 ...; an odd last channel is single; a pair index in `split` becomes two singles."""
    rows: list[list[int]] = []
    for p in range((max(1, n) + 1) // 2):
        a, b = 2 * p, 2 * p + 1
        if b < n and p not in split:
            rows.append([a, b])
        elif b < n:
            rows.extend([[a], [b]])
        else:
            rows.append([a])
    return rows


def row_label(chs, prefix: str = "CH") -> str:
    return f"{prefix} {chs[0] + 1}" if len(chs) == 1 else f"{prefix} {chs[0] + 1}-{chs[-1] + 1}"


def row_key(chs) -> str:
    return "-".join(str(c) for c in chs)


def net_feeds(strips: list, parent: dict) -> tuple[list[int], list[list[int]]]:
    """Engine receiver map for a network input: feed 0 is the strip itself (first row), then one feed per child strip."""
    n = int(parent.get("net", {}).get("ch", 2) or 2)
    rows = rows_of(n, set(parent.get("net", {}).get("split", [])))
    own = rows[0] if rows else [0, 1]
    slots, feeds = [parent["slot"]], [[own[0], own[-1]]]
    for s in strips:
        if s.get("kind") == "netch" and s.get("src", {}).get("parent") == parent["id"] and len(slots) < 8:
            chs = s["src"]["chs"]
            slots.append(s["slot"])
            feeds.append([chs[0], chs[-1]])
    return slots, feeds


def out_matrix(vb: dict, own_bus: str) -> tuple[list[int], list[int]]:
    """Output channel matrix for the engine: per channel a bitmask over BUS_IDS and a source mode (0 pair position, 1 L, 2 R, 3 L+R)."""
    n = max(1, min(8, int(vb.get("ch", 2))))
    m = vb.get("map") or {}
    rows_cfg = m.get("rows", {})
    mx, src = [0] * n, [0] * n
    for chs in rows_of(n, set(m.get("split", []))):
        r = rows_cfg.get(row_key(chs))
        buses = r.get("buses", []) if r is not None else ([own_bus] if chs[0] == 0 else [])
        mask = 0
        for b in buses:
            if b in BUS_IDS:
                mask |= 1 << BUS_IDS.index(b)
        mode = (r or {}).get("src", "LR")
        for ch in chs:
            mx[ch] = mask
            src[ch] = 0 if len(chs) == 2 else {"L": 1, "R": 2, "LR": 3}.get(mode, 3)
    return mx, src


def new_strip(kind: str, n: int, slot: int) -> dict:
    pre = {"hw": "H", "virt": "V", "net": "N", "oban": "O", "netch": "C"}[kind]
    names = {"hw": "Hardware Input", "virt": "Virtual Input", "net": "Network Input", "oban": "OBAN Input", "netch": "Network Channel"}
    return dict(id=f"{pre}{n}", kind=kind, slot=slot, label=f"{names[kind]} {n}", device="",
                gain_db=0.0, pan=0.0, sp_mode="pan", sp_x=0.0, sp_y=1.0, mute=False, solo=False, mono=False,
                sends={b: (b == "A1") for b in BUS_IDS},
                col_x=0.0, col_y=0.0, post=False, lim=False, lim_db=-6.0, kar=0, mc=False,
                net=dict(on=False, port=6990 if kind == "oban" else 6980, stream="", allow="", ch=2, split=[]),
                knobs=dict(comp=0.0, gate=0.0, dns=0.0, rvb=0.0, dly=0.0, bass=0.0, mid=0.0, treb=0.0,
                           ext1=0.0, ext2=0.0))


def new_mp() -> dict:
    """The ONE media player output: every file plays into the single sink VertiMeeter_Media_Player_Output,
    which feeds engine slot 16 (so there is one fader, one meter and one set of routes for the whole player)."""
    return dict(id="Media_Player_Output", kind="media", slot=MEDIA_SLOT, label="Media Player Output", device="",
                gain_db=0.0, mute=False, sends={"A1": True}, send_db={})


def new_bus(bid: str) -> dict:
    return dict(id=bid, label=bid, device="", gain_db=0.0, mute=False, mono=False, mode="Normal",
                sel=False, mon=False, vban=dict(on=False, addr="", port=6980, stream="", f32=False, k=-1, codec="vban_pcm", sr=48000, ch=2, dt=1, kbps=128, ms=20),
                knobs=dict(bass=0.0, mid=0.0, treb=0.0, delay=0.0, limit=0.0, rvb_ret=1.0, dly_ret=1.0))


def defaults() -> dict:
    return dict(
        version=VERSION, theme="purple",
        layout=dict(preset="default", lock169=True, edit=False, saved={}, custom="",
                    widths=dict(hw=300, vi=450, fx=170), media_h=170, rects={}),
        strips=[new_strip("hw", 1, 0), new_strip("virt", 1, 1), new_strip("virt", 2, 2)],
        buses=[new_bus("A1"), new_bus("B1")],
        media=[], mp=new_mp(), names={}, vban=dict(streams=[]), engine=dict(), patches=[],
    )


class Config:
    def __init__(self) -> None:
        self.d = defaults()
        self._last: str | None = None  # text of the file as last written / loaded (autosave compares against it)

    def load(self) -> "Config":
        try:
            with open(CONFIG_PATH) as f:
                d = json.load(f)
            v = tuple(int(x) for x in str(d.get("version", "0.0.0")).split(".")[:2])
            if v < (0, 5):  # old python-engine config: keep a backup, start fresh
                shutil.copy(CONFIG_PATH, CONFIG_PATH + ".pre-0.5.bak")
                log("WARN", "old config backed up; starting with defaults")
            else:
                base = defaults()
                base.update(d)
                base["layout"] = {**defaults()["layout"], **d.get("layout", {})}
                base["mp"] = {**new_mp(), **d.get("mp", {})}
                base["media"] = [dict(path=m["path"], loop=bool(m.get("loop", False)), gain_db=float(m.get("gain_db", 0.0)))
                                 for m in d.get("media", []) if m.get("path")]
                for s in base["strips"]:  # 0.7: nicer default names (renamed strips are left alone)
                    m = re.match(r"^(Mic|Virtual|Network) (\d+)$", s.get("label", ""))
                    if m:
                        s["label"] = {"Mic": "Hardware Input", "Virtual": "Virtual Input", "Network": "Network Input"}[m.group(1)] + " " + m.group(2)
                if base["mp"].get("slot") == 12:  # 0.11: the media player moved from slot 12 to slot 16
                    base["mp"]["slot"] = MEDIA_SLOT
                for s in base["strips"]:  # 0.11: 16 buses, new sends default to off
                    s["sends"] = {b: bool(s.get("sends", {}).get(b, False)) for b in BUS_IDS}
                base["mp"]["sends"] = {b: v for b, v in base["mp"].get("sends", {}).items() if b in BUS_IDS}
                ids = {s["id"] for s in base["strips"]}
                base["strips"] = [s for s in base["strips"] if s["kind"] != "netch" or s.get("src", {}).get("parent") in ids]  # 0.13: drop orphans
                self.d = base
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log("ERROR", f"config unreadable ({e}); using defaults")
            try:  # autosave would overwrite the broken file with defaults: keep a copy of it first
                shutil.copy(CONFIG_PATH, CONFIG_PATH + ".unreadable.bak")
            except OSError:
                pass
        return self

    def _text(self) -> str:
        self.d["version"] = VERSION
        return json.dumps(self.d, indent=1)

    def save(self) -> None:
        """Write the config atomically (temp file, fsync, rename) so a crash or hard reboot never leaves half a file."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        text = self._text()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
        self._last = text

    def save_if_changed(self) -> bool:
        """Autosave hook: writes only when something actually changed since the last write. Returns True if it wrote."""
        if self._text() == self._last:
            return False
        self.save()
        return True

    # ---- strips / buses -------------------------------------------------
    @staticmethod
    def pane_of(kind: str) -> str:
        return "hw" if kind == "hw" else "vi"

    def pane_full(self, kind: str) -> bool:
        p = self.pane_of(kind)
        return sum(1 for s in self.d["strips"] if self.pane_of(s["kind"]) == p) >= PANE_MAX

    def free_slot(self) -> int | None:
        used = {s["slot"] for s in self.d["strips"]}
        return next((i for i in range(MAX_STRIPS) if i not in used), None)

    def add_strip(self, kind: str) -> dict | None:
        if self.pane_full(kind):
            return None
        slot = self.free_slot()
        if slot is None:
            return None
        n = 1 + sum(1 for s in self.d["strips"] if s["kind"] == kind)
        ids = {s["id"] for s in self.d["strips"]}
        while new_strip(kind, n, slot)["id"] in ids:
            n += 1
        s = new_strip(kind, n, slot)
        if kind in ("net", "oban"):
            k = self.free_net_k()
            if k is None:
                return None  # engine has 8 network receivers
            s["net_k"] = k
        self.d["strips"].append(s)
        return s

    def free_net_k(self) -> int | None:
        used = {s.get("net_k") for s in self.d["strips"] if s["kind"] in ("net", "oban")}
        return next((k for k in range(8) if k not in used), None)

    def free_vban_tx(self) -> int | None:
        used = {b.get("vban", {}).get("k") for b in self.d["buses"] if b.get("vban", {}).get("on")}
        return next((k for k in range(4) if k not in used), None)

    def remove_strip(self, sid: str) -> None:
        self.d["strips"] = [s for s in self.d["strips"]
                            if s["id"] != sid and not (s["kind"] == "netch" and s.get("src", {}).get("parent") == sid)]

    def net_children(self, pid: str) -> list[dict]:
        return [s for s in self.d["strips"] if s["kind"] == "netch" and s.get("src", {}).get("parent") == pid]

    def add_net_child(self, parent: dict, chs: list[int]) -> dict | None:
        """A strip that plays channel(s) `chs` of a multichannel network input."""
        if self.pane_full("netch"):
            return None
        slot = self.free_slot()
        if slot is None:
            return None
        n = 1 + sum(1 for s in self.d["strips"] if s["kind"] == "netch")
        ids = {s["id"] for s in self.d["strips"]}
        while new_strip("netch", n, slot)["id"] in ids:
            n += 1
        s = new_strip("netch", n, slot)
        s["src"] = dict(parent=parent["id"], chs=list(chs))
        s["label"] = f"{parent['label']} {row_label(chs)}"[:24]
        self.d["strips"].append(s)
        return s

    def sync_net_children(self, parent: dict, want: list[list[int]]) -> bool:
        """Make the child strips of `parent` exactly the wanted channel rows. False if there is no room for all of them."""
        cur = {tuple(c["src"]["chs"]): c for c in self.net_children(parent["id"])}
        wanted = {tuple(w) for w in want}
        for chs, c in cur.items():
            if chs not in wanted:
                self.remove_strip(c["id"])
        ok = True
        for w in want:
            if tuple(w) not in cur and self.add_net_child(parent, list(w)) is None:
                ok = False
        return ok

    def free_buses(self, prefix: str) -> list[str]:
        used = {b["id"] for b in self.d["buses"]}
        return [b for b in BUS_IDS if b.startswith(prefix) and b not in used]

    def add_bus(self, bid: str) -> dict:
        b = new_bus(bid)
        self.d["buses"].append(b)
        self.d["buses"].sort(key=lambda x: BUS_IDS.index(x["id"]))
        return b

    def remove_bus(self, bid: str) -> None:
        self.d["buses"] = [b for b in self.d["buses"] if b["id"] != bid]

    def snapshot(self) -> dict:
        return copy.deepcopy(self.d)
