"""Cortex UI kit: colour system, stylesheet, vector icons and animated widgets."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize,
    Qt, QTimer, QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QFontDatabase, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)


class Theme:
    BG = "#060914"
    BG_TOP = "#0C1230"
    BG_BOT = "#04060E"
    SURFACE = "#0E1428"
    SURFACE_2 = "#141C36"
    SURFACE_3 = "#1C2647"
    SURFACE_4 = "#27345E"
    BORDER = "#222E50"
    BORDER_HI = "#364877"
    GRID = "#151E3A"

    TEXT = "#EEF3FF"
    TEXT_MUTED = "#93A0C0"
    TEXT_FAINT = "#5E6B8C"

    ACCENT = "#19E6C3"
    ACCENT_2 = "#3DA9FF"
    ACCENT_DIM = "#0E8F7C"
    BLUE = "#5B8CFF"
    PURPLE = "#A78BFA"
    PINK = "#FF7AD9"
    GOLD = "#FFC857"
    ORANGE = "#FF9F43"
    BULL = "#1FE08A"
    BEAR = "#FF4D67"
    WARN = "#FFB547"

    RADIUS = 13
    RADIUS_SM = 8

    @classmethod
    def _pick(cls, options, fallback):
        fams = {f.lower(): f for f in QFontDatabase.families()}
        for o in options:
            if o.lower() in fams:
                return o
        return fallback

    @classmethod
    def ui_font(cls) -> str:
        return cls._pick(("Segoe UI Variable Display", "Segoe UI", "Inter", "SF Pro Display", "Roboto", "Arial"), "Sans Serif")

    @classmethod
    def mono_font(cls) -> str:
        return cls._pick(("Cascadia Mono", "Consolas", "JetBrains Mono", "Menlo", "DejaVu Sans Mono", "Courier New"), "monospace")


def make_font(size: float, weight=QFont.Weight.Normal, mono: bool = False, spacing: float = 0.0) -> QFont:
    """Crisp, antialiased font (fixes the flat / pixelated look)."""
    f = QFont(Theme.mono_font() if mono else Theme.ui_font())
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def stylesheet(font: str) -> str:
    T = Theme
    return f"""
    * {{ font-family: "{font}"; color: {T.TEXT}; outline: none; }}
    QWidget#Clear {{ background: transparent; }}
    QScrollArea {{ background: transparent; border: none; }}
    QLabel {{ background: transparent; }}
    QLabel#Tip {{ background: {T.SURFACE_4}; color: {T.TEXT}; border: 1px solid {T.BORDER_HI};
                  border-radius: 7px; padding: 5px 10px; font-size: 11px; font-weight: 600; }}
    QFrame#Toast {{ background: {T.SURFACE_3}; border: 1px solid {T.ACCENT_DIM}; border-radius: 10px; }}
    QToolTip {{ background: {T.SURFACE_4}; color: {T.TEXT}; border: 1px solid {T.BORDER_HI};
               padding: 5px 8px; border-radius: 6px; font-size: 11px; }}

    QWidget#Sidebar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0B1130, stop:1 #060916);
                       border-right: 1px solid {T.BORDER}; }}
    QLabel#Wordmark {{ font-size: 19px; font-weight: 800; letter-spacing: 3px; }}
    QLabel#Tagline {{ color: {T.TEXT_FAINT}; font-size: 9px; letter-spacing: 2px; font-weight: 600; }}
    QFrame#NavIndicator {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {T.ACCENT}, stop:1 {T.ACCENT_2});
                           border-radius: 2px; }}

    QLabel#PageTitle {{ font-size: 20px; font-weight: 800; }}
    QLabel#PageCrumb {{ font-size: 11px; color: {T.TEXT_FAINT}; }}
    QLabel#Pill {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: 12px;
                   padding: 0px 11px; color: {T.TEXT_MUTED}; font-size: 11px; font-weight: 600;
                   min-height: 24px; max-height: 24px; }}

    QFrame#Card {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {T.SURFACE_3}, stop:0.55 {T.SURFACE_2}, stop:1 {T.SURFACE});
                   border: 1px solid {T.BORDER}; border-radius: {T.RADIUS}px; }}
    QFrame#Panel {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {T.SURFACE_2}, stop:1 {T.SURFACE});
                    border: 1px solid {T.BORDER}; border-radius: {T.RADIUS}px; }}
    QFrame#Flat {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS_SM}px; }}
    QFrame#ChartCard {{ background: {T.BG}; border: 1px solid {T.BORDER_HI}; border-radius: {T.RADIUS}px; }}
    QFrame#Divider {{ background: {T.BORDER}; }}
    QFrame#WatchPanel {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0C1230, stop:1 #070A18);
                         border-left: 1px solid {T.BORDER}; }}

    QLabel#CardLabel {{ color: {T.TEXT_MUTED}; font-size: 9px; letter-spacing: 1px; font-weight: 700; }}
    QLabel#CardValue {{ font-size: 24px; font-weight: 800; }}
    QLabel#CardHint {{ color: {T.TEXT_FAINT}; font-size: 10px; }}
    QLabel#SectionTitle {{ font-size: 13px; font-weight: 800; }}
    QLabel#Group {{ color: {T.TEXT_MUTED}; font-size: 9px; letter-spacing: 1px; font-weight: 800; }}
    QLabel#Body {{ color: {T.TEXT_MUTED}; font-size: 12px; }}
    QLabel#Mini {{ color: {T.TEXT_FAINT}; font-size: 10px; }}
    QLabel#Empty {{ color: {T.TEXT_FAINT}; font-size: 12px; padding: 18px; }}
    QLabel#DayHeader {{ color: {T.ACCENT}; font-size: 10px; letter-spacing: 2px; font-weight: 800; padding-top: 6px; }}

    QPushButton#Primary {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {T.ACCENT}, stop:1 {T.ACCENT_2});
        color: #03101A; border: none; border-radius: {T.RADIUS_SM}px;
        padding: 9px 16px; font-size: 13px; font-weight: 800;
    }}
    QPushButton#Primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5FF5DC, stop:1 #7CC6FF); }}
    QPushButton#Primary:pressed {{ background: {T.ACCENT_DIM}; }}
    QPushButton#Primary:disabled {{ background: {T.SURFACE_3}; color: {T.TEXT_FAINT}; }}
    QPushButton#Danger {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {T.BEAR}, stop:1 #FF7A59);
                          color: #1B0509; border: none; border-radius: {T.RADIUS_SM}px;
                          padding: 9px 14px; font-size: 13px; font-weight: 800; }}
    QPushButton#Danger:disabled {{ background: {T.SURFACE_3}; color: {T.TEXT_FAINT}; }}
    QPushButton#Ghost {{ background: rgba(255,255,255,0.03); color: {T.TEXT}; border: 1px solid {T.BORDER_HI};
                         border-radius: {T.RADIUS_SM}px; padding: 8px 13px; font-size: 12px; font-weight: 600; }}
    QPushButton#Ghost:hover {{ border-color: {T.ACCENT}; color: {T.ACCENT}; background: rgba(25,230,195,0.07); }}
    QPushButton#Ghost:disabled {{ color: {T.TEXT_FAINT}; border-color: {T.BORDER}; }}

    QPushButton#Tool {{ background: {T.SURFACE_2}; color: {T.TEXT_MUTED}; border: 1px solid {T.BORDER};
                        border-radius: 7px; padding: 6px 10px; font-size: 11px; font-weight: 600; }}
    QPushButton#Tool:hover {{ color: {T.TEXT}; border-color: {T.BORDER_HI}; background: {T.SURFACE_3}; }}
    QPushButton#Tool:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {T.ACCENT}, stop:1 {T.ACCENT_2});
                                color: #03101A; border-color: {T.ACCENT}; font-weight: 800; }}
    QPushButton#Seg {{ background: transparent; color: {T.TEXT_MUTED}; border: none;
                       border-radius: 6px; padding: 6px 12px; font-size: 12px; font-weight: 600; }}
    QPushButton#Seg:hover {{ color: {T.TEXT}; }}
    QPushButton#Seg:checked {{ background: {T.SURFACE_4}; color: {T.ACCENT}; font-weight: 800; }}
    QPushButton#Icon {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: 8px; }}
    QPushButton#Icon:hover {{ border-color: {T.ACCENT_DIM}; background: {T.SURFACE_3}; }}
    QPushButton#Icon:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {T.ACCENT}, stop:1 {T.ACCENT_2});
                                border-color: {T.ACCENT}; }}
    QPushButton#IconFlat {{ background: transparent; border: none; border-radius: 7px; }}
    QPushButton#IconFlat:hover {{ background: {T.SURFACE_3}; }}

    QFrame#ModeCard {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS_SM}px; }}
    QFrame#ModeCard:hover {{ border-color: {T.BORDER_HI}; }}
    QFrame#ModeCard[active="true"] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(25,230,195,0.20), stop:1 rgba(61,169,255,0.10));
                                      border: 1px solid {T.ACCENT}; }}

    QComboBox, QSpinBox, QDoubleSpinBox {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: 7px;
                 padding: 6px 9px; font-size: 12px; color: {T.TEXT}; min-width: 56px; }}
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {T.BORDER_HI}; }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {T.ACCENT}; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER_HI}; color: {T.TEXT};
                                   selection-background-color: {T.SURFACE_4}; selection-color: {T.ACCENT}; padding: 3px; }}

    QCheckBox {{ color: {T.TEXT_MUTED}; font-size: 12px; spacing: 7px; padding: 2px; background: transparent; }}
    QCheckBox:hover {{ color: {T.TEXT}; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {T.BORDER_HI}; border-radius: 4px; background: {T.SURFACE}; }}
    QCheckBox::indicator:checked {{ background: {T.ACCENT}; border-color: {T.ACCENT}; }}

    QLineEdit, QPlainTextEdit {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER}; border-radius: {T.RADIUS_SM}px;
                 padding: 8px 12px; font-size: 13px; color: {T.TEXT}; selection-background-color: {T.ACCENT_DIM}; }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {T.ACCENT}; }}
    QListWidget#Suggest {{ background: {T.SURFACE_3}; border: 1px solid {T.ACCENT_DIM}; border-radius: {T.RADIUS_SM}px;
                           padding: 4px; font-size: 12px; }}
    QListWidget#Suggest::item {{ padding: 7px 9px; border-radius: 6px; }}
    QListWidget#Suggest::item:selected {{ background: {T.SURFACE_4}; color: {T.ACCENT}; }}

    QSlider::groove:horizontal {{ height: 4px; background: {T.BORDER}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {T.ACCENT}; width: 13px; margin: -5px 0; border-radius: 6px; }}
    QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {T.ACCENT_DIM}, stop:1 {T.ACCENT}); border-radius: 2px; }}

    QTableWidget {{ background: transparent; border: none; gridline-color: transparent; font-size: 12px; }}
    QTableWidget::item {{ padding: 5px 7px; border-bottom: 1px solid {T.BORDER}; }}
    QTableWidget::item:selected {{ background: {T.SURFACE_3}; color: {T.TEXT}; }}
    QHeaderView {{ background: {T.SURFACE_2}; border: none; border-radius: 6px; }}
    QHeaderView::section {{ background: {T.SURFACE_2}; color: {T.TEXT_MUTED}; padding: 7px; border: none;
                            border-bottom: 1px solid {T.BORDER}; font-size: 10px; font-weight: 800; }}
    QHeaderView::section:hover {{ color: {T.ACCENT}; }}
    QTableCornerButton::section {{ background: {T.SURFACE_2}; border: none; }}

    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {T.BORDER_HI}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {T.ACCENT_DIM}; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 1px; }}
    QScrollBar::handle:horizontal {{ background: {T.BORDER_HI}; border-radius: 4px; min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QMenu {{ background: {T.SURFACE_2}; border: 1px solid {T.BORDER_HI}; border-radius: 9px; padding: 6px; }}
    QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 6px; font-size: 12px; color: {T.TEXT}; }}
    QMenu::item:selected {{ background: {T.SURFACE_4}; color: {T.ACCENT}; }}
    QMenu::separator {{ height: 1px; background: {T.BORDER}; margin: 5px 8px; }}
    """


def soft_shadow(w: QWidget, blur: int = 22, color: Optional[QColor] = None, dy: int = 6):
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(blur)
    eff.setColor(color or QColor(0, 0, 0, 140))
    eff.setOffset(0, dy)
    w.setGraphicsEffect(eff)
    return eff


def scroll_page(inner: QWidget, horizontal: bool = False) -> QScrollArea:
    inner.setObjectName("Clear")
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setWidget(inner)
    s.setFrameShape(QFrame.Shape.NoFrame)
    # NOTE: never put a bare stylesheet on the viewport — Qt cascades it to every
    # descendant and wipes button/card backgrounds. An object name is enough.
    s.viewport().setObjectName("Clear")
    s.viewport().setAutoFillBackground(False)
    if not horizontal:
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return s


def group_label(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setObjectName("Group")
    return l


def divider(vertical: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedWidth(1) if vertical else f.setFixedHeight(1)
    return f


def clear_layout(layout) -> None:
    """Remove every widget from a layout *now*.

    deleteLater() alone leaves the old widgets alive (and painted on top of the
    new ones) until the event loop gets round to them, so hide and detach first.
    """
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def fmt_compact(value: float) -> str:
    v = float(value or 0)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.2f}{suf}"
    return f"{v:,.0f}"


def paint_backdrop(size: QSize) -> QPixmap:
    """Rich app background: deep gradient with two soft colour glows."""
    pm = QPixmap(max(1, size.width()), max(1, size.height()))
    p = QPainter(pm)
    w, h = pm.width(), pm.height()
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor(Theme.BG_TOP))
    g.setColorAt(0.55, QColor("#070B1C"))
    g.setColorAt(1, QColor(Theme.BG_BOT))
    p.fillRect(pm.rect(), QBrush(g))
    for cx, cy, rad, col in ((w * 0.18, -h * 0.05, w * 0.55, QColor(25, 230, 195, 34)),
                             (w * 0.95, h * 0.1, w * 0.5, QColor(91, 140, 255, 40)),
                             (w * 0.6, h * 1.1, w * 0.6, QColor(167, 139, 250, 30))):
        rg = QRadialGradient(cx, cy, rad)
        rg.setColorAt(0, col)
        rg.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.fillRect(pm.rect(), QBrush(rg))
    p.end()
    return pm


# ===========================================================================
# Vector icons — painted, so they never depend on a font having the glyph
# ===========================================================================
ICON_LABELS = {
    "cursor": "Cursor / select", "trend": "Trend line", "ray": "Ray", "extended": "Extended line",
    "hline": "Horizontal line", "hray": "Horizontal ray", "vline": "Vertical line",
    "channel": "Parallel channel", "rect": "Rectangle", "ellipse": "Ellipse",
    "fib": "Fib retracement", "fibext": "Fib extension", "measure": "Measure",
    "arrow": "Arrow", "text": "Text note", "long": "Long position", "short": "Short position",
}


def draw_icon(p: QPainter, kind: str, rect: QRectF, color: QColor, width: float = 1.6):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = min(rect.width(), rect.height()) / 20.0
    ox = rect.x() + (rect.width() - 20 * s) / 2
    oy = rect.y() + (rect.height() - 20 * s) / 2

    def P(x, y):
        return QPointF(ox + x * s, oy + y * s)

    def line(x1, y1, x2, y2):
        p.drawLine(P(x1, y1), P(x2, y2))

    def dot(x, y, r=1.5):
        p.setBrush(color)
        p.drawEllipse(P(x, y), r * s, r * s)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def box(x, y, w, h, rad=0.0, fill=False):
        if fill:
            p.setBrush(color)
        p.drawRoundedRect(QRectF(ox + x * s, oy + y * s, w * s, h * s), rad * s, rad * s)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def poly(pts, fill=True):
        if fill:
            p.setBrush(color)
        p.drawPolygon(QPolygonF([P(x, y) for x, y in pts]))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def circle(x, y, r):
        p.drawEllipse(P(x, y), r * s, r * s)

    def arc(x, y, r, start, span):
        p.drawArc(QRectF(ox + (x - r) * s, oy + (y - r) * s, 2 * r * s, 2 * r * s), int(start * 16), int(span * 16))

    k = kind
    if k == "dashboard":
        for bx, by in ((3, 3), (11, 3), (3, 11), (11, 11)):
            box(bx, by, 6, 6, 1.6)
    elif k == "scanner":
        circle(10, 10, 7)
        circle(10, 10, 3.2)
        line(10, 10, 15, 5)
        dot(14.4, 12.8, 1.2)
    elif k == "charts":
        line(5, 4, 5, 16)
        box(3.5, 7, 3, 6, 0.6, True)
        line(10, 2.5, 10, 14)
        box(8.5, 5, 3, 6, 0.6, True)
        line(15, 6, 15, 17.5)
        box(13.5, 9, 3, 5, 0.6, True)
    elif k == "news":
        box(3, 3.5, 14, 13, 2)
        line(6, 7.5, 14, 7.5)
        line(6, 10.5, 14, 10.5)
        line(6, 13.5, 11, 13.5)
    elif k == "watch":
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            r = 7.5 if i % 2 == 0 else 3.3
            pts.append((10 + r * math.cos(ang), 10.5 + r * math.sin(ang)))
        poly(pts, False)
    elif k == "menu":
        line(4, 6, 16, 6)
        line(4, 10, 16, 10)
        line(4, 14, 16, 14)
    elif k == "search":
        circle(9, 9, 5)
        line(13, 13, 17, 17)
    elif k == "cursor":
        line(10, 3, 10, 8)
        line(10, 12, 10, 17)
        line(3, 10, 8, 10)
        line(12, 10, 17, 10)
        dot(10, 10, 1.1)
    elif k == "trend":
        line(4, 15, 16, 5)
        dot(4, 15)
        dot(16, 5)
    elif k == "ray":
        line(3, 14, 17, 6)
        dot(3, 14)
        line(17, 6, 13.2, 6.2)
        line(17, 6, 15.2, 9.4)
    elif k == "extended":
        line(2, 15, 18, 5)
        dot(7, 11.9)
        dot(13, 8.1)
    elif k == "hline":
        line(2, 10, 18, 10)
        dot(10, 10)
    elif k == "hray":
        line(5, 10, 18, 10)
        dot(5, 10)
    elif k == "vline":
        line(10, 2, 10, 18)
        dot(10, 10)
    elif k == "channel":
        line(3, 13, 15, 5)
        line(5, 17, 17, 9)
    elif k == "rect":
        box(3, 5, 14, 10, 1)
    elif k == "ellipse":
        p.drawEllipse(P(10, 10), 7 * s, 5 * s)
    elif k == "fib":
        for y in (4, 8, 12, 16):
            line(3, y, 17, y)
    elif k == "fibext":
        for y in (6, 11, 16):
            line(8, y, 17, y)
        line(3, 16, 8, 6)
    elif k == "measure":
        box(2.5, 7, 15, 6, 1)
        for x, y2 in ((5.5, 10), (8.5, 9), (11.5, 10), (14.5, 9)):
            line(x, 7, x, y2)
    elif k == "arrow":
        line(4, 16, 15, 5)
        line(15, 5, 9.6, 5.6)
        line(15, 5, 14.4, 10.4)
    elif k == "text":
        line(5, 5, 15, 5)
        line(10, 5, 10, 16)
    elif k == "long":
        poly([(10, 4), (16, 13), (4, 13)])
        line(4, 16.5, 16, 16.5)
    elif k == "short":
        poly([(10, 16), (16, 7), (4, 7)])
        line(4, 3.5, 16, 3.5)
    elif k == "undo":
        line(4, 8, 13, 8)
        line(4, 8, 7.5, 4.5)
        line(4, 8, 7.5, 11.5)
        arc(13, 11.5, 3.5, 90, -180)
        line(13, 15, 8, 15)
    elif k == "redo":
        line(16, 8, 7, 8)
        line(16, 8, 12.5, 4.5)
        line(16, 8, 12.5, 11.5)
        arc(7, 11.5, 3.5, 90, 180)
        line(7, 15, 12, 15)
    elif k == "trash":
        line(4, 6, 16, 6)
        line(8, 6, 8, 4)
        line(8, 4, 12, 4)
        line(12, 4, 12, 6)
        box(5.5, 6, 9, 11, 1.5)
        line(8.5, 9, 8.5, 14)
        line(11.5, 9, 11.5, 14)
    elif k == "delete":
        box(3, 5, 14, 10, 1.5)
        line(8, 8, 12, 12)
        line(12, 8, 8, 12)
    elif k == "plus":
        line(10, 4, 10, 16)
        line(4, 10, 16, 10)
    elif k == "minus":
        line(4, 10, 16, 10)
    elif k == "close":
        line(5, 5, 15, 15)
        line(15, 5, 5, 15)
    elif k == "fit":
        for cx, cy, dx, dy in ((4, 4, 1, 1), (16, 4, -1, 1), (4, 16, 1, -1), (16, 16, -1, -1)):
            line(cx, cy, cx + 4 * dx, cy)
            line(cx, cy, cx, cy + 4 * dy)
    elif k in ("reset", "refresh"):
        arc(10, 10, 6, 50, 280)
        line(14.2, 5.6, 14.6, 9.0)
        line(14.2, 5.6, 10.9, 6.2)
    elif k == "play":
        poly([(6, 4), (16, 10), (6, 16)])
    elif k == "pause":
        box(5, 4, 3.4, 12, 0.8, True)
        box(11.6, 4, 3.4, 12, 0.8, True)
    elif k == "stepf":
        poly([(4, 4), (12, 10), (4, 16)])
        line(15, 4, 15, 16)
    elif k == "stepb":
        poly([(16, 4), (8, 10), (16, 16)])
        line(5, 4, 5, 16)
    elif k == "camera":
        box(3, 6, 14, 10, 2)
        circle(10, 11, 3)
        line(7, 6, 8.5, 4)
        line(8.5, 4, 11.5, 4)
        line(11.5, 4, 13, 6)
    elif k == "gear":
        circle(10, 10, 3)
        for i in range(8):
            a = i * math.pi / 4
            line(10 + 5.2 * math.cos(a), 10 + 5.2 * math.sin(a), 10 + 7.4 * math.cos(a), 10 + 7.4 * math.sin(a))
    elif k == "panel":
        box(3, 4, 14, 12, 2)
        line(12.5, 4, 12.5, 16)
    elif k == "export":
        line(10, 3, 10, 12)
        line(6, 8.5, 10, 12.5)
        line(14, 8.5, 10, 12.5)
        line(4, 16, 16, 16)
    elif k == "indicators":
        line(3, 13, 7, 8)
        line(7, 8, 11, 12)
        line(11, 12, 17, 5)
        dot(17, 5, 1.2)
    elif k == "compare":
        line(3, 14, 8, 9)
        line(8, 9, 12, 12)
        line(12, 12, 17, 6)
        line(3, 8, 8, 12)
        line(8, 12, 17, 15)
    elif k == "up":
        line(5, 12, 10, 7)
        line(10, 7, 15, 12)
    elif k == "down":
        line(5, 8, 10, 13)
        line(10, 13, 15, 8)
    else:
        circle(10, 10, 6)
    p.restore()


class TipBubble:
    """Instant, reliable hover label (replaces flaky native tooltips)."""

    @staticmethod
    def show_for(widget: QWidget, text: str, side: str = "right"):
        if not text:
            return
        win = widget.window()
        tip = getattr(win, "_cortex_tip", None)
        if tip is None:
            tip = QLabel(win)
            tip.setObjectName("Tip")
            tip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            win._cortex_tip = tip
        tip.setText(text)
        tip.adjustSize()
        if side == "bottom":
            pos = widget.mapTo(win, QPoint((widget.width() - tip.width()) // 2, widget.height() + 6))
        elif side == "left":
            pos = widget.mapTo(win, QPoint(-tip.width() - 8, (widget.height() - tip.height()) // 2))
        else:
            pos = widget.mapTo(win, QPoint(widget.width() + 8, (widget.height() - tip.height()) // 2))
        pos.setX(max(4, min(win.width() - tip.width() - 4, pos.x())))
        pos.setY(max(4, min(win.height() - tip.height() - 4, pos.y())))
        tip.move(pos)
        tip.show()
        tip.raise_()

    @staticmethod
    def hide_for(widget: QWidget):
        tip = getattr(widget.window(), "_cortex_tip", None)
        if tip is not None:
            tip.hide()


class IconButton(QPushButton):
    """Button with a painted vector icon and an instant hover label."""

    def __init__(self, kind: str, tip: str = "", size: int = 30, flat: bool = False, tip_side: str = "bottom"):
        super().__init__()
        self.kind, self.tip, self.tip_side = kind, tip, tip_side
        self.setObjectName("IconFlat" if flat else "Icon")
        self.setFixedSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False

    def set_kind(self, kind: str):
        self.kind = kind
        self.update()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        TipBubble.show_for(self, self.tip, self.tip_side)
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        TipBubble.hide_for(self)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        if not self.isEnabled():
            col = QColor(Theme.TEXT_FAINT)
        elif self.isChecked():
            col = QColor("#03101A")
        elif self._hover:
            col = QColor(Theme.ACCENT)
        else:
            col = QColor(Theme.TEXT_MUTED)
        m = max(5.0, self.width() * 0.2)
        draw_icon(p, self.kind, QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m), col)
        p.end()


class Toast:
    """Small slide-in notification at the bottom of the window."""

    @staticmethod
    def show(win: QWidget, text: str, kind: str = "info", ms: int = 2600):
        old = getattr(win, "_cortex_toast", None)
        if old is not None:
            try:
                old.hide()
                old.deleteLater()
            except RuntimeError:
                pass
        box = QFrame(win)
        box.setObjectName("Toast")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(14, 9, 16, 9)
        lay.setSpacing(9)
        col = {"ok": Theme.BULL, "warn": Theme.WARN, "error": Theme.BEAR}.get(kind, Theme.ACCENT)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {col}; font-size: 11px;")
        lab = QLabel(text)
        lab.setStyleSheet("font-size: 12px; font-weight: 600;")
        lay.addWidget(dot)
        lay.addWidget(lab)
        box.adjustSize()
        x = (win.width() - box.width()) // 2
        y_end = win.height() - box.height() - 26
        box.move(x, win.height() + 4)
        box.show()
        box.raise_()
        anim = QPropertyAnimation(box, b"pos", box)
        anim.setDuration(320)
        anim.setStartValue(QPoint(x, win.height() + 4))
        anim.setEndValue(QPoint(x, y_end))
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.start()
        box._anim = anim
        win._cortex_toast = box

        def close():
            try:
                out = QPropertyAnimation(box, b"pos", box)
                out.setDuration(260)
                out.setStartValue(box.pos())
                out.setEndValue(QPoint(box.x(), win.height() + 4))
                out.setEasingCurve(QEasingCurve.Type.InCubic)
                out.finished.connect(box.hide)
                out.start()
                box._out = out
            except RuntimeError:
                pass

        QTimer.singleShot(ms, close)
        return box


# ===========================================================================
# Layout + animated widgets
# ===========================================================================
class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._sp
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Equalizer(QWidget):
    def __init__(self, bars=11, parent=None):
        super().__init__(parent)
        self._n, self._ph = bars, 0.0
        self.setFixedSize(QSize(bars * 11, 40))
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(40)

    def _tick(self):
        self._ph += 0.15
        if self.isVisible():
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        h = self.height()
        for i in range(self._n):
            v = 0.5 + 0.5 * math.sin(self._ph + i * 0.7)
            v2 = 0.5 + 0.5 * math.sin(self._ph * 0.6 + i * 1.3)
            bh = 5 + (0.6 * v + 0.4 * v2) * (h - 7)
            col = QColor(Theme.BULL if math.cos(self._ph + i * 0.7) >= 0 else Theme.BEAR)
            col.setAlpha(215)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(i * 11 + 2, (h - bh) / 2, 6, bh), 2, 2)
        p.end()


class PulseDot(QWidget):
    """Softly pulsing status dot."""

    def __init__(self, color: str = Theme.ACCENT, size: int = 14):
        super().__init__()
        self.setFixedSize(QSize(size, size))
        self._c, self._ph = QColor(color), 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(50)

    def set_color(self, color: str):
        self._c = QColor(color)
        self.update()

    def _tick(self):
        self._ph += 0.13
        if self.isVisible():
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        c = self.rect().center()
        pulse = 0.5 + 0.5 * math.sin(self._ph)
        halo = QColor(self._c)
        halo.setAlpha(int(30 + 60 * (1 - pulse)))
        r = self.width() / 2
        p.setBrush(halo)
        p.drawEllipse(QPointF(c.x() + 0.5, c.y() + 0.5), r * (0.55 + 0.45 * pulse), r * (0.55 + 0.45 * pulse))
        p.setBrush(self._c)
        p.drawEllipse(QPointF(c.x() + 0.5, c.y() + 0.5), r * 0.42, r * 0.42)
        p.end()


class Radar(QWidget):
    """Rotating radar sweep shown while a scan is running."""

    def __init__(self, size: int = 58):
        super().__init__()
        self.setFixedSize(QSize(size, size))
        self._ang, self._active = 0.0, False
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def set_active(self, on: bool):
        self._active = on
        self._t.start(33) if on else self._t.stop()
        self.update()

    def _tick(self):
        self._ang = (self._ang + 5.5) % 360
        if self.isVisible():
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = self.width() / 2 - 2
        col = QColor(Theme.ACCENT if self._active else Theme.TEXT_FAINT)
        ring = QColor(col)
        ring.setAlpha(90)
        p.setPen(QPen(ring, 1))
        for f in (1.0, 0.66, 0.33):
            p.drawEllipse(c, r * f, r * f)
        p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        if self._active:
            g = QConicalGradient(c, -self._ang)
            g.setColorAt(0.0, QColor(25, 230, 195, 150))
            g.setColorAt(0.18, QColor(25, 230, 195, 0))
            g.setColorAt(1.0, QColor(25, 230, 195, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, r, r)
            a = math.radians(self._ang)
            p.setPen(QPen(QColor(Theme.ACCENT), 1.6))
            p.drawLine(c, QPointF(c.x() + r * math.cos(a), c.y() + r * math.sin(a)))
        p.end()


class ProgressStrip(QWidget):
    def __init__(self, height=7):
        super().__init__()
        self.setFixedHeight(height)
        self._frac, self._ph, self._active = 0.0, 0.0, False
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def set_progress(self, f):
        self._frac = max(0.0, min(1.0, f))
        self.update()

    def set_active(self, on: bool):
        self._active = on
        self._t.start(32) if on else self._t.stop()
        self.update()

    def _tick(self):
        self._ph = (self._ph + 0.02) % 1.0
        if self.isVisible():
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.BORDER))
        p.drawRoundedRect(r, 3, 3)
        w = r.width() * self._frac
        if self._active and w < 5:
            w = 5
        if w > 0:
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor(Theme.ACCENT_DIM))
            g.setColorAt(1, QColor(Theme.ACCENT_2))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(0, 0, w, r.height()), 3, 3)
            if self._active:
                sx = self._ph * (w + 70) - 70
                sg = QLinearGradient(sx, 0, sx + 70, 0)
                sg.setColorAt(0, QColor(255, 255, 255, 0))
                sg.setColorAt(0.5, QColor(255, 255, 255, 115))
                sg.setColorAt(1, QColor(255, 255, 255, 0))
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, w, r.height()), 3, 3)
                p.setClipPath(clip)
                p.fillRect(QRectF(sx, 0, 70, r.height()), QBrush(sg))
        p.end()


class CountUpLabel(QLabel):
    def __init__(self, fmt="{:,.0f}", parent=None):
        super().__init__("—", parent)
        self.setObjectName("CardValue")
        self.fmt = fmt
        self._value = 0.0
        self._anim = None

    def animate_to(self, target: float, ms=750):
        a = QVariantAnimation(self)
        a.setStartValue(float(self._value))
        a.setEndValue(float(target))
        a.setDuration(ms)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(lambda v: self.setText(self.fmt.format(float(v))))
        a.start()
        self._anim, self._value = a, float(target)

    def set_now(self, target: float):
        self._value = float(target)
        self.setText(self.fmt.format(float(target)))

    def set_text(self, text: str):
        self._value = 0.0
        self.setText(text)


class Sparkline(QWidget):
    """Sparkline from a real series, drawn in with an animation."""

    def __init__(self, height=34, line_width: float = 1.7):
        super().__init__()
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._data: list[float] = []
        self._color = QColor(Theme.ACCENT)
        self._grow = 1.0
        self._lw = line_width
        self._anim = None

    def set_data(self, data: list[float], color: Optional[str] = None, animate=True):
        self._data = [float(x) for x in data if x == x]
        if color:
            self._color = QColor(color)
        if animate and self._data:
            a = QVariantAnimation(self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(700)
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.valueChanged.connect(self._set_grow)
            a.start()
            self._anim = a
        else:
            self._grow = 1.0
        self.update()

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        if len(self._data) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        lo, hi = min(self._data), max(self._data)
        rng = (hi - lo) or 1
        n = max(2, int(len(self._data) * self._grow))
        step = w / (len(self._data) - 1)
        pts = [QPointF(i * step, h - 3 - (v - lo) / rng * (h - 7)) for i, v in enumerate(self._data[:n])]
        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(QPointF(pts[-1].x(), h))
        fill.lineTo(QPointF(0, h))
        fill.closeSubpath()
        g = QLinearGradient(0, 0, 0, h)
        c = QColor(self._color)
        c.setAlpha(90)
        g.setColorAt(0, c)
        g.setColorAt(1, QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
        p.fillPath(fill, QBrush(g))
        glow = QColor(self._color)
        glow.setAlpha(60)
        p.setPen(QPen(glow, self._lw + 3))
        p.drawPath(path)
        p.setPen(QPen(self._color, self._lw))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(pts[-1], 2.6, 2.6)
        p.end()


class AuroraBanner(QFrame):
    """Panel with slow drifting colour glows behind its content."""

    def __init__(self, min_height: int = 118):
        super().__init__()
        self.setMinimumHeight(min_height)
        self._ph = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(45)

    def _tick(self):
        self._ph += 0.011
        if self.isVisible():
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(r, Theme.RADIUS, Theme.RADIUS)
        p.setClipPath(clip)
        base = QLinearGradient(0, 0, r.width(), r.height())
        base.setColorAt(0, QColor("#121C42"))
        base.setColorAt(1, QColor("#090E20"))
        p.fillRect(r, QBrush(base))
        for color, fx, off, alpha in ((Theme.ACCENT, 0.2, 0.0, 74), (Theme.BLUE, 0.6, 2.1, 70), (Theme.PURPLE, 0.88, 4.0, 62)):
            cx = r.width() * (fx + 0.11 * math.sin(self._ph + off))
            cy = r.height() * (0.5 + 0.42 * math.cos(self._ph * 0.8 + off))
            g = QRadialGradient(cx, cy, max(r.width() * 0.3, 120))
            c = QColor(color)
            c.setAlpha(alpha)
            g.setColorAt(0, c)
            c2 = QColor(color)
            c2.setAlpha(0)
            g.setColorAt(1, c2)
            p.fillRect(r, QBrush(g))
        p.setClipping(False)
        p.setPen(QPen(QColor(Theme.BORDER_HI), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, Theme.RADIUS, Theme.RADIUS)
        p.end()


class GlowCard(QFrame):
    """Card whose shadow animates into an accent glow on hover."""

    def __init__(self, object_name: str = "Card"):
        super().__init__()
        self.setObjectName(object_name)
        self._sh = soft_shadow(self, 18, dy=5)
        self._glow_anim = QVariantAnimation(self)
        self._glow_anim.setDuration(220)
        self._glow_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glow_anim.valueChanged.connect(self._apply_glow)
        self._glow = 0.0

    def _apply_glow(self, v):
        self._glow = float(v)
        self._sh.setBlurRadius(18 + 20 * self._glow)
        self._sh.setColor(QColor(int(25 * self._glow), int(230 * self._glow), int(195 * self._glow),
                                 int(140 - 60 * self._glow)))

    def _animate_glow(self, target: float):
        self._glow_anim.stop()
        self._glow_anim.setStartValue(self._glow)
        self._glow_anim.setEndValue(target)
        self._glow_anim.start()

    def enterEvent(self, e):  # noqa: N802
        self._animate_glow(1.0)

    def leaveEvent(self, e):  # noqa: N802
        self._animate_glow(0.0)


class StatCard(GlowCard):
    def __init__(self, label: str, accent: str = ""):
        super().__init__("Card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 11, 15, 11)
        lay.setSpacing(2)
        lab = QLabel(label.upper())
        lab.setObjectName("CardLabel")
        lay.addWidget(lab)
        self.value = CountUpLabel()
        if accent:
            self.value.setStyleSheet(f"color: {accent}; font-size: 24px; font-weight: 800;")
        lay.addWidget(self.value)
        self.hint = QLabel("")
        self.hint.setObjectName("CardHint")
        lay.addWidget(self.hint)


class BarList(QWidget):
    def __init__(self):
        super().__init__()
        self._rows: list[tuple[str, int]] = []
        self._grow = 0.0
        self._anim = None
        self.setMinimumHeight(30)

    def set_rows(self, rows: list[tuple[str, int]]):
        self._rows = rows[:6]
        self.setMinimumHeight(max(30, len(self._rows) * 24 + 4))
        a = QVariantAnimation(self)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(650)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(self._set_grow)
        a.start()
        self._anim = a

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        mx = max(c for _, c in self._rows) or 1
        colors = [Theme.ACCENT, Theme.BLUE, Theme.PURPLE, Theme.GOLD, Theme.PINK, Theme.BULL]
        p.setFont(make_font(8.5, QFont.Weight.DemiBold))
        for i, (name, count) in enumerate(self._rows):
            y = i * 24 + 3
            p.setPen(QColor(Theme.TEXT_MUTED))
            p.drawText(QRectF(0, y, 104, 18), Qt.AlignmentFlag.AlignVCenter, name[:16])
            bar_w = max(3.0, (w - 146) * count / mx * self._grow)
            g = QLinearGradient(108, 0, 108 + bar_w, 0)
            c1 = QColor(colors[i % len(colors)])
            c2 = QColor(c1)
            c2.setAlpha(100)
            g.setColorAt(0, c2)
            g.setColorAt(1, c1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(108, y + 4, bar_w, 10), 5, 5)
            p.setPen(QColor(Theme.TEXT))
            p.drawText(QRectF(108 + bar_w + 7, y, 36, 18), Qt.AlignmentFlag.AlignVCenter, str(count))
        p.end()


class TickerTape(QWidget):
    """Scrolling strip of real quotes. Pauses while the mouse is over it."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)
        self._items: list[tuple[str, str, float]] = []
        self._off = 0.0
        self._paused = False
        self._font = make_font(8.5, QFont.Weight.DemiBold)
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(33)

    def set_items(self, items: list[tuple[str, str, float]]):
        self._items = list(items)
        self.update()

    def item_count(self) -> int:
        return len(self._items)

    def enterEvent(self, e):  # noqa: N802
        self._paused = True

    def leaveEvent(self, e):  # noqa: N802
        self._paused = False

    def _tick(self):
        if self._items and self.isVisible() and not self._paused:
            self._off += 0.9
            self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        fm = p.fontMetrics()
        h = self.height()
        if not self._items:
            p.setPen(QColor(Theme.TEXT_FAINT))
            p.drawText(self.rect().adjusted(4, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, "Waiting for market data…")
            p.end()
            return
        segs = []
        total = 0.0
        for name, price, chg in self._items:
            a, b = f"{name}  ", f"{price}  "
            c = f"{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%"
            w = fm.horizontalAdvance(a) + fm.horizontalAdvance(b) + fm.horizontalAdvance(c) + 34
            segs.append((a, b, c, chg, w))
            total += w
        if total <= 0:
            p.end()
            return
        x = -(self._off % total)
        base = (h + fm.ascent() - fm.descent()) / 2
        while x < self.width():
            for a, b, c, chg, w in segs:
                if x + w > 0:
                    cx = x
                    p.setPen(QColor(Theme.TEXT_MUTED))
                    p.drawText(QPointF(cx, base), a)
                    cx += fm.horizontalAdvance(a)
                    p.setPen(QColor(Theme.TEXT))
                    p.drawText(QPointF(cx, base), b)
                    cx += fm.horizontalAdvance(b)
                    p.setPen(QColor(Theme.BULL if chg >= 0 else Theme.BEAR))
                    p.drawText(QPointF(cx, base), c)
                x += w
                if x > self.width():
                    break
        # soft fade at both edges
        for x0, x1 in ((0, 36), (self.width(), self.width() - 36)):
            g = QLinearGradient(x0, 0, x1, 0)
            g.setColorAt(0, QColor(7, 10, 26, 255))
            g.setColorAt(1, QColor(7, 10, 26, 0))
            p.fillRect(QRectF(min(x0, x1), 0, 36, h), QBrush(g))
        p.end()


class NumItem(QTableWidgetItem):
    def __init__(self, text: str, value: float):
        super().__init__(text)
        self.value = value

    def __lt__(self, other):
        try:
            return self.value < other.value
        except Exception:
            return super().__lt__(other)


def make_table(headers: list[str], row_height: int = 31) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(row_height)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    t.setShowGrid(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.horizontalHeader().setHighlightSections(False)
    return t


class SegmentedControl(QFrame):
    def __init__(self, options: list[str], initial: int = 0):
        super().__init__()
        self.setObjectName("Flat")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.buttons: list[QPushButton] = []
        self._callbacks: list = []
        for i, name in enumerate(options):
            b = QPushButton(name)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(i == initial)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.set_index(idx))
            lay.addWidget(b)
            self.buttons.append(b)
        self._index = initial

    def on_change(self, fn):
        self._callbacks.append(fn)

    def index(self) -> int:
        return self._index

    def set_index(self, idx: int):
        idx = max(0, min(idx, len(self.buttons) - 1))
        self._index = idx
        for i, b in enumerate(self.buttons):
            b.setChecked(i == idx)
        for fn in self._callbacks:
            fn(idx)
