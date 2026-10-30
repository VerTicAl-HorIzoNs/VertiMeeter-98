"""Windows media player path: ffmpeg decodes, Python mixes the playing files and feeds the engine over a loopback TCP link.

Linux uses `ffmpeg | pw-cat` into a PipeWire sink; Windows has no such sink, so the engine opens a small authenticated
local feed (see engine/src/feed.rs) bound to the media player slot. The engine answers with its buffer fill after every
read, which makes the engine's audio clock the pacemaker: Python only sends when the buffer has room (closed loop).
Per file gain, pause and position are exact because Python counts the frames it hands over.
"""
from __future__ import annotations

import select
import socket
import subprocess
import threading
import time

import numpy as np

from .config import MEDIA_SLOT
from .log import log

RATE = 48000
BLK = 480  # 10 ms
TARGET = 4800  # keep ~100 ms in the engine's feed buffer
KEEP = RATE * 8 * 3  # decoded bytes kept per file (3 s); ffmpeg is held back by the pipe beyond that


class Src:
    """One playing file: ffmpeg -> f32le stereo 48 kHz -> byte buffer."""

    def __init__(self, argv: list[str], tag: str, gain: float):
        self.tag, self.gain, self.cur = tag, gain, gain
        self.paused = False
        self.played = 0  # frames handed to the engine
        self.buf = bytearray()
        self.eof = False
        self.lock = threading.Lock()
        self.dead = False
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while not self.dead:
                with self.lock:
                    full = len(self.buf) > KEEP
                if full:
                    time.sleep(0.02)
                    continue
                d = self.proc.stdout.read(65536 // 4 * 4)
                if not d:
                    break
                with self.lock:
                    self.buf += d
        except (OSError, ValueError):
            pass
        self.eof = True

    def take(self, n: int) -> np.ndarray:
        """Up to n frames as float32 (m, 2); m < n only at the end of the file or when decoding is behind."""
        with self.lock:
            m = min(n, len(self.buf) // 8)
            raw = bytes(self.buf[: m * 8])
            del self.buf[: m * 8]
        self.played += m
        return np.frombuffer(raw, dtype="<f4").reshape(-1, 2)

    @property
    def finished(self) -> bool:
        return self.eof and len(self.buf) < 8

    def close(self) -> None:
        self.dead = True
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.stdout.close()
        except OSError:
            pass


class Feed:
    def __init__(self) -> None:
        self.eng = None
        self.srcs: list[Src] = []
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.run = False
        self.t: threading.Thread | None = None

    def attach(self, eng) -> None:
        self.eng = eng

    def add(self, s: Src) -> None:
        with self.lock:
            self.srcs.append(s)
        self._ensure_thread()

    def remove(self, s: Src) -> None:
        with self.lock:
            if s in self.srcs:
                self.srcs.remove(s)
        s.close()

    def set_gain_db(self, tag: str, db: float) -> None:
        g = 0.0 if db <= -59.5 else 10 ** (db / 20.0)
        with self.lock:
            for s in self.srcs:
                if s.tag == tag:
                    s.gain = g

    def stop(self) -> None:
        self.run = False
        with self.lock:
            for s in self.srcs:
                s.close()
            self.srcs.clear()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    # ----------------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self.t is None or not self.t.is_alive():
            self.run = True
            self.t = threading.Thread(target=self._loop, daemon=True)
            self.t.start()

    def _connect(self) -> bool:
        if self.eng is None or not getattr(self.eng, "ok", False):
            return False
        r = self.eng.call(timeout=3.0, c="feed_open", slot=MEDIA_SLOT)
        if not r.get("ok"):
            log("ERROR", f"media feed: engine refused ({r.get('err', '?')})")
            return False
        try:
            s = socket.create_connection((self.eng.host, int(r["port"])), timeout=3)
            s.sendall(f"AUTH {self.eng.token}\n".encode())
        except OSError as e:
            log("ERROR", f"media feed: cannot connect ({e})")
            return False
        s.setblocking(False)
        self.sock = s
        return True

    def _acks(self, fill: int) -> int:
        try:
            while select.select([self.sock], [], [], 0)[0]:
                d = self.sock.recv(4096)
                if not d:
                    raise OSError("engine closed the feed")
                k = len(d) // 4 * 4
                if k >= 4:
                    fill = int.from_bytes(d[k - 4:k], "little")
        except (BlockingIOError, InterruptedError):
            pass
        return fill

    def _mix(self, srcs: list[Src]) -> np.ndarray:
        out = np.zeros((BLK, 2), dtype=np.float32)
        for s in srcs:
            if s.paused:
                continue
            x = s.take(BLK)
            if len(x) == 0:
                continue
            ramp = np.linspace(s.cur, s.gain, len(x), dtype=np.float32)[:, None]  # no zipper noise on gain changes
            s.cur = s.gain
            out[: len(x)] += x * ramp
        return out

    def _loop(self) -> None:
        fill, idle = 0, 0
        while self.run:
            if self.sock is None and not self._connect():
                time.sleep(0.5)
                with self.lock:
                    if not self.srcs:
                        break
                continue
            try:
                fill = self._acks(fill)
                with self.lock:
                    srcs = [s for s in self.srcs]
                for s in srcs:
                    if s.finished and not s.paused and s.proc.poll() is not None:
                        pass  # Player.alive() reports the end; the owner removes it
                active = [s for s in srcs if not s.paused and not s.finished]
                if not active:
                    idle += 1
                    time.sleep(0.02)
                    if idle > 250 and not srcs:  # nothing for 5 s: let the thread go
                        break
                    continue
                idle = 0
                if fill >= TARGET:
                    time.sleep(0.003)
                    continue
                blk = self._mix(active)
                self.sock.setblocking(True)
                self.sock.sendall(blk.tobytes())
                self.sock.setblocking(False)
                fill += BLK
            except OSError as e:
                log("WARN", f"media feed lost ({e}); reconnecting")
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
        self.t = None


FEED = Feed()


class Player:
    """Same interface as media.Player on Linux: one file, start/pause/resume/kill/pos/alive."""

    safe_default = False

    def __init__(self, path: str, tag: str, node: str = ""):
        self.path, self.tag, self.node = path, tag, node
        self.src: Src | None = None
        self.base = 0.0
        self.paused = False
        self.safe = False
        self.cd = None

    @property
    def ff(self):
        """media.py asks `pl.ff is not None` to tell 'was started' from 'never started' (Linux: the ffmpeg process)."""
        return self.src

    def alive(self) -> bool:
        s = self.src
        return s is not None and not s.finished

    def start(self, pos=0.0, vol=1.0) -> None:
        self.kill()
        argv = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{pos:.2f}", "-i", self.path,
                "-vn", "-f", "f32le", "-ac", "2", "-ar", str(RATE), "pipe:1"]
        self.src = Src(argv, self.tag, max(0.0, min(4.0, vol)))
        self.base, self.paused = pos, False
        FEED.add(self.src)

    def pos(self) -> float:
        return self.base + (self.src.played / RATE if self.src else 0.0)

    def pause(self) -> None:
        if self.alive() and not self.paused:
            self.src.paused = True
            self.paused = True

    def resume(self) -> None:
        if self.alive() and self.paused:
            self.src.paused = False
            self.paused = False

    def kill(self) -> None:
        if self.src is not None:
            FEED.remove(self.src)
            self.src = None
        self.paused = False
