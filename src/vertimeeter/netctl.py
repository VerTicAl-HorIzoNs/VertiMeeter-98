"""VBAN-TEXT remote control (Voicemeeter-style): "Strip[0].Gain=-3;Bus[1].Mute=1".
The engine only forwards text that came from the allowed sender IP of a network input that has this turned on.
Strip[n] is the n-th strip from the left (0-based), Bus[n] is A1..A5,B1..B3,A6..A8,B4..B8 (0-based; the first eight match Voicemeeter Potato). Gain is in dB.
Blank parts and parts starting with // or # are ignored, so a sender can add comments."""
from __future__ import annotations

import re

REMOTE_BUSES = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "A6", "A7", "A8"] + [f"B{i}" for i in range(4, 9)]

_RE = re.compile(r"^(Strip|Bus)\[(\d+)\]\.(Gain|Mute|Solo|Mono)\s*=\s*(-?[0-9]*\.?[0-9]+)$", re.I)


def apply_text(win, text: str) -> list[str]:
    """Apply every command in one received text. Returns short status lines."""
    out = []
    for part in re.split(r"[;\r\n]+", text):
        part = part.strip()
        if not part or part.startswith(("//", "#")):
            continue
        m = _RE.match(part)
        if not m:
            out.append(f"text control: ignored '{part[:32]}'")
            continue
        kind, n, what, val = m.group(1).lower(), int(m.group(2)), m.group(3).lower(), float(m.group(4))
        if kind == "strip":
            strips = win.cfg.d["strips"]
            if n >= len(strips):
                out.append(f"text control: no Strip[{n}]")
                continue
            w = win.strip_w.get(strips[n]["id"])
        else:
            if n >= len(REMOTE_BUSES):
                out.append(f"text control: no Bus[{n}]")
                continue
            w = win.bus_w.get(REMOTE_BUSES[n])
        if w is None:
            out.append(f"text control: no {kind} {n}")
            continue
        if what == "gain":
            db = max(-60.0, min(12.0, val))
            w.fader.db = db
            w.fader.update()
            w._gain(db)
            out.append(f"text control: {kind} {n} gain {db:+.1f} dB")
        else:
            btn = getattr(w, what, None)
            if btn is None:
                out.append(f"text control: {kind} {n} has no {what}")
                continue
            btn.setChecked(val >= 0.5)
            out.append(f"text control: {kind} {n} {what} {'on' if btn.isChecked() else 'off'}")
    return out
