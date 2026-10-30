"""Palettes + global stylesheet. Values are copied 1:1 from mockup 5 (purple '98) and the sampled Voicemeeter Potato look."""
from __future__ import annotations

PURPLE = dict(
    name="VertiMeeter '98",
    bg="#0b0b10", panel="#12101a", panel2="#1a1726", border="#4a4560", borderhi="#8a82a8",
    text="#c9c4d8", dim="#7d7794", accent="#a64dff", accenthi="#c18bff", accentdk="#4b1a7a",
    pink="#ff5fa8", red="#e0304f", amber="#e8a21c", teal="#27b7ae", unlit="#1a1626",
    namebg="#4b1a7a", capbg="#12101a", onfg="#000000", trackbg="#0b0914",
    hi="#c18bff", dk="#7a35c0", bright="#ff5fa8", green="#7dff9b",
    title1="#1d0636", title2="#4b1a7a", title3="#8a35e0", logobg="#0b0b10", knobacc="#a64dff",
)

POTATO = dict(
    name="Voicemeeter Potato",
    bg="#2c3d4d", panel="#354a59", panel2="#3f505e", border="#5d7385", borderhi="#8aa3b5",
    text="#e8eef3", dim="#9db0be", accent="#6ec592", accenthi="#9be0b9", accentdk="#32485d",
    pink="#78a196", red="#e0304f", amber="#e8a21c", teal="#27b7ae", unlit="#253845",
    namebg="#4b6f86", capbg="#2e4558", onfg="#06231a", trackbg="#223039",
    hi="#a9f0cc", dk="#3d8f72", bright="#bff2dc", green="#7dff9b",
    title1="#1f2d38", title2="#36495a", title3="#4b6477", logobg="#1a2630", knobacc="#7fdbb0",
)

THEMES = {"purple": PURPLE, "potato": POTATO}


def _legacy(t: dict) -> dict:
    """Old key names still used by dialogs / custom-layout code."""
    return {**t, "header": t["panel"], "btn": t["panel2"], "btn_on": t["accentdk"], "edge": t["accent"],
            "well": "#050508", "meter_bg": "#050508", "fill": t["accent"]}


def stylesheet(t: dict) -> str:
    t = _legacy(t)
    return f"""
* {{ font-family: Tahoma,"DejaVu Sans","Noto Sans",sans-serif; font-size: 11px; color: {t['text']}; }}
QMainWindow, QWidget#root {{ background: {t['bg']}; }}
QDialog {{ background: {t['panel']}; border: 1px solid {t['accent']}; }}
QWidget {{ background: transparent; }}
QMenuBar {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; padding: 0; }}
QMenuBar::item {{ padding: 4px 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {t['accentdk']}; }}
QMenu {{ background: {t['panel']}; border: 1px solid {t['borderhi']}; padding: 2px 0; }}
QMenu::item {{ padding: 3px 22px 3px 18px; }}
QMenu::item:selected {{ background: {t['accent']}; color: {t['onfg']}; }}
QMenu::item:disabled {{ color: {t['dim']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 3px 2px; }}
QMenu::indicator {{ width: 12px; height: 12px; left: 4px; }}
QFrame#pane {{ background: {t['bg']}; border: 1px solid {t['accent']}; }}
QFrame#mediasec {{ background: {t['panel']}; border: 1px solid {t['accent']}; }}
QFrame#mediasec[drag="true"] {{ background: #1d1233; border: 1px dashed {t['accent']}; }}
QFrame#strip {{ background: {t['panel']}; border: 1px solid {t['border']}; }}
QFrame#strip[sel="true"] {{ border: 1px solid {t['accent']}; }}
QFrame#fxbox {{ background: {t['panel']}; border: 1px solid {t['border']}; }}
QFrame#hwstat {{ background: {t['panel']}; border: 1px solid {t['border']}; }}
QFrame#netbox {{ background: {t['panel']}; border: 1px solid {t['border']}; }}
QFrame#netbox:hover {{ border-color: {t['accent']}; }}
QFrame#mlist {{ background: {t['bg']}; border: 1px solid {t['border']}; }}
QFrame#mrow {{ background: transparent; border: 0; border-bottom: 1px solid #1d1a28; }}
QFrame#mrow[sel="true"] {{ background: #1d1233; }}
QFrame#mrow[cur="true"] {{ border-left: 3px solid {t['accent']}; }}
QFrame#titlebar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {t['title1']}, stop:0.5 {t['title2']}, stop:1 {t['title3']});
  border: 0; border-bottom: 1px solid {t['accent']}; }}
QFrame#logo {{ background: {t['logobg']}; border: 1px solid {t['accenthi']}; }}
QLabel#engstat {{ color: #ffffff; font-family: "DejaVu Sans Mono", monospace; }}
QLabel#cap {{ color: {t['accenthi']}; font-weight: bold; font-size: 10px; letter-spacing: 1px; padding: 3px 5px;
  border-top: 1px solid {t['accentdk']}; border-bottom: 1px solid {t['accentdk']}; background: {t['capbg']}; }}
QLabel#sname {{ background: {t['namebg']}; border: 1px solid {t['accent']}; color: #ffffff; font-weight: bold; font-size: 10px; padding: 1px 2px; }}
QLabel#bname {{ background: {t['namebg']}; border: 1px solid {t['accent']}; color: #ffffff; font-weight: bold; font-size: 15px; }}
QLabel#dev {{ border: 1px solid {t['border']}; background: {t['panel']}; padding: 2px 3px; font-size: 10px; }}
QLabel#dev:hover {{ border-color: {t['accent']}; }}
QLabel#dev[info="true"] {{ color: {t['dim']}; }}
QLabel#dev[info="true"]:hover {{ border-color: {t['border']}; }}
QLabel#iplab {{ color: {t['dim']}; font-size: 9px; font-weight: bold; letter-spacing: 1px; padding: 0 2px; }}
QLabel#readout {{ background: {t['panel']}; border: 1px solid {t['border']}; color: {t['accenthi']};
  font-family: "DejaVu Sans Mono", monospace; padding: 1px 2px; }}
QLabel#mono {{ color: {t['accenthi']}; font-family: "DejaVu Sans Mono", monospace; }}
QLabel#dim {{ color: {t['dim']}; }}
QLabel#dl {{ color: {t['dim']}; font-size: 10px; }}
QLabel#dcap {{ color: {t['accenthi']}; font-size: 10px; font-weight: bold; letter-spacing: 1px; padding: 2px 0;
  border-bottom: 1px solid {t['accentdk']}; }}
QLabel#mempty {{ color: {t['dim']}; }}
QLabel#tag {{ color: {t['bg']}; background: {t['teal']}; font-size: 9px; font-weight: bold; padding: 0 3px; }}
QLabel#app {{ border: 1px solid {t['border']}; background: {t['bg']}; color: {t['accenthi']}; font-size: 10px; padding: 0 3px; }}
QLabel#app:hover {{ border-color: {t['accent']}; }}
QLabel#appadd {{ border: 1px solid {t['border']}; background: {t['panel2']}; color: {t['dim']}; font-size: 10px; }}
QLabel#appadd:hover {{ border-color: {t['accent']}; }}
QPushButton {{ background: {t['panel2']}; border: 1px solid {t['border']}; color: {t['text']}; padding: 0 4px; min-height: 16px; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['bg']}; }}
QPushButton:checked {{ background: {t['accent']}; color: {t['onfg']}; border-color: {t['accenthi']}; font-weight: bold; }}
QPushButton:disabled {{ color: {t['dim']}; }}
QPushButton#mute:checked {{ background: {t['red']}; color: #ffffff; border-color: #ff7a90; }}
QPushButton#solo:checked {{ background: {t['amber']}; color: #000000; border-color: #ffd070; }}
QPushButton#mono:checked {{ background: {t['teal']}; color: #000000; border-color: #7fe8e0; }}
QPushButton#rt {{ font-size: 10px; font-weight: bold; padding: 0; }}
QPushButton#rtB {{ font-size: 10px; font-weight: bold; padding: 0; }}
QPushButton#rtB:checked {{ background: {t['pink']}; color: {t['onfg']}; border-color: #ffa6cf; }}
QPushButton#barbtn {{ font-weight: bold; font-size: 15px; padding: 0; }}
QPushButton#small {{ font-size: 10px; min-height: 14px; padding: 0; }}
QPushButton#chip {{ border: 1px solid {t['border']}; color: {t['accenthi']}; font-size: 9px; font-weight: bold; padding: 0 5px; min-height: 12px; }}
QPushButton#chip:hover {{ border-color: {t['accent']}; }}
QPushButton#lim {{ font-size: 9px; padding: 0; color: {t['amber']}; }}
QPushButton#lim:checked {{ background: {t['panel2']}; border-color: {t['border']}; font-weight: normal; }}
QPushButton#post {{ font-size: 9px; padding: 0; }}
QPushButton#fxon:checked {{ background: {t['accent']}; color: {t['onfg']}; font-weight: bold; }}
QPushButton#mac {{ border: 2px solid {t['accentdk']}; }}
QComboBox {{ background: {t['bg']}; border: 1px solid {t['border']}; padding: 0 4px; min-height: 18px; font-size: 10px; }}
QComboBox::drop-down {{ border: 0; width: 16px; }}
QComboBox QAbstractItemView {{ background: {t['panel']}; selection-background-color: {t['accent']};
  selection-color: {t['onfg']}; border: 1px solid {t['borderhi']}; }}
QScrollBar:horizontal {{ background: {t['panel']}; height: 12px; border: 0; }}
QScrollBar::handle:horizontal {{ background: {t['border']}; min-width: 30px; border: 1px solid #000; }}
QScrollBar:vertical {{ background: {t['panel']}; width: 12px; border: 0; }}
QScrollBar::handle:vertical {{ background: {t['border']}; min-height: 30px; border: 1px solid #000; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: 0; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QToolTip {{ background: #0d0a16; color: {t['text']}; border: 1px solid {t['accent']}; padding: 4px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {t['bg']}; border: 1px solid {t['border']}; padding: 0 4px; min-height: 18px; }}
QStatusBar {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
QStatusBar QLabel {{ color: {t['dim']}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {t['borderhi']}; background: {t['panel2']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; }}
"""


_cur = {"t": PURPLE}


def cur() -> dict:
    return _legacy(_cur["t"])


def set_cur(key: str) -> dict:
    _cur["t"] = THEMES[key]
    return cur()
