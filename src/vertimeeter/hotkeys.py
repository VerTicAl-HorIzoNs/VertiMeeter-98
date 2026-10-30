"""Global hotkeys that work while another window has focus (also on Wayland), read straight from /dev/input.

Needs read access to the keyboard devices (on Arch: be in the `input` group). Keys are only listened to, never
grabbed or changed, so the focused app still receives them. Combos look like `ctrl+alt+f5` or `meta+m`.
"""
from __future__ import annotations

import select
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

import sys

try:
    if sys.platform == "win32":
        raise ImportError  # Windows uses a low-level keyboard hook (ctypes), not evdev
    import evdev
    from evdev import ecodes
except ImportError:  # optional dependency
    evdev = None
    ecodes = None

IS_WIN = sys.platform == "win32"

# Windows virtual-key code -> the same lower-case names evdev gives (so saved combos work on both systems)
_VK_MODS = {0xA0: "leftshift", 0xA1: "rightshift", 0xA2: "leftctrl", 0xA3: "rightctrl", 0xA4: "leftalt", 0xA5: "rightalt",
            0x5B: "leftmeta", 0x5C: "rightmeta"}
_VK_KEYS = {0x1B: "esc", 0x20: "space", 0x0D: "enter", 0x08: "backspace", 0x09: "tab", 0x26: "up", 0x28: "down", 0x25: "left",
            0x27: "right", 0x2D: "insert", 0x2E: "delete", 0x24: "home", 0x23: "end", 0x21: "pageup", 0x22: "pagedown",
            0x13: "pause", 0x91: "scrolllock", 0x14: "capslock", 0x90: "numlock", 0x6A: "kpasterisk", 0x6B: "kpplus",
            0x6D: "kpminus", 0x6E: "kpdot", 0x6F: "kpslash", 0xBA: "semicolon", 0xBB: "equal", 0xBC: "comma", 0xBD: "minus",
            0xBE: "dot", 0xBF: "slash", 0xC0: "grave", 0xDB: "leftbrace", 0xDC: "backslash", 0xDD: "rightbrace", 0xDE: "apostrophe"}


def vk_name(vk: int) -> str:
    """Windows virtual-key code -> evdev-style name ('f5', 'a', '7', 'kp1', 'leftctrl'); '' when unknown."""
    if vk in _VK_MODS:
        return _VK_MODS[vk]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"kp{vk - 0x60}"
    return _VK_KEYS.get(vk, "")

MOD_NAMES = {"LEFTCTRL": "ctrl", "RIGHTCTRL": "ctrl", "LEFTALT": "alt", "RIGHTALT": "alt",
             "LEFTSHIFT": "shift", "RIGHTSHIFT": "shift", "LEFTMETA": "meta", "RIGHTMETA": "meta"}
MOD_ORDER = ("ctrl", "alt", "shift", "meta")


def key_name(code: int) -> str:
    """evdev key code -> lower-case name without KEY_ ('f5', 'a', 'kp1'); '' if unknown."""
    if ecodes is None:
        return ""
    n = ecodes.KEY.get(code, "")
    if isinstance(n, list):
        n = n[0]
    return n[4:].lower() if n.startswith("KEY_") else ""


def normalize(combo: str) -> str:
    parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
    alias = {"control": "ctrl", "super": "meta", "win": "meta", "windows": "meta", "option": "alt"}
    parts = [alias.get(p, p) for p in parts]
    mods = [m for m in MOD_ORDER if m in parts]
    keys = [p for p in parts if p not in MOD_ORDER]
    return "+".join(mods + keys)


class KeyTracker:
    """Turns raw key events into combos. feed(code, value) -> combo string when a non-modifier key goes down."""

    def __init__(self) -> None:
        self.mods: set[str] = set()

    def feed(self, code: int, value: int, name: str | None = None) -> str | None:
        name = key_name(code) if name is None else name
        up = name.upper()
        if up in MOD_NAMES:
            if value:
                self.mods.add(MOD_NAMES[up])
            else:
                self.mods.discard(MOD_NAMES[up])
            return None
        if value != 1 or not name:
            return None
        return "+".join([m for m in MOD_ORDER if m in self.mods] + [name])


class HotkeyListener(QObject):
    combo = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._run = False
        self._thread: threading.Thread | None = None

    @staticmethod
    def available() -> bool:
        return IS_WIN or evdev is not None

    def start(self) -> bool:
        if IS_WIN:
            if self._thread and self._thread.is_alive():
                return True
            self._run = True
            self._thread = threading.Thread(target=self._loop_win, daemon=True)
            self._thread.start()
            return True
        if evdev is None:
            self.status.emit("Global hotkeys need python-evdev (pip install evdev)")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._run = False
        if IS_WIN and getattr(self, "_tid", 0):
            import ctypes  # noqa: PLC0415
            ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT ends the hook's message loop

    def _loop_win(self) -> None:
        """WH_KEYBOARD_LL hook: sees every key system-wide and never blocks or changes it (listen only)."""
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)

        class KBD(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        proc_t = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_t, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        tracker, down = KeyTracker(), set()
        hook = [None]

        def cb(n, wp, lp):
            if n == 0 and self._run:
                k = ctypes.cast(lp, ctypes.POINTER(KBD)).contents
                name = vk_name(k.vk)
                if name and wp in (0x100, 0x104, 0x101, 0x105):
                    is_down = wp in (0x100, 0x104)
                    if is_down and k.vk in down:
                        pass  # auto-repeat
                    else:
                        down.add(k.vk) if is_down else down.discard(k.vk)
                        c = tracker.feed(k.vk, 1 if is_down else 0, name)
                        if c:
                            self.combo.emit(c)
            return user32.CallNextHookEx(hook[0], n, wp, lp)

        cbf = proc_t(cb)  # keep a reference alive
        self._tid = kernel32.GetCurrentThreadId()
        hook[0] = user32.SetWindowsHookExW(13, cbf, kernel32.GetModuleHandleW(None), 0)
        if not hook[0]:
            self.status.emit("Global hotkeys: the keyboard hook could not be installed")
            return
        self.status.emit("Global hotkeys on (Windows keyboard hook)")
        msg = wintypes.MSG()
        while self._run and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(hook[0])
        self._tid = 0

    def _keyboards(self, have: dict) -> None:
        try:
            paths = evdev.list_devices()
        except OSError:
            paths = []
        denied = False
        for p in paths:
            if p in have:
                continue
            try:
                d = evdev.InputDevice(p)
                keys = d.capabilities().get(ecodes.EV_KEY, [])
                if ecodes.KEY_A in keys and ecodes.KEY_ENTER in keys:
                    have[p] = (d, KeyTracker())
                else:
                    d.close()
            except PermissionError:
                denied = True
            except OSError:
                pass
        if denied and not have:
            self.status.emit("No permission to read /dev/input (add yourself to the 'input' group, then log in again)")

    def _loop(self) -> None:
        have: dict = {}
        last_scan = 0.0
        while self._run:
            if time.time() - last_scan > 8.0:
                last_scan = time.time()
                before = len(have)
                self._keyboards(have)
                if have and len(have) != before:
                    self.status.emit(f"Global hotkeys on ({len(have)} keyboard{'s' if len(have) != 1 else ''})")
            if not have:
                time.sleep(1.0)
                continue
            fds = {d.fd: (p, d, t) for p, (d, t) in have.items()}
            try:
                r, _, _ = select.select(list(fds), [], [], 0.5)
            except (OSError, ValueError):
                have.clear()
                continue
            for fd in r:
                p, d, t = fds[fd]
                try:
                    for ev in d.read():
                        if ev.type == ecodes.EV_KEY:
                            c = t.feed(ev.code, ev.value)
                            if c:
                                self.combo.emit(c)
                except OSError:  # unplugged
                    have.pop(p, None)
        for d, _t in have.values():
            try:
                d.close()
            except OSError:
                pass
