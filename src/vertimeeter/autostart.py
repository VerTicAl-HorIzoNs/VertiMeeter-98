"""Start VertiMeeter at login. Linux: a standard XDG autostart entry (works on KDE, GNOME, XFCE...).
Windows is not offered yet (nothing was verified there), so the menu item is greyed out on Windows."""
from __future__ import annotations

import os

from .plat import IS_WIN

NAME = "vertimeeter.desktop"


def _dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "autostart")


def path() -> str:
    return os.path.join(_dir(), NAME)


def supported() -> bool:
    return not IS_WIN


def is_enabled() -> bool:
    return supported() and os.path.isfile(path())


def entry(project_dir: str, minimized: bool) -> str:
    exe = os.path.join(project_dir, "vertimeeter.sh")
    cmd = f'"{exe}"' + (" --minimized" if minimized else "")
    icon = os.path.expanduser("~/.local/share/icons/vertimeeter.png")
    return ("[Desktop Entry]\nType=Application\nName=VertiMeeter\nComment=Voicemeeter-style mixer for PipeWire\n"
            f"Exec={cmd}\nIcon={icon}\nTerminal=false\nStartupWMClass=vertimeeter\n"
            "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\nX-KDE-autostart-after=panel\n")


def set_enabled(project_dir: str, on: bool, minimized: bool = False) -> bool:
    """Create / refresh / remove the autostart entry. Returns True on success."""
    if not supported():
        return False
    try:
        if on:
            os.makedirs(_dir(), exist_ok=True)
            tmp = path() + ".tmp"
            with open(tmp, "w") as f:
                f.write(entry(project_dir, minimized))
            os.replace(tmp, path())
        elif os.path.isfile(path()):
            os.remove(path())
        return True
    except OSError:
        return False
