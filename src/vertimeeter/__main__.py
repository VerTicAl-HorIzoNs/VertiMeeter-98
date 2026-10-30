import argparse
import os
import signal
import sys

from . import plat

from .config import CONFIG_DIR, CONFIG_PATH, VERSION, Config
from .log import log


def _single_instance():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fh = open(os.path.join(CONFIG_DIR, "lock"), "w")
    try:
        if plat.IS_WIN:
            import msvcrt  # noqa: PLC0415
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fh


def main() -> int:
    plat.apply()  # Windows: kill-on-close job for child processes, PATH for bundled tools
    ap = argparse.ArgumentParser(prog="vertimeeter", description="VertiMeeter '98 - Voicemeeter-style mixer for PipeWire (Linux) / WASAPI (Windows)")
    ap.add_argument("--reset", action="store_true", help="ignore saved config and start fresh")
    ap.add_argument("--screenshot", metavar="PNG", help="render the window to PNG and exit (testing)")
    ap.add_argument("--layout", choices=["default", "potato"], help="start with this layout")
    ap.add_argument("--theme", choices=["purple", "potato"], help="start with this theme")
    ap.add_argument("--add-media", action="append", metavar="FILE", help="pre-load a media file (testing)")
    ap.add_argument("--minimized", action="store_true", help="start minimized (used by the start-at-login entry)")
    args = ap.parse_args()

    lock = _single_instance()
    if lock is None:
        log("ERROR", "VertiMeeter is already running (only one instance allowed)")
        return 1

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from . import pwutil as pw
    from . import theme
    from .engineclient import EngineClient, sock_path
    from .linker import Linker
    from .mainwindow import MainWindow

    cfg = Config() if args.reset else Config().load()
    first = not os.path.exists(CONFIG_PATH) or args.reset
    if first:
        cfg.d["buses"][0]["device"] = pw.default_sink()
        src = pw.default_source()
        if src and not src.endswith(".monitor"):
            cfg.d["strips"][0]["device"] = src
        log("INFO", f"first run, config will be saved to {CONFIG_PATH}")
    for mp in args.add_media or []:  # test / screenshot helper: pre-load media files
        cfg.d["media"].append(dict(path=os.path.abspath(mp), loop=False, gain_db=0.0))
    if args.layout:
        cfg.d["layout"]["preset"] = args.layout
    if args.theme:
        cfg.d["theme"] = args.theme

    log("INFO", f"VertiMeeter {VERSION} starting")

    def _hook(t, v, tb):  # PyQt6 aborts the whole process on an unhandled exception: log it and keep running
        import traceback  # noqa: PLC0415
        log("ERROR", "unhandled " + "".join(traceback.format_exception(t, v, tb)).strip())

    sys.excepthook = _hook
    if not os.environ.get("VERTIMEETER_NOLINK"):
        pw.cleanup_modules()
        pw.sweep_stale(sock_path())
    eng = EngineClient()
    ok = eng.start()
    pw.set_engine(eng) if hasattr(pw, "set_engine") else None  # Windows: device lists + media feed come from the engine
    if plat.IS_WIN and ok:
        eng.send(c="latency", ms=int(cfg.d.get("engine", {}).get("lat_ms", 40)))
    linker = Linker()
    linker.eng = eng
    linker.start()

    app = QApplication(sys.argv[:1])
    from .icon import make_icon
    app.setWindowIcon(make_icon())
    app.setDesktopFileName("vertimeeter")
    if not plat.IS_WIN and not os.environ.get("VERTIMEETER_NOLINK") and not getattr(args, "screenshot", None):
        from .icon import install_desktop_entry
        install_desktop_entry(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    app.setStyle("Fusion")
    app.setApplicationName("VertiMeeter")
    theme.set_cur(cfg.d["theme"])
    app.setStyleSheet(theme.stylesheet(theme.cur()))
    win = MainWindow(cfg, eng, linker)
    if args.minimized and not getattr(args, "screenshot", None):
        win.showMinimized()
    else:
        win.show()
    if not ok:
        QTimer.singleShot(200, lambda: QMessageBox.critical(win, "Engine", "Engine failed to start - see terminal log"))

    signal.signal(signal.SIGINT, lambda *_: win.close())
    signal.signal(signal.SIGTERM, lambda *_: win.close())
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, lambda *_: win.close())
    keep = QTimer()
    keep.start(250)
    keep.timeout.connect(lambda: None)  # let Python handle signals

    if args.screenshot:
        def snap():
            win.grab().save(args.screenshot)
            log("OK", f"screenshot saved: {args.screenshot}")
            win.close()

        QTimer.singleShot(2500, snap)
    try:
        code = app.exec()
    finally:
        linker.stop()
        if plat.IS_WIN:
            from . import winmedia  # noqa: PLC0415
            winmedia.FEED.stop()
        eng.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
