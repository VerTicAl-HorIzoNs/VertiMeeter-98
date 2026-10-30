"""Audio CD support for the media player (0.11.1).

A CD track is a media row whose path is  cdda://<device>#<track> . The Player plays it as
cdparanoia | ffmpeg | pw-cat, so it goes through the one media sink (fader, DSP, routes) like any file.
Needs:  pacman -S cdparanoia   (optional: libdiscid + `pip install discid` for MusicBrainz track names).
The TOC reader and the MusicBrainz lookup follow Audio Death Toolkit 2.0 (cd_ripper.py / cd_player.py).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request

PREFIX = "cdda://"
DEFAULT_DEVICE = "/dev/sr0"
_TOC = re.compile(r"^\s*(\d+)\.\s+(\d+)\s+\[(\d+):(\d+\.\d+)\]")


def available() -> bool:
    return shutil.which("cdparanoia") is not None


def make_path(device: str, track: int) -> str:
    return f"{PREFIX}{device}#{int(track)}"


def is_cd(path: str) -> bool:
    return isinstance(path, str) and path.startswith(PREFIX)


def parse_path(path: str) -> tuple[str, int] | None:
    """cdda:///dev/sr0#3 -> ('/dev/sr0', 3). Anything malformed -> None."""
    if not is_cd(path):
        return None
    dev, _, n = path[len(PREFIX):].partition("#")
    if not dev.startswith("/dev/") or not n.isdigit() or int(n) < 1 or int(n) > 99:
        return None
    return dev, int(n)


def parse_toc(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = _TOC.match(line)
        if m:
            frames = int(m.group(2))
            out.append(dict(track=int(m.group(1)), frames=frames, secs=frames / 75.0))
    return out


def toc(device: str = DEFAULT_DEVICE) -> list[dict]:
    """Audio tracks on the disc in the drive ([] when there is no disc or it is not an audio CD)."""
    if not available():
        return []
    try:
        r = subprocess.run(["cdparanoia", "-Q", "-d", device], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_toc(r.stderr or r.stdout)  # cdparanoia prints the table on stderr


def disc_kind(device: str = DEFAULT_DEVICE) -> str:
    """'audio', 'data' (a disc with a file system, e.g. MP3s on a CD) or 'none'."""
    if toc(device):
        return "audio"
    try:
        r = subprocess.run(["lsblk", "-no", "FSTYPE", device], capture_output=True, text=True, timeout=8)
        if r.stdout.strip():
            return "data"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "none"


def mount_data(device: str = DEFAULT_DEVICE) -> str:
    """Mount a data disc with udisksctl (no root needed) and return the mount point ('' on failure)."""
    try:
        r = subprocess.run(["udisksctl", "mount", "-b", device], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r" at (/\S.*?)\.?\s*$", r.stdout.strip()) or re.search(r"mounted at (/\S.*?)\.?\s*$", r.stderr.strip())
    return m.group(1) if m else ""


def eject(device: str = DEFAULT_DEVICE) -> bool:
    try:
        return subprocess.run(["eject", device], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def span(track: int, pos: float = 0.0) -> str:
    """cdparanoia span for 'track N from <pos> seconds to the end of the track', e.g. 3[1:30.00]-3."""
    pos = max(0.0, float(pos))
    if pos < 0.05:
        return str(int(track))
    return f"{int(track)}[{int(pos) // 60}:{pos - 60 * (int(pos) // 60):05.2f}]-{int(track)}"


SPINUP = 3.0  # seconds a drive typically needs before the first audio arrives (the position clock waits for it)


def rip_argv(device: str, track: int, pos: float = 0.0, fast: bool = True) -> list[str]:
    """cdparanoia command that writes raw 16-bit little-endian 44.1 kHz stereo to stdout.
    fast=True adds -Z (no paranoia checks): on the dev drive full paranoia ran at 0.8x real time and stuttered, -Z ran at 2.6x.
    A scratched disc can click in fast mode; set "cd_safe": true in config.json to use full error correction."""
    return ["cdparanoia", "-q", "-r", *(["-Z"] if fast else []), "-d", device, span(track, pos), "-"]


FFMPEG_IN = ["-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0"]


def musicbrainz_titles(device: str = DEFAULT_DEVICE) -> dict[int, str]:
    """{track number: title}. Needs the `discid` python package (libdiscid) and a network. Any failure -> {}."""
    try:
        import discid  # noqa: PLC0415

        did = discid.read(device).id
        url = f"https://musicbrainz.org/ws/2/discid/{did}?fmt=json&inc=recordings"
        req = urllib.request.Request(url, headers={"User-Agent": "VertiMeeter/0.11 (local)"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        rel = (data.get("releases") or [data])[0]
        media = rel.get("media") or []
        tracks = media[0].get("tracks") if media else []
        return {i + 1: t["title"] for i, t in enumerate(tracks or []) if t.get("title")}
    except Exception:  # noqa: BLE001
        return {}
