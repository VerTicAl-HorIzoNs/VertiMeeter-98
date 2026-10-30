"""Knob value -> engine command. One place, so GUI, presets and dialogs stay consistent."""
from __future__ import annotations

TONE_CELL = {"bass": 6, "mid": 7, "treb": 8}


def comp_cmd(slot: int, a: float) -> dict:
    """COMP knob 0..10 -> compressor (threshold falls, ratio rises, small makeup)."""
    return dict(c="comp", i=slot, on=a > 0.05, thr=-4.0 * a, ratio=1.0 + 0.4 * a, att=10.0, rel=120.0, makeup=0.5 * a)


def gate_cmd(slot: int, a: float) -> dict:
    """GATE knob 0..10 -> noise gate threshold -70..-20 dB."""
    return dict(c="gate", i=slot, on=a > 0.05, thr=-70.0 + 5.0 * a, att=2.0, rel=150.0)


def tone_cmd(target: str, idx: int, key: str, db: float) -> dict:
    return dict(c="eq", t=target, i=idx, cell=TONE_CELL[key], on=abs(db) > 0.05,
                kind={"bass": 1, "mid": 0, "treb": 2}[key],
                f={"bass": 200.0, "mid": 1000.0, "treb": 3500.0}[key], g=db, q=0.707)
