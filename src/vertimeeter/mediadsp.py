"""DSP panel of the media player: the same processing a virtual input has (EQ, 3D / modulation pad, M.C., karaoke,
reverb / delay / pan, EXT sends, limiter, voice modeler + insert FX), applied to the ONE media player output
(engine slot 16), so everything the player plays goes through it. Per-file volume stays on each file."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import fxdialogs
from .dialogs import default_eq
from .fxhost import host as _fxhost
from .strips import FT, KAR_MODES, StripBase, _btn, _knob_row

from .config import MEDIA_SLOT as SLOT  # noqa: E402  (engine slot 16)
ID = "MEDIA"


def default_dsp() -> dict:
    return dict(id=ID, label="Media Player", kind="virt", slot=SLOT, gain_db=0.0, mute=False, solo=False, mono=False,
                pan=0.0, sends={}, knobs=dict(bass=0.0, mid=0.0, treb=0.0, rvb=0.0, dly=0.0, ext1=0.0, ext2=0.0),
                post=False, lim=False, lim_db=-6.0, kar=0, mc=False, sp_mode="pan", eq=default_eq())


def ensure(mp: dict) -> dict:
    d = mp.setdefault("dsp", default_dsp())
    for k, v in default_dsp().items():
        d.setdefault(k, v)
    for k, v in default_dsp()["knobs"].items():
        d["knobs"].setdefault(k, v)
    d["slot"] = SLOT  # 0.11.1: configs and scenes saved before 0.11 still say slot 12, so the DSP controls hit the wrong engine strip
    return d


class MediaDsp(StripBase):
    """Built on StripBase so every control sends exactly what a virtual strip sends, just to the media slot."""
    dock_toggled = pyqtSignal()

    def __init__(self, cfg: dict, eng):
        super().__init__(cfg, eng, width=372)
        self.setObjectName("mediadsp")
        self.lay.setContentsMargins(6, 4, 6, 4)
        while self.lay.count():  # the name / fader / meter of StripBase are not used here: gain and meter live in the player header
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        cap = QLabel("PLAYER DSP  (everything the player plays)")
        cap.setObjectName("iplab")
        cap.setFixedHeight(16)
        self.dock_btn = QPushButton("DOCK")
        self.dock_btn.setFixedHeight(16)
        self.dock_btn.setStyleSheet("font-size: 9px; padding: 0 8px;")
        self.dock_btn.clicked.connect(self.dock_toggled.emit)
        hd = QHBoxLayout()
        hd.setContentsMargins(0, 0, 0, 0)
        hd.addWidget(cap, 1)
        hd.addWidget(self.dock_btn)
        self.lay.addLayout(hd)
        body = QHBoxLayout()
        body.setSpacing(6)
        left = QVBoxLayout()
        left.setSpacing(3)
        left.addWidget(self.make_pad(("pan", "3d", "mod"), "pan", "pan"), 0, Qt.AlignmentFlag.AlignHCenter)
        mc = _btn("M.C.", "", cfg["mc"], "Center only (mono centre channel of the signal)", h=18)
        mc.setFixedWidth(46)
        mc.toggled.connect(self._mc)
        self.kar = QComboBox()
        self.kar.addItems(KAR_MODES)
        self.kar.setCurrentIndex(min(2, int(cfg["kar"])))
        self.kar.setToolTip("Karaoke: K1 removes the centre (vocals), K2 removes it but keeps the bass")
        self.kar.setFixedHeight(18)
        self.kar.currentIndexChanged.connect(self._kar)
        r = QHBoxLayout()
        r.setSpacing(3)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(mc)
        r.addWidget(self.kar, 1)
        left.addLayout(r)
        left.addStretch(1)
        body.addLayout(left)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        mid.addLayout(_knob_row(*(self.make_knob(k, lab, -12.0, 12.0, 0.0, fmt=FT)
                                  for k, lab in (("bass", "BASS"), ("mid", "MID"), ("treb", "TREB")))))
        mid.addLayout(_knob_row(self.make_knob("rvb", "RVB"), self.make_knob("dly", "DLY"), self.make_pan_knob()))
        mid.addStretch(1)
        body.addLayout(mid, 1)
        right = QVBoxLayout()
        right.setSpacing(2)
        right.addLayout(self.make_ext_row())
        right.addWidget(self.make_fx_row())
        self.peq_btn = _btn("PEQ", "", False, "6-cell parametric EQ for everything the player plays", h=18)
        self.peq_btn.setCheckable(False)
        self.peq_btn.setStyleSheet("font-size: 10px; padding: 0;")
        self.peq_btn.clicked.connect(self._open_peq)
        self._peq_mark()
        right.addWidget(self.peq_btn)
        right.addStretch(1)
        body.addLayout(right)
        self.lay.addLayout(body)
        self.lay.addStretch(1)

    def set_floating(self, floating: bool):
        self.dock_btn.setText("DOCK" if floating else "FLOAT")
        self.dock_btn.setToolTip("Snap this panel to the right end of the media player" if floating
                                 else "Pop this panel out into its own movable window")

    def _peq_mark(self):
        self.peq_btn.setText("PEQ *" if any(c.get("on") for c in self.cfg.get("eq", [])) else "PEQ")

    def _open_peq(self):
        self.open_peq()
        self._peq_mark()

    def _mc(self, on):
        self.cfg["mc"] = on
        self.eng.send(c="strip", i=SLOT, kar=self._kar_value())

    def _kar(self, i):
        self.cfg["kar"] = i
        self.eng.send(c="strip", i=SLOT, kar=self._kar_value())

    def push(self) -> None:
        """Send only the processing. Gain / mute / routes of the media slot belong to the player header (MediaSection.push)."""
        c = self.cfg
        self.eng.send(c="strip", i=SLOT, mono=c["mono"], ext_post=c["post"], lim_on=c["lim"], lim_db=c["lim_db"],
                      kar=self._kar_value())
        self._send_pad()
        if c.get("voice"):
            fxdialogs.send_voice(self.eng, SLOT, c["voice"])
        if c.get("ins"):
            ins = dict(c["ins"])
            ins["on"] = bool(ins["on"]) and _fxhost().alive(c["id"])
            fxdialogs.send_ins(self.eng, SLOT, ins)
        for k, v in c["knobs"].items():
            self._send_knob(k, v)
        for i, cell in enumerate(c.get("eq", [])):
            self.eng.send(c="eq", t="strip", i=SLOT, cell=i, **cell)
        for k, d in c.get("dyn", {}).items():
            self.eng.send(c=k, i=SLOT, **d)
