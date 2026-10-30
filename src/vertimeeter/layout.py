"""Layout presets. Panes are re-parented into a fresh container each time."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QSplitter, QWidget

from . import theme

ORDER = ["hw", "vi", "fx", "ha", "vb"]


def _style_splitter(sp: QSplitter) -> None:
    t = theme.cur()
    sp.setHandleWidth(6)
    sp.setStyleSheet(f"QSplitter::handle {{ background: {t['accent']}; border: 0; }}"
                     f"QSplitter::handle:hover {{ background: {t['accenthi']}; }}")
    sp.setChildrenCollapsible(False)


def build_container(win) -> QWidget:
    lay = win.cfg.d["layout"]
    if lay["preset"] == "custom":
        return win.build_custom()
    return _grid(win) if lay["preset"] == "potato" else _split(win)


def _split(win) -> QWidget:
    lay = win.cfg.d["layout"]
    win.fxpanel.set_horizontal(False)
    outer = QSplitter(Qt.Orientation.Vertical)
    inner = QSplitter(Qt.Orientation.Horizontal)
    for k in ORDER:
        inner.addWidget(win.panes[k])
    outer.addWidget(inner)
    outer.addWidget(win.media)
    for sp in (outer, inner):
        _style_splitter(sp)
    w = lay["widths"]
    inner.setSizes([w["hw"], w["vi"], w["fx"], 380, 260])
    outer.setSizes([650, lay["media_h"]])
    outer.setStretchFactor(0, 1)
    outer.setStretchFactor(1, 0)
    inner.setStretchFactor(3, 1)
    inner.setStretchFactor(4, 1)

    def save_inner(*_):
        s = inner.sizes()
        w.update(hw=s[0], vi=s[1], fx=s[2])

    def save_outer(*_):
        lay["media_h"] = outer.sizes()[1]

    inner.splitterMoved.connect(save_inner)
    outer.splitterMoved.connect(save_outer)
    return outer


def _grid(win) -> QWidget:
    """Voicemeeter-Potato arrangement: strips on the left, media/FX on top right, buses below."""
    win.fxpanel.set_horizontal(True)
    c = QWidget()
    g = QGridLayout(c)
    g.setContentsMargins(0, 0, 0, 0)
    g.setSpacing(4)
    g.addWidget(win.panes["hw"], 0, 0, 2, 1)
    g.addWidget(win.panes["vi"], 0, 1, 2, 1)
    g.addWidget(win.media, 0, 2)
    g.addWidget(win.panes["fx"], 0, 3)
    g.addWidget(win.panes["ha"], 1, 2)
    g.addWidget(win.panes["vb"], 1, 3)
    g.setColumnMinimumWidth(0, 300)
    g.setColumnMinimumWidth(1, 450)
    g.setColumnStretch(2, 14)
    g.setColumnStretch(3, 10)
    g.setRowMinimumHeight(0, 210)
    g.setRowStretch(0, 0)
    g.setRowStretch(1, 1)
    return c
