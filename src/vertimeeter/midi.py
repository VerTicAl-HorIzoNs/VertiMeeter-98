"""MIDI input without extra dependencies: reads the ALSA raw MIDI devices (/dev/snd/midiC*D*), which is what USB
controllers (Launchpad, X-Touch Mini, nanoKONTROL, keyboards...) show up as. Listening only (no feedback / lights yet).

A message is described as kind:channel:number - `cc:1:7`, `note:1:36`, `pb:1:0` - with channel 0 meaning "any channel".
"""
from __future__ import annotations

import glob
import os
import select
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal


class MidiParser:
    """Byte stream -> (kind, channel 1-16, number, value 0-127). Handles running status; ignores sysex / clock."""

    def __init__(self) -> None:
        self.status = 0
        self.data: list[int] = []
        self.in_sysex = False

    def feed(self, chunk: bytes) -> list[tuple[str, int, int, int]]:
        out = []
        for b in chunk:
            if b >= 0xF8:  # realtime (clock, start...) can appear anywhere
                continue
            if b == 0xF0:
                self.in_sysex = True
                continue
            if b == 0xF7:
                self.in_sysex = False
                continue
            if b & 0x80:
                self.in_sysex = False
                self.status = b if b < 0xF0 else 0
                self.data = []
                continue
            if self.in_sysex or not self.status:
                continue
            self.data.append(b)
            hi, ch = self.status & 0xF0, (self.status & 0x0F) + 1
            need = 1 if hi in (0xC0, 0xD0) else 2
            if len(self.data) < need:
                continue
            d, self.data = self.data, []
            if hi == 0x90:
                out.append(("note", ch, d[0], d[1]))
            elif hi == 0x80:
                out.append(("note", ch, d[0], 0))
            elif hi == 0xB0:
                out.append(("cc", ch, d[0], d[1]))
            elif hi == 0xE0:
                out.append(("pb", ch, 0, ((d[1] << 7) | d[0]) >> 7))
        return out


IS_WIN = os.name == "nt"


def devices() -> list[str]:
    if IS_WIN:
        return _win_names()
    return sorted(glob.glob("/dev/snd/midiC*D*"))


def _win_names() -> list[str]:
    """Names of the Windows MIDI input devices (winmm)."""
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class Caps(ctypes.Structure):
        _fields_ = [("mid", wintypes.WORD), ("pid", wintypes.WORD), ("ver", wintypes.UINT), ("name", ctypes.c_wchar * 32),
                    ("support", wintypes.DWORD)]

    winmm = ctypes.WinDLL("winmm")
    out = []
    for i in range(winmm.midiInGetNumDevs()):
        c = Caps()
        if winmm.midiInGetDevCapsW(ctypes.c_size_t(i), ctypes.byref(c), ctypes.sizeof(c)) == 0:
            out.append(c.name)
    return out


def token(kind: str, ch: int, num: int) -> str:
    return f"{kind}:{ch}:{num}"


def matches(rule: str, kind: str, ch: int, num: int) -> bool:
    """rule 'cc:1:7' matches that exact message; channel 0 in the rule means any channel."""
    try:
        k, c, n = rule.split(":")
        return k == kind and int(n) == num and int(c) in (0, ch)
    except ValueError:
        return False


class MidiListener(QObject):
    message = pyqtSignal(str, int, int, int)  # kind, channel, number, value
    status = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._run = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._run = True
        self._thread = threading.Thread(target=self._loop_win if IS_WIN else self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._run = False

    def _loop_win(self) -> None:
        """winmm midiIn: one open handle per device, a callback queues the raw bytes, this thread parses them."""
        import ctypes  # noqa: PLC0415
        import queue  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        winmm = ctypes.WinDLL("winmm")
        proc_t = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)
        q: "queue.Queue[tuple[int, bytes]]" = queue.Queue()

        def cb(_h, msg, inst, p1, _p2):
            if msg == 0x3C3:  # MIM_DATA: status, data1, data2 packed in p1
                q.put((inst, bytes([p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF])))

        cbf = proc_t(cb)
        opened: dict[int, tuple[ctypes.c_void_p, MidiParser]] = {}
        last = 0.0
        while self._run:
            if time.time() - last > 4.0:
                last = time.time()
                for i in range(winmm.midiInGetNumDevs()):
                    if i in opened:
                        continue
                    h = ctypes.c_void_p()
                    if winmm.midiInOpen(ctypes.byref(h), i, cbf, ctypes.c_size_t(i), 0x30000) == 0:  # CALLBACK_FUNCTION
                        winmm.midiInStart(h)
                        opened[i] = (h, MidiParser())
                        self.status.emit(f"MIDI input on ({len(opened)} device{'s' if len(opened) != 1 else ''})")
            try:
                inst, data = q.get(timeout=0.25)
            except queue.Empty:
                continue
            if inst in opened:
                for kind, ch, num, val in opened[inst][1].feed(data):
                    self.message.emit(kind, ch, num, val)
        for h, _p in opened.values():
            winmm.midiInStop(h)
            winmm.midiInReset(h)
            winmm.midiInClose(h)

    def _loop(self) -> None:
        open_fds: dict[int, tuple[str, MidiParser]] = {}
        last, warned = 0.0, False
        while self._run:
            if time.time() - last > 4.0:
                last = time.time()
                for path in devices():
                    if path in (p for p, _ in open_fds.values()):
                        continue
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        open_fds[fd] = (path, MidiParser())
                        self.status.emit(f"MIDI input on ({len(open_fds)} device{'s' if len(open_fds) != 1 else ''})")
                    except PermissionError:
                        if not warned:
                            warned = True
                            self.status.emit("No permission to read MIDI devices (add yourself to the 'audio' group)")
                    except OSError:
                        pass
            if not open_fds:
                time.sleep(1.0)
                continue
            try:
                r, _, _ = select.select(list(open_fds), [], [], 0.5)
            except (OSError, ValueError):
                open_fds.clear()
                continue
            for fd in r:
                path, parser = open_fds[fd]
                try:
                    data = os.read(fd, 256)
                except BlockingIOError:
                    continue
                except OSError:
                    os.close(fd)
                    open_fds.pop(fd, None)
                    continue
                if not data:
                    os.close(fd)
                    open_fds.pop(fd, None)
                    continue
                for kind, ch, num, val in parser.feed(data):
                    self.message.emit(kind, ch, num, val)
        for fd in open_fds:
            try:
                os.close(fd)
            except OSError:
                pass
