"""Platform glue (Windows vs Linux): paths, process hygiene, the PATH for bundled tools. Imported first by __main__."""
from __future__ import annotations

import os
import subprocess
import sys

# VERTIMEETER_FAKE_WIN=1 runs the Windows code paths on Linux (engine soft backend + loopback TCP) for testing
REAL_WIN = sys.platform == "win32"
IS_WIN = REAL_WIN or os.environ.get("VERTIMEETER_FAKE_WIN") == "1"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def config_dir() -> str:
    if IS_WIN:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VertiMeeter")
    return os.path.expanduser("~/.config/vertimeeter")


def cache_dir() -> str:
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "VertiMeeter", "cache")
    return os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "vertimeeter")


def lowprio(argv: list[str]) -> list[str]:
    """Run a helper (ffmpeg, fluidsynth) at low priority so it never competes with the mixer."""
    if IS_WIN:
        return list(argv)  # handled by creationflags where it matters (see apply())
    return ["nice", "-n", "10", *argv]


_job = None


def _kill_on_close_job() -> None:
    """Every child process we start (engine, ffmpeg...) joins this job, and the job dies with us: no leftovers even
    after a crash or Task Manager 'End task'. Children of a process inside a job join it automatically."""
    global _job
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IOC(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IOC), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k.CreateJobObjectW.restype = wintypes.HANDLE
    h = k.CreateJobObjectW(None, None)
    if not h:
        return
    info = EXT()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k.SetInformationJobObject(wintypes.HANDLE(h), 9, ctypes.byref(info), ctypes.sizeof(info))
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject(wintypes.HANDLE(h), k.GetCurrentProcess())
    _job = h  # keep the handle open for the life of the process


def apply() -> None:
    """Call once, first thing. No-op on Linux."""
    if not REAL_WIN:
        return
    try:
        _kill_on_close_job()
    except Exception:  # noqa: BLE001
        pass
    # tools shipped next to the app (bin\ffmpeg.exe, bin\opus.dll ...) win over the PATH
    b = os.path.join(ROOT, "bin")
    if os.path.isdir(b):
        os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
    # never flash a console window for ffmpeg & co. (a windowed Python has none to inherit)
    orig = subprocess.Popen.__init__

    def init(self, *a, **kw):
        kw["creationflags"] = kw.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        orig(self, *a, **kw)

    subprocess.Popen.__init__ = init
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Vertical.VertiMeeter98")  # own taskbar icon group
    except Exception:  # noqa: BLE001
        pass
