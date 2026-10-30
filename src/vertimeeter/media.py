"""Multi-file media player with ONE global output.

ffmpeg decodes any format (video -> audio only) straight into pw-cat. Every file plays into the single sink
'VertiMeeter_Media_Player_Output' (shown as 'VertiMeeter Media Player Output'), which feeds ONE engine slot (16).
That slot has one fader, one meter and one set of A/B routes for the whole player. Per file you only get
play / seek / loop / volume. Python is never in the audio data path."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time

from PyQt6.QtCore import QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea,
                             QVBoxLayout, QWidget)

from . import cdaudio, mediadsp, midifile, pwutil, theme
from .config import BUS_IDS
from .log import log
from .widgets import ClickLabel, HSlider, SeekBar

from .config import MEDIA_SLOT  # noqa: E402  (engine slot 16)
SINK = "VertiMeeter_Media_Player_Output"
FILTER = "Media (*);;MIDI (*.mid *.midi *.kar);;Audio (*.mp3 *.flac *.wav *.ogg *.opus *.m4a *.aac *.wma);;Video (*.mp4 *.mkv *.webm *.avi *.mov *.flv)"
VIDEO = (".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv", ".m4v", ".ts")
_seq = [0]


def probe_duration(path: str) -> float:
    if cdaudio.is_cd(path):  # a CD track: the length comes from the disc TOC (set by the caller)
        return 0.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def fmt_t(s: float) -> str:
    s = int(max(0, s))
    return f"{s // 60}:{s % 60:02d}"


def db_text(v: float) -> str:
    return "-inf" if v <= -59.5 else f"{v:+.1f} dB"


class Player:
    """One file: ffmpeg | pw-cat, targeting the shared output sink. `tag` names the stream so its volume can be set live."""

    safe_default = False  # set from config "cd_safe" by MediaSection

    def __init__(self, path: str, tag: str, node: str = SINK):
        self.path, self.tag, self.node = path, tag, node
        self.ff = self.pc = self.cd = None
        self.safe = Player.safe_default  # CD tracks: True = cdparanoia full error correction (slower than real time on some drives)
        self.base = 0.0
        self.t0 = 0.0
        self.paused = False

    def alive(self) -> bool:
        return self.ff is not None and self.ff.poll() is None

    def start(self, pos=0.0, vol=1.0) -> None:
        self.kill()
        out = ["-vn", "-f", "f32le", "-ac", "2", "-ar", "48000", "pipe:1"]
        cd = cdaudio.parse_path(self.path) if cdaudio.is_cd(self.path) else None
        if cd:  # audio CD: cdparanoia (raw PCM, starts at `pos` seconds into the track) -> ffmpeg -> pw-cat
            self.cd = subprocess.Popen(pwutil.die_with_parent(cdaudio.rip_argv(cd[0], cd[1], pos, fast=not self.safe), "KILL"),
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.ff = subprocess.Popen(
                pwutil.die_with_parent(lowprio(["ffmpeg", "-nostdin", "-v", "error", *cdaudio.FFMPEG_IN, *out]), "KILL"),
                stdin=self.cd.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.cd.stdout.close()
        else:
            self.ff = subprocess.Popen(
                pwutil.die_with_parent(lowprio(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{pos:.2f}", "-i", self.path, *out]), "KILL"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.pc = subprocess.Popen(
            pwutil.die_with_parent(["pw-cat", "-p", "-a", "--rate", "48000", "--channels", "2", "--format", "f32",
             "--latency", "200ms", "--target", self.node, "--media-role", "Music",
             "--volume", f"{max(0.0, min(4.0, vol)):.4f}", "-P", f"{{ application.name={self.tag} node.name={self.tag} media.name={self.tag} }}", "-"], "KILL"),
            stdin=self.ff.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ff.stdout.close()
        self.base, self.t0, self.paused = pos, time.time() + (cdaudio.SPINUP if cd else 0.0), False

    def pos(self) -> float:
        return self.base if self.paused or not self.alive() else max(self.base, self.base + time.time() - self.t0)

    def pause(self) -> None:
        if self.alive() and not self.paused:
            self.base = self.pos()
            for p in (self.cd, self.ff, self.pc):
                if p and p.poll() is None:
                    os.kill(p.pid, signal.SIGSTOP)
            self.paused = True

    def resume(self) -> None:
        if self.alive() and self.paused:
            for p in (self.pc, self.ff, self.cd):
                if p and p.poll() is None:
                    os.kill(p.pid, signal.SIGCONT)
            self.t0, self.paused = time.time(), False

    def kill(self) -> None:
        for p in (self.cd, self.ff, self.pc):
            if p and p.poll() is None:
                try:
                    os.kill(p.pid, signal.SIGCONT)
                except OSError:
                    pass
                p.terminate()
        self.ff = self.pc = self.cd = None
        self.paused = False


from .plat import IS_WIN, lowprio  # noqa: E402

if IS_WIN:  # no pw-cat / SIGSTOP on Windows: ffmpeg feeds the engine directly (see winmedia.py)
    from .winmedia import Player  # noqa: F401,E402


class RoutePopup(QFrame):
    """Precise per-bus routing + level for the whole media player output."""
    changed = pyqtSignal()

    def __init__(self, title: str, cfg: dict, bus_ids: list[str], parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("pane")
        v = QVBoxLayout(self)
        t = QLabel(title)
        t.setObjectName("cap")
        v.addWidget(t)
        for b in bus_ids:
            row = QHBoxLayout()
            cb = QCheckBox(b)
            cb.setChecked(cfg["sends"].get(b, False))
            sl = HSlider(-60.0, 12.0, cfg["send_db"].get(b, 0.0), 0.0)
            sl.setMinimumWidth(170)
            lab = QLabel(f"{cfg['send_db'].get(b, 0.0):+.1f} dB")
            lab.setObjectName("mono")
            lab.setMinimumWidth(62)
            cb.toggled.connect(lambda on, bb=b: self._on(bb, on, cfg))
            sl.changed.connect(lambda x, bb=b, l=lab: self._lvl(bb, x, l, cfg))
            row.addWidget(cb)
            row.addWidget(sl, 1)
            row.addWidget(lab)
            v.addLayout(row)

    def _on(self, b, on, cfg):
        cfg["sends"][b] = on
        self.changed.emit()

    def _lvl(self, b, x, lab, cfg):
        cfg["send_db"][b] = round(x, 1)
        lab.setText(f"{x:+.1f} dB")
        self.changed.emit()


class HMeter(QWidget):
    """Small horizontal stereo peak meter for the player output."""

    def __init__(self):
        super().__init__()
        self.l = self.r = -90.0
        self.setFixedSize(120, 16)
        self.setToolTip("Peak level of the whole media player output")

    def set_levels(self, l, r):
        import math  # noqa: PLC0415
        self.l = 20 * math.log10(l) if l > 1e-6 else -90.0
        self.r = 20 * math.log10(r) if r > 1e-6 else -90.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        t = theme.cur()
        p.fillRect(0, 0, 120, 16, QColor("#050508"))
        for ch, db in enumerate((self.l, self.r)):
            n = 28
            for k in range(n):
                d = -60 + (k + 1) / n * 60
                col = QColor("#e0304f" if d > -2 else t["bright"] if d > -9 else t["hi"] if d > -24 else t["dk"]) if db >= d - 1 else QColor("#1a1626")
                p.fillRect(QRectF(2 + k * 4, 2 + ch * 6, 3, 5), col)
        p.setPen(QColor(t["border"]))
        p.drawRect(0, 0, 119, 15)


class MidiTracksDialog(QDialog):
    """Pick the SoundFont and, per track, on/off and the instrument. Tracks come with the names the sequencer gave them."""

    def __init__(self, mid, mcfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MIDI tracks - " + os.path.basename(mid.path))
        self.mid, self.mcfg = mid, mcfg
        self.setMinimumWidth(640)
        v = QVBoxLayout(self)
        info = QLabel(f"{len(mid.tracks)} tracks, {fmt_t(mid.seconds())}, {mid.bpm():.0f} BPM")
        info.setObjectName("dim")
        v.addWidget(info)
        h = QHBoxLayout()
        h.addWidget(QLabel("SoundFont"))
        self.sf = QComboBox()
        self.sfs = midifile.find_soundfonts()
        cur = mcfg.get("sf", "")
        if cur and cur not in self.sfs:
            self.sfs.append(cur)
        for p in self.sfs:
            self.sf.addItem(os.path.basename(p), p)
        self.sf.addItem("Browse...", "")
        if cur in self.sfs:
            self.sf.setCurrentIndex(self.sfs.index(cur))
        self.sf.activated.connect(self._sf)
        h.addWidget(self.sf, 1)
        v.addLayout(h)
        if not self.sfs:
            n = QLabel("No SoundFont found. Install one:  sudo pacman -S soundfont-fluid   (or Browse... for any .sf2)")
            n.setWordWrap(True)
            n.setObjectName("dim")
            v.addWidget(n)
        body = QWidget()
        self.grid = QVBoxLayout(body)
        self.grid.setSpacing(3)
        self.rows = []
        tr = mcfg.setdefault("tracks", {})
        for t in sorted(mid.tracks, key=lambda x: (x.notes == 0, x.index)):
            if t.notes == 0:
                continue  # tempo / marker only
            s = tr.get(str(t.index), {})
            row = QHBoxLayout()
            on = QCheckBox()
            on.setChecked(s.get("on", True))
            nm = QLabel(f"{t.label}  ({t.notes} notes)")
            nm.setMinimumWidth(280)
            ins = QComboBox()
            if t.drums:
                ins.addItem("Drums (channel 10)", None)
                ins.setEnabled(False)
            else:
                base = t.program.get(next(iter(t.channels), 0), 0)
                ins.addItem(f"From file: {midifile.GM[base]}", None)
                for i, g in enumerate(midifile.GM):
                    ins.addItem(f"{i + 1:3d}  {g}", i)
                if s.get("prog") is not None:
                    ins.setCurrentIndex(1 + s["prog"])
            row.addWidget(on)
            row.addWidget(nm)
            row.addWidget(ins, 1)
            self.grid.addLayout(row)
            self.rows.append((t.index, on, ins))
        self.grid.addStretch(1)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(body)
        sa.setMinimumHeight(260)
        v.addWidget(sa, 1)
        b = QHBoxLayout()
        a1, a0 = QPushButton("ALL ON"), QPushButton("ALL OFF")
        a1.clicked.connect(lambda: [r[1].setChecked(True) for r in self.rows])
        a0.clicked.connect(lambda: [r[1].setChecked(False) for r in self.rows])
        ok, no = QPushButton("OK"), QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        no.clicked.connect(self.reject)
        b.addWidget(a1)
        b.addWidget(a0)
        b.addStretch(1)
        b.addWidget(ok)
        b.addWidget(no)
        v.addLayout(b)

    def _sf(self, i):
        if self.sf.itemData(i) == "":
            p, _ = QFileDialog.getOpenFileName(self, "SoundFont", os.path.expanduser("~"), "SoundFont (*.sf2 *.sf3)")
            if p:
                self.sf.insertItem(i, os.path.basename(p), p)
                self.sf.setCurrentIndex(i)
            else:
                self.sf.setCurrentIndex(0 if self.sfs else -1)

    def apply(self):
        self.mcfg["sf"] = self.sf.currentData() or ""
        tr = self.mcfg.setdefault("tracks", {})
        for idx, on, ins in self.rows:
            tr[str(idx)] = dict(on=on.isChecked(), prog=ins.currentData())


class MediaRow(QFrame):
    _rendered = pyqtSignal(str, str)  # wav path, error text
    removed = pyqtSignal(object)
    selected = pyqtSignal(object)
    changed = pyqtSignal()
    finished = pyqtSignal(object)  # played to its end (queue advances)
    started = pyqtSignal(object)
    moved = pyqtSignal(object, int)  # -1 up, +1 down

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        _seq[0] += 1
        self.tag = f"VM-media-{os.getpid()}-{_seq[0]}"
        self.setObjectName("mrow")
        self.setFixedHeight(30)
        self.setMinimumWidth(840)
        self.done = False
        self.pl = Player(cfg["path"], self.tag)
        self.mid = None
        self._wav, self._wav_key, self._after = "", "", None
        if midifile.is_midi(cfg["path"]):
            try:
                self.mid = midifile.Midi(cfg["path"])
            except (OSError, ValueError, IndexError) as e:
                log("WARN", f"MIDI file unreadable: {e}")
        if self.mid is not None:
            self.mcfg = cfg.setdefault("midi", dict(sf="", tracks={}))
            self.dur = self.mid.seconds()
            self._rendered.connect(self._render_done)
        else:
            self.dur = float(cfg.get("dur", 0.0)) if cdaudio.is_cd(cfg["path"]) else probe_duration(cfg["path"])
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 0, 6, 0)
        h.setSpacing(8)
        self.play = QPushButton("▶")
        self.play.setFixedSize(30, 22)
        self.play.setStyleSheet("font-size: 12px; padding: 0;")
        self.name = ClickLabel(cfg.get("title") or os.path.basename(cfg["path"]))
        self.name.setFixedWidth(260)
        self.name.setToolTip(cfg["path"] if not cdaudio.is_cd(cfg["path"]) else "Audio CD " + cfg["path"][len(cdaudio.PREFIX):].replace("#", " track "))
        self.name.clicked.connect(lambda: self.selected.emit(self))
        nm = QWidget()
        nm.setFixedWidth(260)
        nl = QHBoxLayout(nm)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(5)
        nl.addWidget(self.name, 0)
        if self.mid is not None:
            mb = QPushButton("MIDI ▾")
            mb.setFixedHeight(20)
            mb.setStyleSheet("font-size: 10px; padding: 0 6px;")
            mb.setToolTip("SoundFont and per-track instruments")
            mb.clicked.connect(lambda: self._midi_menu(mb))
            nl.addWidget(mb)
        if cfg["path"].lower().endswith(VIDEO):
            tag = QLabel("VIDEO → AUDIO")
            tag.setObjectName("tag")
            nl.addWidget(tag)
        nl.addStretch(1)
        self.name.setFixedWidth(min(260, self.name.fontMetrics().horizontalAdvance(self.name.text()) + 6))
        self.seek = SeekBar()
        self.seek.dur = self.dur
        self.time = QLabel("0:00 / " + fmt_t(self.dur))
        self.time.setObjectName("mono")
        self.time.setFixedWidth(92)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loop = QPushButton("↻")
        self.loop.setCheckable(True)
        self.loop.setChecked(cfg.get("loop", False))
        self.loop.setFixedSize(30, 22)
        self.loop.setToolTip("Loop")
        self.vol = HSlider(-60.0, 12.0, cfg["gain_db"], 0.0)
        self.vol.setToolTip("Volume of this file (double-click = 0 dB). All files share the one player output.")
        self.vol_l = QLabel(db_text(cfg["gain_db"]))
        self.vol_l.setObjectName("mono")
        self.vol_l.setFixedWidth(64)
        self.vol_l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vw = QWidget()
        vw.setFixedWidth(230)
        vl = QHBoxLayout(vw)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(8)
        vl.addWidget(self.vol, 1)
        vl.addWidget(self.vol_l)
        rm = QPushButton("×")
        rm.setFixedSize(26, 22)
        rm.setToolTip("Remove this file")
        ud = QWidget()
        ud.setFixedWidth(16)
        ul = QVBoxLayout(ud)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(0)
        for txt, d in (("▲", -1), ("▼", 1)):
            b = QPushButton(txt)
            b.setFixedSize(16, 13)
            b.setStyleSheet("font-size: 7px; padding: 0;")
            b.setToolTip("Move up in the queue" if d < 0 else "Move down in the queue")
            b.clicked.connect(lambda _c, dd=d: self.moved.emit(self, dd))
            ul.addWidget(b)
        h.addWidget(ud)
        h.addWidget(self.play)
        h.addWidget(nm)
        h.addWidget(self.seek, 1)
        h.addWidget(self.time)
        h.addWidget(self.loop)
        h.addWidget(vw)
        h.addWidget(rm)
        self.play.clicked.connect(self._toggle)
        self.seek.seeked.connect(self._seek)
        self.seek.dragged.connect(lambda f: self.time.setText(f"{fmt_t(f * self.dur)} / {fmt_t(self.dur)}"))
        self.loop.toggled.connect(lambda v: cfg.__setitem__("loop", v))
        self.vol.changed.connect(self._vol)
        rm.clicked.connect(lambda: self.removed.emit(self))
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self.apply_volume)

    def set_selected(self, on: bool) -> None:
        self.setProperty("sel", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    # ---- volume (live, per stream) --------------------------------------
    def lin(self) -> float:
        return 10 ** (self.cfg["gain_db"] / 20.0) if self.cfg["gain_db"] > -59.5 else 0.0

    def apply_volume(self):
        if self.pl.alive():
            pwutil.set_stream_volume_async(self.tag, self.cfg["gain_db"])

    def _vol(self, v):
        self.cfg["gain_db"] = round(v, 1)
        self.vol_l.setText(db_text(self.cfg["gain_db"]))
        self._pending.start(60)  # coalesce slider drags into few pactl calls

    # ---- transport ------------------------------------------------------
    def playing(self) -> bool:
        return self.pl.alive() and not self.pl.paused

    # ---- MIDI: render with fluidsynth, then play the WAV like any file ----------------
    def _sel(self) -> dict:
        return {int(k): v for k, v in self.mcfg.get("tracks", {}).items()}

    def _sf_path(self) -> str:
        sf = self.mcfg.get("sf", "")
        if sf and os.path.isfile(sf):
            return sf
        found = midifile.find_soundfonts()
        pref = [p for p in found if "GM" in os.path.basename(p) or "default" in os.path.basename(p).lower()]
        return (pref or found or [""])[0]

    def _midi_menu(self, btn):
        m = QMenu(self)
        m.addAction("Tracks / instruments...").triggered.connect(self.edit_tracks)
        sub = m.addMenu("SoundFont")
        cur = self._sf_path()
        for p in midifile.find_soundfonts():
            a = sub.addAction(os.path.basename(p))
            a.setCheckable(True)
            a.setChecked(p == cur)
            a.triggered.connect(lambda _c, pp=p: self._set_sf(pp))
        sub.addAction("Browse...").triggered.connect(self.edit_tracks)
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _set_sf(self, p):
        self.mcfg["sf"] = p
        self._midi_changed()

    def edit_tracks(self):
        d = MidiTracksDialog(self.mid, self.mcfg, self)
        if d.exec():
            d.apply()
            self._midi_changed()

    def _midi_changed(self):
        was = self.pl.alive()
        pos = self.pl.pos() if was else 0.0
        self._wav = self._wav_key = ""
        if was:
            self.stop()
        self.changed.emit()
        if was:
            self._start(pos)

    def _midi_ready(self) -> bool:
        if not (self._wav and os.path.isfile(self._wav)):
            return False
        return self._wav_key == midifile.render_key(self.cfg["path"], self._sf_path(), self._sel())

    def _render_then(self, after):
        if self._after is not None:
            return
        sf = self._sf_path()
        if not sf:
            QMessageBox.information(self, "MIDI", "No SoundFont found.\nInstall one with:  sudo pacman -S soundfont-fluid\n"
                                    "or pick any .sf2 file in MIDI > Tracks / instruments.")
            return
        self._after = after
        self.play.setText("…")
        self.time.setText("rendering")
        sel, path = self._sel(), self.cfg["path"]
        key = midifile.render_key(path, sf, sel)

        def work():
            try:
                self._rendered.emit(midifile.render(path, sf, sel), key)
            except Exception as e:  # noqa: BLE001
                self._rendered.emit("", str(e))
        threading.Thread(target=work, daemon=True).start()

    def _render_done(self, wav, key_or_err):
        after, self._after = self._after, None
        self.play.setText("▶")
        if not wav:
            self.time.setText("0:00 / " + fmt_t(self.dur))
            QMessageBox.warning(self, "MIDI", key_or_err)
            return
        self._wav, self._wav_key = wav, key_or_err
        self.pl.path = wav
        d = probe_duration(wav)
        if d:
            self.dur = d
            self.seek.dur = d
        self.time.setText("0:00 / " + fmt_t(self.dur))
        if after:
            after()

    def _start(self, pos):
        if self.mid is not None and not self._midi_ready():
            self._render_then(lambda: self._start(pos))
            return
        self.pl.start(pos, self.lin())
        self.play.setText("❚❚")
        self.done = False
        self.started.emit(self)
        QTimer.singleShot(700, self.apply_volume)  # the stream exists by now: exact value (also > 0 dB)

    def _toggle(self):
        if self._after is not None:
            return  # still rendering
        if not self.pl.alive():
            self._start(0.0 if self.seek.f * self.dur >= self.dur - 0.5 else self.seek.f * self.dur)
        elif self.pl.paused:
            self.pl.resume()
            self.play.setText("❚❚")
        else:
            self.pl.pause()
            self.play.setText("▶")

    def pause_or_play(self, on: bool):
        if on and not self.playing():
            self._toggle()
        elif not on and self.playing():
            self._toggle()

    def set_current(self, on: bool):
        self.setProperty("cur", bool(on))
        self.style().unpolish(self)
        self.style().polish(self)

    def play_from_start(self):
        self.pl.kill()
        self.done = False
        self._start(0.0)

    def finish(self, emit: bool = True):
        """Played to its end: the bar stays full so you can see it has been played."""
        self.pl.kill()
        self.play.setText("▶")
        self.done = True
        self.seek.set_fraction(1.0)
        self.time.setText(f"{fmt_t(self.dur)} / {fmt_t(self.dur)}")
        if emit:
            self.finished.emit(self)

    def stop(self):
        self.pl.kill()
        self.done = False
        self.play.setText("▶")
        self.seek.set_fraction(0.0)
        self.time.setText("0:00 / " + fmt_t(self.dur))

    def seek_to(self, secs: float):
        secs = max(0.0, min(self.dur, secs))
        if self.pl.alive():
            was_paused = self.pl.paused
            self._start(secs)
            if was_paused:
                self.pl.pause()
                self.play.setText("▶")
        else:
            self.seek.set_fraction(secs / self.dur if self.dur else 0.0)
            self.time.setText(f"{fmt_t(secs)} / {fmt_t(self.dur)}")

    def _seek(self, f):
        self.done = False
        self.seek_to(f * self.dur)

    def tick(self):
        if self.pl.ff is not None and not self.pl.alive():
            if self.loop.isChecked():
                self._start(0.0)
            else:
                self.finish()
                return
        if self.pl.alive() and not self.seek.drag:
            p = min(self.pl.pos(), self.dur)
            self.seek.set_fraction(p / self.dur if self.dur else 0.0)
            self.time.setText(f"{fmt_t(p)} / {fmt_t(self.dur)}")

    def shutdown(self):
        self.pl.kill()


class DspWindow(QWidget):
    """The media player DSP as its own movable window (opened with the DSP button)."""

    def __init__(self, sec):
        super().__init__(sec.window(), Qt.WindowType.Tool | Qt.WindowType.WindowCloseButtonHint)
        self.sec = sec
        self.setWindowTitle("Media Player DSP")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(4, 4, 4, 4)
        self.lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self.setObjectName("dspwin")

    def set_panel(self, w):
        if self.lay.indexOf(w) < 0:
            self.lay.addWidget(w)

    def _geo(self):
        g = self.geometry()
        self.sec.mp["dsp_geo"] = [g.x(), g.y()]

    def moveEvent(self, e):
        super().moveEvent(e)
        if self.isVisible():
            self._geo()

    def closeEvent(self, e):
        self.sec.dsp_btn.setChecked(False)
        super().closeEvent(e)


class MediaSection(QFrame):
    changed = pyqtSignal()
    message = pyqtSignal(str)

    def __init__(self, cfg, eng):
        super().__init__()
        self.cfg, self.eng = cfg, eng
        Player.safe_default = bool(cfg.d.get("cd_safe", False))
        self.mp = cfg.d["mp"]
        self.setObjectName("mediasec")
        self.setMinimumWidth(240)  # explicit: the header reflows (output cluster moves to its own row) instead of forcing a wide window
        self.setAcceptDrops(True)
        self.rows: list[MediaRow] = []
        self.sel: MediaRow | None = None
        self.cur: MediaRow | None = None  # row the queue is on
        self.mp.setdefault("mix", False)
        self.mp.setdefault("rep_all", False)
        self.mp.setdefault("dsp_show", False)
        self.mp.setdefault("dsp_float", True)
        v = QVBoxLayout(self)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title = ClickLabel("MEDIA PLAYER")
        self.title.setObjectName("cap")
        self.title.setToolTip("Double-click to rename")
        self.title.doubleClicked.connect(self._rename)
        self.title.setFixedHeight(22)
        bar.addWidget(self.title)
        self.add = QPushButton("+ FILES ▾")
        self.add.setFixedHeight(24)
        self.add.setToolTip("Add audio or video files")
        menu = QMenu(self)
        menu.addAction("Choose files...  (any audio / video)").triggered.connect(self.add_dialog)
        menu.addAction("Add folder...").triggered.connect(self.add_folder)
        menu.addSeparator()
        self.cd_add = menu.addAction("Add audio CD tracks")
        self.cd_add.triggered.connect(self.add_cd)
        self.cd_eject = menu.addAction("Eject CD")
        self.cd_eject.triggered.connect(self.eject_cd)
        menu.aboutToShow.connect(self._cd_menu)
        self.add.setMenu(menu)
        bar.addWidget(self.add)
        for txt, tip, fn, w in (("-", "Remove the selected file (or the last one)", self.remove_sel, 34),
                                ("CLEAR", "Remove all files", self.clear, 0),
                                ):
            b = QPushButton(txt)
            b.setFixedHeight(24)
            b.setToolTip(tip)
            if w:
                b.setFixedWidth(w)
                b.setStyleSheet("font-size: 14px; padding: 0;")
            else:
                b.setStyleSheet("padding: 0 10px;")
            b.clicked.connect(fn)
            bar.addWidget(b)
        # tape deck: << >> side by side, then stop and play
        def deck(items, gap):
            lo = QHBoxLayout()
            lo.setSpacing(gap)
            lo.setContentsMargins(0, 0, 0, 0)
            for txt, tip, fn in items:
                b = QPushButton(txt)
                b.setFixedSize(32, 24)
                b.setToolTip(tip)
                b.setStyleSheet("font-size: 14px; padding: 0;")
                b.clicked.connect(fn)
                lo.addWidget(b)
            bar.addLayout(lo)
        deck((("⏮", "Previous file (restarts this one if it is more than 3 s in)", self.prev),
              ("⏭", "Next file", self.next)), 0)
        deck((("■", "Stop (all files back to the start)", self.stop_all),
              ("▶", "Play / pause the queue: top to bottom, each file starts when the one before ends", self.play_all)), 2)
        self.mix_btn = QPushButton("MIX")
        self.mix_btn.setCheckable(True)
        self.mix_btn.setChecked(self.mp["mix"])
        self.mix_btn.setFixedHeight(24)
        self.mix_btn.setToolTip("MIX: play / pause starts every file at once instead of one after another")
        self.mix_btn.toggled.connect(lambda v: self.mp.__setitem__("mix", bool(v)))
        self.rep_btn = QPushButton("↻ ALL")
        self.rep_btn.setCheckable(True)
        self.rep_btn.setChecked(self.mp["rep_all"])
        self.rep_btn.setFixedHeight(24)
        self.rep_btn.setToolTip("Repeat the whole queue: after the last file start again from the top")
        self.rep_btn.toggled.connect(lambda v: self.mp.__setitem__("rep_all", bool(v)))
        bar.addWidget(self.mix_btn)
        bar.addWidget(self.rep_btn)
        self.dsp_btn = QPushButton("DSP")
        self.dsp_btn.setCheckable(True)
        self.dsp_btn.setChecked(self.mp.get("dsp_show", False))
        self.dsp_btn.setFixedHeight(24)
        self.dsp_btn.setToolTip("Show / hide the DSP panel (EQ, 3D, reverb, ... for everything the player plays)")
        self.dsp_btn.toggled.connect(self._dsp_show)
        bar.addWidget(self.dsp_btn)
        bar.addStretch(1)
        # ---- the ONE output of the whole player (sits in the header when wide, on its own row when narrow) ----
        self.cluster = QWidget()
        cl = QHBoxLayout(self.cluster)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        o = QLabel("OUTPUT")
        o.setObjectName("iplab")
        cl.addWidget(o)
        self.route = QPushButton()
        self.route.setFixedHeight(24)
        self.route.setMinimumWidth(110)
        self.route.clicked.connect(self._popup)
        cl.addWidget(self.route)
        self.vol = HSlider(-60.0, 12.0, self.mp["gain_db"], 0.0)
        self.vol.setMinimumWidth(100)
        self.vol.setMaximumWidth(220)
        self.vol.setToolTip("Master volume of the whole media player (double-click = 0 dB)")
        self.vol.changed.connect(self._master)
        self.vol_l = QLabel(db_text(self.mp["gain_db"]))
        self.vol_l.setObjectName("mono")
        self.vol_l.setFixedWidth(64)
        self.vol_l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.meter = HMeter()
        self.mute = QPushButton("MUTE")
        self.mute.setObjectName("mute")
        self.mute.setCheckable(True)
        self.mute.setChecked(self.mp["mute"])
        self.mute.setFixedHeight(24)
        self.mute.toggled.connect(self._mute)
        for w in (self.vol, self.vol_l, self.meter, self.mute):
            cl.addWidget(w)
        self.bar = bar
        v.addLayout(bar)
        self.row2 = QWidget()
        r2 = QHBoxLayout(self.row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.addStretch(1)
        self.row2_l = r2
        self.row2.hide()
        v.addWidget(self.row2)
        bar.addWidget(self.cluster)
        self._wide = True
        self.area = QScrollArea()
        self.area.setObjectName("mlist")
        self.area.setFrameShape(QFrame.Shape.StyledPanel)
        self.area.setStyleSheet("QScrollArea#mlist { background: %s; border: 1px solid %s; }" % (theme.cur()["bg"], theme.cur()["border"]))
        self.area.setWidgetResizable(True)
        self.body = QWidget()
        self.col = QVBoxLayout(self.body)
        self.col.setContentsMargins(0, 0, 0, 0)
        self.col.setSpacing(0)
        self.empty = QLabel("No files loaded. Click [ + FILES ▾ ] or drop audio / video files here.")
        self.empty.setObjectName("mempty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.col.addWidget(self.empty)
        self.col.addStretch(1)
        self.area.setWidget(self.body)
        mid = QHBoxLayout()
        mid.setSpacing(4)
        mid.addWidget(self.area, 1)
        self.dsp_host = QScrollArea()
        self.dsp_host.setFrameShape(QFrame.Shape.NoFrame)
        self.dsp_host.setWidgetResizable(True)
        self.dsp_host.setFixedWidth(392)
        self.dsp_host.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        mid.addWidget(self.dsp_host)
        v.addLayout(mid, 1)
        self.dsp = None
        self.dsp_win = None
        self._build_dsp()
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: [r.tick() for r in self.rows])
        self.timer.start(200)
        self.bus_ids_fn = lambda: [b["id"] for b in cfg.d["buses"]]
        for m in cfg.d.get("media", []):
            if os.path.exists(m["path"]):  # CD tracks are not restored: the disc may not be in the drive
                self._add_row(m)
        cfg.d["media"] = [r.cfg for r in self.rows]
        self.refresh_route()
        self.push()

    def _build_dsp(self):
        old = self.dsp
        if old is not None:
            if self.dsp_host.widget() is old:
                self.dsp_host.takeWidget()
            old.setParent(None)
            old.deleteLater()
        self.dsp = mediadsp.MediaDsp(mediadsp.ensure(self.mp), self.eng)
        self.dsp.dock_toggled.connect(self._dock_toggle)
        self._place_dsp()

    def _place_dsp(self):
        show, floating = bool(self.mp.get("dsp_show", False)), bool(self.mp.get("dsp_float", True))
        self.dsp.set_floating(floating)
        if self.dsp_host.widget() is self.dsp:
            self.dsp_host.takeWidget()
        if floating:
            if self.dsp_win is None:
                self.dsp_win = DspWindow(self)
            self.dsp_host.hide()
            self.dsp_win.set_panel(self.dsp)
            self.dsp.show()
            if show and not self.dsp_win.isVisible():
                g = self.mp.get("dsp_geo")
                if g:
                    self.dsp_win.move(int(g[0]), int(g[1]))
                else:
                    p = self.mapToGlobal(self.rect().topRight())
                    self.dsp_win.move(p.x() - 420, p.y() + 40)
                self.dsp_win.show()
            elif show:
                self.dsp_win.raise_()
            elif self.dsp_win.isVisible():
                self.dsp_win.hide()
        else:
            if self.dsp_win is not None:
                self.dsp_win.hide()
            self.dsp_host.setWidget(self.dsp)
            self.dsp_host.setVisible(show)

    def _dsp_show(self, on):
        self.mp["dsp_show"] = bool(on)
        self._place_dsp()

    def _dock_toggle(self):
        self.mp["dsp_float"] = not self.mp.get("dsp_float", True)
        self.mp["dsp_show"] = True
        self.dsp_btn.blockSignals(True)
        self.dsp_btn.setChecked(True)
        self.dsp_btn.blockSignals(False)
        self._place_dsp()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        wide = self.width() >= 1000
        if wide != self._wide:
            self._wide = wide
            if wide:
                self.row2.hide()
                self.bar.addWidget(self.cluster)
            else:
                self.row2_l.addWidget(self.cluster)
                self.row2.show()

    # ---- the one output ---------------------------------------------------
    def push(self):
        c = self.mp
        ids = self.bus_ids_fn()
        self.eng.send(c="strip", i=MEDIA_SLOT, active=True, gain_db=c["gain_db"], pan=0.0, mute=c["mute"],
                      sends=[bool(c["sends"].get(b, False)) and b in ids for b in BUS_IDS],
                      send_db=[c["send_db"].get(b, 0.0) for b in BUS_IDS])
        if self.dsp is not None:
            self.dsp.push()

    def _master(self, v):
        self.mp["gain_db"] = round(v, 1)
        self.vol_l.setText(db_text(self.mp["gain_db"]))
        self.eng.send(c="strip", i=MEDIA_SLOT, gain_db=self.mp["gain_db"])

    def _mute(self, on):
        self.mp["mute"] = on
        self.eng.send(c="strip", i=MEDIA_SLOT, mute=on)

    def refresh_route(self):
        ids = self.bus_ids_fn()
        on = [b for b in ids if self.mp["sends"].get(b, False)]
        self.route.setText("→ " + (" ".join(on[:3]) + (" …" if len(on) > 3 else "") if on else "(none)"))
        tip = "\n".join(f"{b}: {self.mp['send_db'].get(b, 0.0):+.1f} dB" for b in on) or "Not routed anywhere"
        self.route.setToolTip(f"'{self.mp['label']}' plays to:\n{tip}\n(click for exact outputs + levels)")

    def reload_mp(self):
        """Show the master state from config again (scene recall / macros changed it)."""
        self.mute.blockSignals(True)
        self.mute.setChecked(self.mp["mute"])
        self.mute.blockSignals(False)
        self.vol.set_value(self.mp["gain_db"], False)
        self.vol_l.setText(db_text(self.mp["gain_db"]))
        self._build_dsp()
        self.dsp_btn.blockSignals(True)
        self.dsp_btn.setChecked(self.mp.get("dsp_show", False))
        self.dsp_btn.blockSignals(False)
        self.refresh_route()
        self.push()

    def refresh_routes(self):
        self.refresh_route()
        self.push()

    def _popup(self):
        pop = RoutePopup(f"ROUTING  {self.mp['label']}  (all files)", self.mp, self.bus_ids_fn(), self)
        pop.changed.connect(lambda: (self.push(), self.refresh_route()))
        pop.move(self.route.mapToGlobal(self.route.rect().bottomLeft()))
        pop.show()

    def update_meter(self, lr):
        self.meter.set_levels(lr[0], lr[1])

    def _rename(self):
        from PyQt6.QtWidgets import QInputDialog  # noqa: PLC0415
        t, ok = QInputDialog.getText(self, "Rename", "Name of the media player:", text=self.title.text())
        if ok and t.strip():
            self.title.setText(t.strip().upper()[:28])
            self.cfg.d.setdefault("names", {})["media"] = self.title.text()

    def set_title(self, t: str):
        self.title.setText(t)

    # ---- files --------------------------------------------------------------
    def add_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add media files", os.path.expanduser("~"), FILTER)
        for f in files:
            self.add_file(f)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder", os.path.expanduser("~"))
        if d:
            for f in sorted(os.listdir(d)):
                if f.lower().endswith((".mp3", ".flac", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".wma") + VIDEO + midifile.MIDI_EXT):
                    self.add_file(os.path.join(d, f))

    # ---- audio CD -----------------------------------------------------------
    def _cd_device(self) -> str:
        return self.cfg.d.get("cd_device") or cdaudio.DEFAULT_DEVICE

    def _cd_menu(self):
        ok = cdaudio.available()
        self.cd_add.setEnabled(ok)
        self.cd_add.setText("Add audio CD tracks" if ok else ("Add audio CD tracks (not available on Windows)" if IS_WIN else "Add audio CD tracks (install cdparanoia)"))
        self.cd_eject.setEnabled(ok or shutil.which("eject") is not None)

    def add_cd(self):
        dev = self._cd_device()
        kind = cdaudio.disc_kind(dev)
        if kind == "none":
            QMessageBox.information(self, "Audio CD", f"No disc found in {dev}.\nInsert an audio CD and try again.")
            return
        if kind == "data":
            mp = cdaudio.mount_data(dev)
            if not mp:
                QMessageBox.information(self, "Data disc", f"{dev} holds a data disc and could not be mounted.")
                return
            files, _ = QFileDialog.getOpenFileNames(self, "Files on the disc", mp, FILTER)
            for f in files:
                self.add_file(f)
            return
        tracks = cdaudio.toc(dev)
        for tr in tracks:
            m = dict(path=cdaudio.make_path(dev, tr["track"]), loop=False, gain_db=0.0, dur=tr["secs"],
                     title=f"CD track {tr['track']:02d}")
            self._add_row(m)
        self.cfg.d["media"] = [r.cfg for r in self.rows]
        self.changed.emit()
        self._fetch_cd_titles(dev)

    def _fetch_cd_titles(self, dev: str):
        """Look the names up in the background (MusicBrainz). Failure just leaves 'CD track NN'."""
        box: dict = {}
        threading.Thread(target=lambda: box.setdefault("t", cdaudio.musicbrainz_titles(dev)), daemon=True).start()
        tries = [0]

        def poll():
            tries[0] += 1
            if "t" not in box:
                if tries[0] < 40:
                    QTimer.singleShot(500, poll)
                return
            for r in self.rows:
                p = cdaudio.parse_path(r.cfg["path"])
                if p and p[0] == dev and p[1] in box["t"]:
                    r.cfg["title"] = f"{p[1]:02d} {box['t'][p[1]]}"
                    r.name.setText(r.cfg["title"])
        QTimer.singleShot(500, poll)

    def eject_cd(self):
        dev = self._cd_device()
        for r in [r for r in self.rows if (cdaudio.parse_path(r.cfg["path"]) or ("",))[0] == dev]:
            self._remove(r)  # stop and drop this disc's tracks first
        if not cdaudio.eject(dev):
            self.message.emit(f"Could not eject {dev}")

    def add_file(self, path: str):
        m = dict(path=path, loop=False, gain_db=0.0)
        self._add_row(m)
        self.cfg.d["media"] = [r.cfg for r in self.rows]
        self.changed.emit()

    def _add_row(self, m: dict):
        r = MediaRow(m)
        r.removed.connect(self._remove)
        r.selected.connect(self._select)
        r.started.connect(self._row_started)
        r.finished.connect(self._row_finished)
        r.moved.connect(self._move_row)
        self.col.insertWidget(self.col.count() - 1, r)
        self.rows.append(r)
        self.empty.setVisible(False)

    def _select(self, r):
        self.sel = None if self.sel is r else r
        for x in self.rows:
            x.set_selected(x is self.sel)

    def _remove(self, r: MediaRow):
        r.shutdown()
        self.rows.remove(r)
        if self.cur is r:
            self.cur = None
        if self.sel is r:
            self.sel = None
        r.setParent(None)
        r.deleteLater()
        self.cfg.d["media"] = [x.cfg for x in self.rows]
        self.empty.setVisible(not self.rows)
        self.changed.emit()

    def remove_sel(self):
        if self.rows:
            self._remove(self.sel if self.sel in self.rows else self.rows[-1])

    def clear(self):
        for r in list(self.rows):
            self._remove(r)

    # ---- queue: top to bottom, each file starts when the one before ends --------------------
    def _mark(self, r):
        self.cur = r
        for x in self.rows:
            x.set_current(x is r)

    def _row_started(self, r):
        self._mark(r)
        if not self.mp.get("mix"):
            for x in self.rows:
                if x is not r and x.pl.alive():
                    x.stop()

    def _row_finished(self, r):
        if not self.mp.get("mix") and r is self.cur:
            self._advance()

    def _advance(self):
        i = self.rows.index(self.cur) + 1 if self.cur in self.rows else 0
        if i < len(self.rows):
            self.rows[i].play_from_start()
        elif self.mp.get("rep_all") and self.rows:
            for x in self.rows:
                x.stop()
            self.rows[0].play_from_start()
        else:
            self._mark(None)

    def _move_row(self, r, d):
        i = self.rows.index(r)
        j = i + d
        if not 0 <= j < len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        self.col.removeWidget(r)
        self.col.insertWidget(j + 1, r)  # index 0 of the column is the "no files" label
        self.cfg.d["media"] = [x.cfg for x in self.rows]
        self.changed.emit()

    def play_all(self):
        if self.mp.get("mix"):
            any_on = any(r.playing() for r in self.rows)
            for r in self.rows:
                r.pause_or_play(not any_on)
            return
        playing = next((r for r in self.rows if r.playing()), None)
        if playing is not None:
            playing.pause_or_play(False)
            return
        paused = next((r for r in self.rows if r.pl.alive() and r.pl.paused), None)
        if paused is not None:
            paused.pause_or_play(True)
            return
        todo = next((r for r in self.rows if not r.done), None)
        if todo is None and self.rows:  # everything has been played: start over from the top
            for x in self.rows:
                x.stop()
            todo = self.rows[0]
        if todo is not None:
            todo.play_from_start()

    def stop_all(self):
        for r in self.rows:
            r.stop()
        self._mark(None)

    def prev(self):
        if self.mp.get("mix"):
            for r in self.rows:
                r.seek_to(0.0)
            return
        cur = self.cur if self.cur in self.rows else None
        if cur is None:
            return
        i = self.rows.index(cur)
        if i == 0 or (cur.pl.alive() and cur.pl.pos() > 3.0):
            cur.play_from_start()
        else:
            self.rows[i - 1].play_from_start()

    def next(self):
        if self.mp.get("mix"):
            for r in self.rows:
                if r.pl.alive():
                    r.finish(False)
            return
        if self.cur not in self.rows:
            self.play_all()
            return
        self.cur.finish(False)
        self._advance()

    def now_playing(self) -> list[str]:
        return [os.path.basename(r.cfg["path"]) for r in self.rows if r.playing()]

    # ---- drag and drop ------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setProperty("drag", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, e):
        self.setProperty("drag", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.add_file(u.toLocalFile())

    def shutdown(self):
        for r in self.rows:
            r.shutdown()
        self.eng.send(c="strip", i=MEDIA_SLOT, active=False)
