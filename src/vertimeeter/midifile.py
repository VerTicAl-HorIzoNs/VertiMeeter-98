"""MIDI files for the media player: read the tracks, rewrite the file with chosen tracks / instruments, render with fluidsynth.

No extra Python packages. A .mid holds notes, not sound, so playing one means rendering it to a WAV with a SoundFont
(fluidsynth) and handing that WAV to the normal player."""
from __future__ import annotations

import glob
import hashlib
import os
import shutil
import struct
import subprocess

SF_DIRS = ["/usr/share/soundfonts", "/usr/share/sounds/sf2", "/usr/share/sounds/sf3", "~/.local/share/soundfonts",
           "~/.local/share/sounds/sf2", "~/soundfonts"]
GM = ["Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano", "Electric Piano 1",
      "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
      "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
      "Reed Organ", "Accordion", "Harmonica", "Tango Accordion", "Nylon Guitar", "Steel Guitar", "Jazz Guitar",
      "Clean Guitar", "Muted Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
      "Finger Bass", "Pick Bass", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
      "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
      "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs",
      "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section",
      "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn",
      "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle",
      "Ocarina", "Square Lead", "Saw Lead", "Calliope Lead", "Chiff Lead", "Charang Lead", "Voice Lead", "Fifths Lead",
      "Bass + Lead", "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad", "Bowed Pad", "Metallic Pad", "Halo Pad",
      "Sweep Pad", "Rain FX", "Soundtrack FX", "Crystal FX", "Atmosphere FX", "Brightness FX", "Goblins FX",
      "Echoes FX", "Sci-Fi FX", "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
      "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
      "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot"]
MIDI_EXT = (".mid", ".midi", ".kar")


def is_midi(path: str) -> bool:
    return path.lower().endswith(MIDI_EXT)


def find_soundfonts() -> list[str]:
    out = []
    for d in SF_DIRS:
        d = os.path.expanduser(d)
        for ext in ("sf2", "sf3", "SF2"):
            out += sorted(glob.glob(os.path.join(d, f"*.{ext}")))
    seen, res = set(), []
    for p in out:
        r = os.path.realpath(p)
        if r not in seen:
            seen.add(r)
            res.append(p)
    return res


from .plat import cache_dir as _cache_dir  # noqa: E402


def fluidsynth_ok() -> bool:
    return shutil.which("fluidsynth") is not None


def _vl(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def _wvl(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    return bytes(reversed(out))


class Track:
    def __init__(self, index: int):
        self.index = index
        self.name = ""
        self.events: list[tuple[int, bytes, str]] = []  # (abs tick, raw bytes incl. status, kind: ch / meta / sys)
        self.notes = 0
        self.channels: dict[int, int] = {}  # channel (0-15) -> note count
        self.program: dict[int, int] = {}  # channel -> first program
        self.tempos: list[tuple[int, int]] = []

    @property
    def drums(self) -> bool:
        return 9 in self.channels

    @property
    def label(self) -> str:
        return self.name or f"Track {self.index + 1}"


class Midi:
    def __init__(self, path: str):
        self.path = path
        d = open(path, "rb").read()
        if d[:4] != b"MThd":
            raise ValueError("not a MIDI file")
        self.fmt, n, self.div = struct.unpack(">HHH", d[8:14])
        p = 8 + struct.unpack(">I", d[4:8])[0]
        self.tracks: list[Track] = []
        for t in range(n):
            if d[p:p + 4] != b"MTrk":
                break
            ln = struct.unpack(">I", d[p + 4:p + 8])[0]
            self.tracks.append(self._track(t, d[p + 8:p + 8 + ln]))
            p += 8 + ln
        self.tempo_map = sorted(x for t in self.tracks for x in t.tempos) or [(0, 500000)]
        self.end_tick = max((e[0] for t in self.tracks for e in t.events), default=0)

    def _track(self, idx: int, b: bytes) -> Track:
        tr = Track(idx)
        i, tick, st = 0, 0, 0
        while i < len(b):
            dt, i = _vl(b, i)
            tick += dt
            s = b[i]
            if s == 0xFF:
                ty = b[i + 1]
                ln, j = _vl(b, i + 2)
                data = b[j:j + ln]
                raw = b[i:j + ln]
                i = j + ln
                if ty == 3 and not tr.name:
                    tr.name = data.decode("latin1", "replace").strip()
                if ty == 0x51 and len(data) == 3:
                    tr.tempos.append((tick, int.from_bytes(data, "big")))
                tr.events.append((tick, raw, "meta"))
                continue
            if s in (0xF0, 0xF7):
                ln, j = _vl(b, i + 1)
                tr.events.append((tick, b[i:j + ln], "sys"))
                i = j + ln
                continue
            if s & 0x80:
                st = s
                i += 1
            h, c = st & 0xF0, st & 0x0F
            n = 1 if h in (0xC0, 0xD0) else 2
            data = b[i:i + n]
            i += n
            if h == 0x90 and data[1] > 0:
                tr.notes += 1
                tr.channels[c] = tr.channels.get(c, 0) + 1
            elif h == 0xC0:
                tr.program.setdefault(c, data[0])
            tr.events.append((tick, bytes([st]) + bytes(data), "ch"))
        return tr

    def seconds(self) -> float:
        """Length in seconds from the tempo map."""
        sec, last_t, tempo = 0.0, 0, 500000
        for t, tp in self.tempo_map:
            sec += (min(t, self.end_tick) - last_t) * tempo / 1e6 / self.div
            last_t, tempo = min(t, self.end_tick), tp
        return sec + (self.end_tick - last_t) * tempo / 1e6 / self.div

    def bpm(self) -> float:
        return 60000000 / self.tempo_map[0][1]

    def write(self, out: str, sel: dict[int, dict]) -> None:
        """New file. sel[track index] = {"on": bool, "prog": int or None}. An off track keeps only its tempo / meta events."""
        chunks = []
        for tr in self.tracks:
            s = sel.get(tr.index, {})
            on = s.get("on", True)
            prog = s.get("prog")
            ev = []
            for tick, raw, kind in tr.events:
                if kind == "ch":
                    if not on:
                        continue
                    if prog is not None and (raw[0] & 0xF0) == 0xC0 and (raw[0] & 15) != 9:
                        continue
                    ev.append((tick, raw))
                elif raw[:2] == b"\xff\x2f":
                    continue
                else:
                    ev.append((tick, raw))
            if on and prog is not None:
                for ch in tr.channels:
                    if ch != 9:
                        ev.insert(0, (0, bytes([0xC0 | ch, prog])))
            ev.sort(key=lambda e: e[0])  # stable: keeps the program change in front of the notes at tick 0
            body, last = b"", 0
            for tick, raw in ev:
                body += _wvl(tick - last) + raw
                last = tick
            body += _wvl(0) + b"\xff\x2f\x00"
            chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
        with open(out, "wb") as f:
            f.write(b"MThd" + struct.pack(">IHHH", 6, self.fmt, len(chunks), self.div) + b"".join(chunks))


def cache_dir() -> str:
    d = os.path.join(_cache_dir(), "midi")
    os.makedirs(d, exist_ok=True)
    return d


def render_key(path: str, sf: str, sel: dict) -> str:
    st = os.stat(path)
    spec = repr((path, st.st_mtime_ns, st.st_size, sf, sorted((k, v.get("on", True), v.get("prog")) for k, v in sel.items())))
    return hashlib.sha1(spec.encode()).hexdigest()[:16]


def render(path: str, sf: str, sel: dict[int, dict], gain: float = 0.9) -> str:
    """Render to a cached 48 kHz WAV and return its path. Raises RuntimeError with a readable message."""
    if not fluidsynth_ok():
        raise RuntimeError("fluidsynth is not installed (Arch: sudo pacman -S fluidsynth; Windows: install FluidSynth and put fluidsynth.exe on the PATH or in bin\\)")
    if not sf or not os.path.isfile(sf):
        raise RuntimeError("no SoundFont chosen (sudo pacman -S soundfont-fluid, or pick an .sf2 file)")
    out = os.path.join(cache_dir(), render_key(path, sf, sel) + ".wav")
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        return out
    m = Midi(path)
    tmp_mid = out[:-4] + ".mid"
    m.write(tmp_mid, sel)
    r = subprocess.run([*(["nice", "-n", "10"] if os.name != "nt" else []), "fluidsynth", "-ni", "-g", str(gain), "-r", "48000", "-T", "wav", "-F", out + ".part",
                        sf, tmp_mid], capture_output=True, text=True, timeout=600)
    try:
        os.remove(tmp_mid)
    except OSError:
        pass
    if r.returncode != 0 or not os.path.isfile(out + ".part"):
        raise RuntimeError("fluidsynth failed: " + (r.stderr or r.stdout).strip().splitlines()[-1][:120] if (r.stderr or r.stdout).strip()
                           else "fluidsynth failed")
    os.replace(out + ".part", out)
    return out
