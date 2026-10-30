"""A titled pane (mockup 5 .sec): caption, wide scroll slider, [ - ] and [ + v ] buttons, strips scroll sideways."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QInputDialog, QMenu, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .widgets import ClickLabel, ScrollSlider


class HScroll(QScrollArea):
    """Mouse wheel scrolls sideways (like the mockup)."""

    def wheelEvent(self, e):
        hb = self.horizontalScrollBar()
        if hb.maximum() > 0:
            hb.setValue(hb.value() - e.angleDelta().y() // 2 - e.angleDelta().x() // 2)
            e.accept()
        else:
            super().wheelEvent(e)


class Pane(QFrame):
    addRequested = pyqtSignal(object)   # payload from the chosen menu action
    removeRequested = pyqtSignal()
    menuAboutToShow = pyqtSignal(object)  # QMenu, to be filled live
    renamed = pyqtSignal(str, str)        # key, new title

    def __init__(self, key: str, title: str, with_controls=True):
        super().__init__()
        self.key = key
        self.default_title = title
        self.setObjectName("pane")
        self.setMinimumWidth(60)
        v = QVBoxLayout(self)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        bar.setContentsMargins(0, 0, 0, 0)
        self.title = ClickLabel(title)
        self.title.setObjectName("cap")
        self.title.setToolTip("Double-click to rename this pane")
        self.title.doubleClicked.connect(self.rename)
        self.title.setFixedHeight(22)
        bar.addWidget(self.title)
        self.scroll_bar = ScrollSlider()
        bar.addWidget(self.scroll_bar, 1)
        self.minus = QPushButton("-")
        self.plus = QPushButton("+ ▾")
        for b in (self.minus, self.plus):
            b.setObjectName("barbtn")
            b.setFixedHeight(24)
        self.minus.setFixedWidth(34)
        self.plus.setFixedWidth(56)
        self.minus.setToolTip("Remove the selected strip (click its name to select), or the last one")
        self.plus.setToolTip("Add (list reads live when opened)")
        self.minus.clicked.connect(self.removeRequested.emit)
        self.menu = QMenu(self)
        self.menu.aboutToShow.connect(lambda: self._fill())
        self.plus.setMenu(self.menu)
        if with_controls:
            bar.addWidget(self.minus)
            bar.addWidget(self.plus)
        else:
            self.scroll_bar.hide()
        v.addLayout(bar)
        self.area = HScroll()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body = QWidget()
        self.row = QHBoxLayout(self.body)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(4)
        self.row.addStretch(1)
        self.area.setWidget(self.body)
        v.addWidget(self.area, 1)
        hb = self.area.horizontalScrollBar()
        hb.rangeChanged.connect(self._range)
        hb.valueChanged.connect(self.scroll_bar.set_value)
        self.scroll_bar.valueChanged.connect(hb.setValue)

    def set_title(self, t: str) -> None:
        self.title.setText(t)

    def rename(self):
        t, ok = QInputDialog.getText(self, "Rename pane", "Pane name:", text=self.title.text())
        if ok and t.strip():
            self.title.setText(t.strip().upper()[:28])
            self.renamed.emit(self.key, self.title.text())

    def _range(self, lo, hi):
        self.scroll_bar.set_range(hi)

    def _fill(self):
        self.menu.clear()
        self.menuAboutToShow.emit(self.menu)

    def set_widgets(self, widgets: list[QWidget]) -> None:
        while self.row.count() > 1:
            it = self.row.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        for i, w in enumerate(widgets):
            self.row.insertWidget(i, w)
