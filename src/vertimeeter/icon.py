"""Program icon: the VM'98 logo, painted in code (no image file needed)."""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap


def make_pixmap(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    s = size / 256.0
    p.fillRect(QRectF(0, 0, size, size), QColor("#0b0b10"))
    g = QLinearGradient(0, 0, 0, size * 0.34)
    g.setColorAt(0, QColor("#1d0636"))
    g.setColorAt(1, QColor("#8a35e0"))
    p.fillRect(QRectF(0, 0, size, size * 0.34), g)
    p.setPen(QPen(QColor("#a64dff"), max(2, 10 * s)))
    p.drawRect(QRectF(5 * s, 5 * s, size - 10 * s, size - 10 * s))
    f = QFont("DejaVu Sans")
    f.setBold(True)
    f.setPixelSize(int(104 * s))
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 56 * s, size, 110 * s), Qt.AlignmentFlag.AlignCenter, "VM")
    f.setPixelSize(int(84 * s))
    f.setItalic(True)
    p.setFont(f)
    p.setPen(QColor("#c08cff"))
    p.drawText(QRectF(0, 150 * s, size, 90 * s), Qt.AlignmentFlag.AlignCenter, "'98")
    p.end()
    return pm


def make_icon() -> QIcon:
    ic = QIcon()
    for n in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(make_pixmap(n))
    return ic


def install_desktop_entry(project_dir: str) -> None:
    """Make the taskbar show the VM'98 icon instead of the generic one: the desktop shell matches the window's
    app id to a .desktop file, so write one (user-local, harmless, only if missing or out of date)."""
    import os
    import sys
    if sys.platform == "win32":
        return  # the taskbar icon comes from QApplication.setWindowIcon + the AppUserModelID set in plat.apply()
    home = os.path.expanduser("~/.local/share")
    try:
        os.makedirs(f"{home}/applications", exist_ok=True)
        os.makedirs(f"{home}/icons", exist_ok=True)
        icon = f"{home}/icons/vertimeeter.png"
        make_pixmap(256).save(icon)
        entry = (f"[Desktop Entry]\nType=Application\nName=VertiMeeter\nComment=Voicemeeter-style mixer for PipeWire\n"
                 f"Exec={project_dir}/vertimeeter.sh\nIcon={icon}\nTerminal=false\nStartupWMClass=vertimeeter\n"
                 f"Categories=AudioVideo;Audio;Mixer;\n")
        path = f"{home}/applications/vertimeeter.desktop"
        try:
            old = open(path).read()
        except OSError:
            old = ""
        if old != entry:
            open(path, "w").write(entry)
    except OSError:
        pass
