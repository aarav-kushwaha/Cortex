"""Cortex native chart engine.

Interactive price chart widget: 10 chart types, 30 indicators, 17 drawing
tools, replay, compare overlay, log / percent / inverted scales.

Navigation (works like a trading terminal):
  drag the plot            pan in time; drag clearly up/down to move price too
  mouse wheel              zoom time around the cursor
  wheel over price axis    stretch / squeeze the price scale (also Ctrl+wheel)
  drag the price axis      stretch the price scale
  drag the time axis       stretch the time scale
  double-click             reset view (on price axis: back to auto scale)
  click a drawing          select it; drag to move, drag a handle to reshape
  Delete / right-click     remove the selected drawing

Everything static is rendered once into a cached pixmap; moving the mouse only
repaints the crosshair layer, so the chart stays light on the CPU.
"""

from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QInputDialog, QMenu, QSizePolicy, QWidget

import cortex_indicators as ind
from cortex_indicators import Candle
from cortex_theme import ICON_LABELS, Theme, make_font

CHART_TYPES = ["Candles", "Hollow", "Heikin Ashi", "Bars", "HLC", "Line", "Step", "Area", "Baseline", "Renko"]
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "1D", "1W", "1M"]

MA_SPECS = {  # key -> (kind, period, colour)
    "ma_sma20": ("sma", 20, Theme.ACCENT_2), "ma_sma50": ("sma", 50, Theme.PINK),
    "ma_sma200": ("sma", 200, "#8FB4E0"), "ma_ema9": ("ema", 9, Theme.GOLD),
    "ma_ema21": ("ema", 21, Theme.ORANGE), "ma_ema50": ("ema", 50, Theme.WARN),
    "ma_ema200": ("ema", 200, Theme.BLUE), "ma_wma20": ("wma", 20, "#67E8A0"),
    "ma_hma21": ("hma", 21, "#C9A0FF"),
}

INDICATOR_CATALOG = [  # key, label, pane, group
    ("ma_sma20", "SMA 20", "main", "Moving averages"),
    ("ma_sma50", "SMA 50", "main", "Moving averages"),
    ("ma_sma200", "SMA 200", "main", "Moving averages"),
    ("ma_ema9", "EMA 9", "main", "Moving averages"),
    ("ma_ema21", "EMA 21", "main", "Moving averages"),
    ("ma_ema50", "EMA 50", "main", "Moving averages"),
    ("ma_ema200", "EMA 200", "main", "Moving averages"),
    ("ma_wma20", "WMA 20", "main", "Moving averages"),
    ("ma_hma21", "HMA 21", "main", "Moving averages"),
    ("boll", "Bollinger Bands", "main", "Bands & channels"),
    ("keltner", "Keltner Channel", "main", "Bands & channels"),
    ("donchian", "Donchian Channel", "main", "Bands & channels"),
    ("vwap", "VWAP", "main", "Bands & channels"),
    ("supertrend", "Supertrend", "main", "Trend"),
    ("psar", "Parabolic SAR", "main", "Trend"),
    ("ichimoku", "Ichimoku Cloud", "main", "Trend"),
    ("pivots", "Pivot Points", "main", "Trend"),
    ("volume", "Volume", "main", "Volume"),
    ("rsi", "RSI 14", "sub", "Momentum"),
    ("macd", "MACD", "sub", "Momentum"),
    ("stoch", "Stochastic", "sub", "Momentum"),
    ("stochrsi", "Stoch RSI", "sub", "Momentum"),
    ("cci", "CCI 20", "sub", "Momentum"),
    ("willr", "Williams %R", "sub", "Momentum"),
    ("roc", "ROC 12", "sub", "Momentum"),
    ("adx", "ADX 14", "sub", "Trend"),
    ("atr", "ATR 14", "sub", "Volatility"),
    ("obv", "OBV", "sub", "Volume"),
    ("mfi", "MFI 14", "sub", "Volume"),
    ("cmf", "CMF 20", "sub", "Volume"),
]
INDICATOR_LABELS = {k: l for k, l, _, _ in INDICATOR_CATALOG}
DEFAULT_INDICATORS = ("ma_sma20", "ma_ema50", "volume")
SUB_KEYS = [k for k, _, pane, _ in INDICATOR_CATALOG if pane == "sub"]

DRAW_TOOLS = [  # key, label, points needed
    ("cursor", "Cursor / select", 0), ("trend", "Trend line", 2), ("ray", "Ray", 2),
    ("extended", "Extended line", 2), ("hline", "Horizontal line", 1),
    ("hray", "Horizontal ray", 1), ("vline", "Vertical line", 1),
    ("channel", "Parallel channel", 3), ("rect", "Rectangle", 2),
    ("ellipse", "Ellipse", 2), ("fib", "Fib retracement", 2),
    ("fibext", "Fib extension", 2), ("measure", "Measure", 2),
    ("arrow", "Arrow", 2), ("text", "Text note", 1),
    ("long", "Long position", 2), ("short", "Short position", 2),
]
TOOL_POINTS = {k: n for k, _, n in DRAW_TOOLS}
TOOL_LABELS = {k: l for k, l, _ in DRAW_TOOLS}
FIB_LEVELS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
FIB_EXT_LEVELS = (0.0, 0.618, 1.0, 1.272, 1.618, 2.0, 2.618)

AXIS_W, TIME_H, TOP_PAD = 72, 24, 6
RIGHT_GAP = 6          # empty bars kept to the right of the last candle
HIT_TOL = 7.0


def default_settings() -> dict:
    return {"log": False, "percent": False, "invert": False, "grid": "both",
            "magnet": False, "last_price": True, "legend": True, "watermark": True,
            "keep_tool": False}


def _seg_dist(px, py, ax, ay, bx, by, extend_a=False, extend_b=False) -> float:
    dx, dy = bx - ax, by - ay
    ln2 = dx * dx + dy * dy
    if ln2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / ln2
    if not extend_a:
        t = max(0.0, t)
    if not extend_b:
        t = min(1.0, t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


class CandleChart(QWidget):
    toolChanged = Signal(str)        # emitted when the chart switches tool itself
    selectionChanged = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(280)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._candles: list[Candle] = []
        self._labels: list[str] = []
        self._disp: list[Candle] = []
        self._disp_labels: list[str] = []
        self.symbol, self.timeframe, self.display_name = "", "1D", ""
        self._state, self._message = "empty", "Pick a stock from your watchlist or search to load its chart"
        self.chart_type = "Candles"
        self.settings = default_settings()
        self.ind = {k: (k in DEFAULT_INDICATORS) for k, _, _, _ in INDICATOR_CATALOG}

        # view: [right - bars, right] in bar-index space; price range auto or manual
        self._bars, self._right = 140.0, 0.0
        self._y_auto, self._y_hi, self._y_lo = True, 1.0, 0.0
        self._mouse = QPoint(-1, -1)
        self._drag: Optional[dict] = None
        self._vel = 0.0
        self._vel_samples: list[tuple[float, float]] = []
        self._cache: dict = {}
        self._geo: Optional[dict] = None
        self._pm: Optional[QPixmap] = None
        self._bg_pm: Optional[QPixmap] = None
        self._dirty = True
        self._stride = 1

        self.tool = "cursor"
        self._shapes: list[dict] = []
        self._redo: list[dict] = []
        self._draft: Optional[dict] = None
        self._sel: Optional[int] = None
        self._hover_shape: Optional[int] = None

        self._compare: Optional[tuple[str, list[float]]] = None
        self._replay_at: Optional[int] = None
        self._replay_timer = QTimer(self)
        self._replay_timer.timeout.connect(self._replay_tick)

        self._reveal, self._pulse = 1.0, 0.0
        self._f_axis = make_font(8.5, QFont.Weight.DemiBold)
        self._f_small = make_font(8, QFont.Weight.Medium)
        self._f_legend = make_font(9, QFont.Weight.DemiBold)
        self._f_title = make_font(11.5, QFont.Weight.ExtraBold)
        self._f_ui = make_font(10, QFont.Weight.Medium)
        self._f_water = make_font(54, QFont.Weight.Black, spacing=4)
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(60)
        self._kinetic = QTimer(self)
        self._kinetic.timeout.connect(self._kinetic_tick)

    # ------------------------------------------------------------------ data
    def set_loading(self, symbol: str, tf: str):
        self.symbol, self.timeframe = symbol, tf
        self._state = "loading"
        self._message = f"Loading {symbol.split('.')[0]} · {tf}"
        self._invalidate()

    def set_error(self, message: str):
        self._state, self._message = "error", message
        self._invalidate()

    def set_candles(self, symbol: str, tf: str, candles: list[Candle], labels: list[str], name: str = ""):
        keep_shapes = symbol == self.symbol and tf == self.timeframe
        self.symbol, self.timeframe, self.display_name = symbol, tf, name
        self._candles, self._labels = list(candles), list(labels)
        self._compare = None
        self._replay_at = None
        self._replay_timer.stop()
        if not keep_shapes:
            self._shapes, self._redo = [], []
        self._draft, self._sel, self._hover_shape = None, None, None
        self._state = "ready" if self._candles else "error"
        if not self._candles:
            self._message = "No data"
        self._rebuild()
        self.fit(animate=False)
        self._reveal = 0.0
        self.selectionChanged.emit()
        self._invalidate()

    def set_compare(self, symbol: str, closes: list[float]):
        self._compare = (symbol, [float(x) for x in closes]) if closes else None
        self._invalidate()

    def clear_compare(self):
        self._compare = None
        self._invalidate()

    def has_data(self) -> bool:
        return self._state == "ready" and bool(self._disp)

    def last_candle(self) -> Optional[Candle]:
        return self._candles[-1] if self._candles else None

    def _rebuild(self):
        gap = self._right - len(self._disp)
        bars = self._candles[:self._replay_at] if self._replay_at is not None else self._candles
        labels = self._labels[:len(bars)]
        if self.chart_type == "Heikin Ashi":
            self._disp, self._disp_labels = ind.to_heikin(bars), labels
        elif self.chart_type == "Renko":
            self._disp, self._disp_labels = ind.to_renko(bars, labels)
        else:
            self._disp, self._disp_labels = list(bars), list(labels)
        self._cache = {}
        self._right = len(self._disp) + gap
        self._clamp_view()

    def _invalidate(self):
        self._dirty = True
        self.update()

    # ------------------------------------------------------------- settings
    def set_type(self, t: str):
        if t in CHART_TYPES and t != self.chart_type:
            was_renko = "Renko" in (t, self.chart_type)
            self.chart_type = t
            self._rebuild()
            if was_renko:
                self.fit(animate=False)
            self._invalidate()

    def set_indicator(self, key: str, on: bool):
        if key in self.ind:
            self.ind[key] = bool(on)
            self._invalidate()

    def active_indicators(self) -> list[str]:
        return [k for k, v in self.ind.items() if v]

    def set_setting(self, key: str, value):
        self.settings[key] = value
        if key == "log" and value and not self._y_auto:
            self._y_lo = max(self._y_lo, 1e-9)
        self._invalidate()

    def toggle_setting(self, key: str) -> bool:
        self.set_setting(key, not self.settings.get(key, False))
        return self.settings[key]

    # ---------------------------------------------------------------- view
    def _clamp_view(self):
        n = len(self._disp)
        self._bars = max(8.0, min(max(60.0, n * 1.6 + 20), self._bars))
        self._right = max(min(6.0, float(n)), min(n - 6.0 + self._bars, self._right))

    def zoom(self, direction: int, anchor: Optional[float] = None):
        """direction < 0 zooms in. ``anchor`` is a plot x pixel (default: right edge)."""
        if not self._disp:
            return
        factor = 0.84 if direction < 0 else 1 / 0.84
        plot_w = max(1.0, self.width() - AXIS_W)
        left = self._right - self._bars
        ax = plot_w if anchor is None else max(0.0, min(plot_w, anchor))
        idx = left + ax / plot_w * self._bars
        self._bars *= factor
        self._clamp_view()
        self._right = idx - ax / plot_w * self._bars + self._bars
        self._clamp_view()
        self._invalidate()

    def zoom_price(self, direction: int, anchor_y: Optional[float] = None):
        g = self._geo
        if not g:
            return
        factor = 0.86 if direction < 0 else 1 / 0.86
        hi, lo = g["hi"], g["lo"]
        t_hi, t_lo = self._t(hi), self._t(lo)
        if anchor_y is None:
            a = (t_hi + t_lo) / 2
        else:
            a = self._t(self._unscale_y(anchor_y, hi, lo, g["top"], g["price_h"]))
        self._set_manual(self._ti(a + (t_hi - a) * factor), self._ti(a + (t_lo - a) * factor))

    def pan(self, bars: float):
        if self._disp:
            self._right += bars
            self._clamp_view()
            self._invalidate()

    def fit(self, animate: bool = True):
        n = len(self._disp)
        self._bars = float(max(20, min(140, n + RIGHT_GAP)))
        self._right = float(n + RIGHT_GAP)
        self._y_auto = True
        self._clamp_view()
        self._invalidate()

    def fit_all(self):
        n = len(self._disp)
        self._bars = float(max(20, n + RIGHT_GAP + 2))
        self._right = float(n + RIGHT_GAP)
        self._y_auto = True
        self._clamp_view()
        self._invalidate()

    def auto_scale(self) -> bool:
        return self._y_auto

    def set_auto_scale(self, on: bool):
        if not on and self._geo:
            self._y_hi, self._y_lo = self._geo["hi"], self._geo["lo"]
        self._y_auto = bool(on)
        self._invalidate()

    def _set_manual(self, hi: float, lo: float):
        if hi < lo:
            hi, lo = lo, hi
        if hi - lo < 1e-9:
            return
        if self.settings.get("log"):
            lo = max(lo, 1e-9)
        self._y_hi, self._y_lo, self._y_auto = hi, lo, False
        self._invalidate()

    # log-aware transform so stretching feels the same on a log scale
    def _t(self, price: float) -> float:
        return math.log(max(price, 1e-9)) if self.settings.get("log") else price

    def _ti(self, value: float) -> float:
        return math.exp(value) if self.settings.get("log") else value

    # -------------------------------------------------------------- replay
    def replay_active(self) -> bool:
        return self._replay_at is not None

    def replay_playing(self) -> bool:
        return self._replay_timer.isActive()

    def start_replay(self, at: Optional[int] = None):
        if not self._candles:
            return
        self._replay_at = at if at is not None else max(30, int(len(self._candles) * 0.6))
        self._rebuild()
        self.fit(animate=False)

    def stop_replay(self):
        self._replay_timer.stop()
        self._replay_at = None
        self._rebuild()
        self.fit(animate=False)

    def replay_play(self, on: bool, speed_ms: int = 300):
        if on and self._replay_at is not None:
            self._replay_timer.start(speed_ms)
        else:
            self._replay_timer.stop()

    def replay_step(self, n: int = 1):
        if self._replay_at is None:
            return
        self._replay_at = max(5, min(len(self._candles), self._replay_at + n))
        self._rebuild()
        self._invalidate()

    def _replay_tick(self):
        if self._replay_at is None or self._replay_at >= len(self._candles):
            self._replay_timer.stop()
            return
        self.replay_step(1)

    def replay_progress(self) -> tuple[int, int]:
        return (self._replay_at or len(self._candles)), len(self._candles)

    # ------------------------------------------------------------ drawings
    def set_tool(self, tool: str):
        self.tool = tool
        self._draft = None
        if tool != "cursor":
            self._select(None)
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "cursor" else Qt.CursorShape.CrossCursor)
        self._invalidate()

    def _select(self, index: Optional[int]):
        if index != self._sel:
            self._sel = index
            self.selectionChanged.emit()
            self._invalidate()

    def has_selection(self) -> bool:
        return self._sel is not None and 0 <= self._sel < len(self._shapes)

    def selected_label(self) -> str:
        return TOOL_LABELS.get(self._shapes[self._sel]["type"], "Drawing") if self.has_selection() else ""

    def delete_selected(self) -> bool:
        if not self.has_selection():
            return False
        self._redo.append(self._shapes.pop(self._sel))
        self._hover_shape = None
        self._select(None)
        self._invalidate()
        return True

    def clear_drawings(self):
        self._shapes, self._draft, self._hover_shape = [], None, None
        self._select(None)
        self._invalidate()

    def undo_drawing(self):
        if self._shapes:
            self._redo.append(self._shapes.pop())
            self._hover_shape = None
            self._select(None)
            self._invalidate()

    def redo_drawing(self):
        if self._redo:
            self._shapes.append(self._redo.pop())
            self._invalidate()

    def drawing_count(self) -> int:
        return len(self._shapes)

    # ------------------------------------------------------------ computing
    def _calc(self, key: str):
        if key in self._cache:
            return self._cache[key]
        cs = self._disp
        closes = [c.c for c in cs]
        if key in MA_SPECS:
            kind, period, _ = MA_SPECS[key]
            val = ind._ma(closes, period, kind)
        elif key == "boll":
            val = ind.bollinger(closes)
        elif key == "keltner":
            val = ind.keltner(cs)
        elif key == "donchian":
            val = ind.donchian(cs)
        elif key == "vwap":
            val = ind.vwap(cs)
        elif key == "supertrend":
            val = ind.supertrend(cs)
        elif key == "psar":
            val = ind.psar(cs)
        elif key == "ichimoku":
            val = ind.ichimoku(cs)
        elif key == "pivots":
            val = ind.pivots(cs)
        elif key == "rsi":
            val = ind.rsi(closes)
        elif key == "macd":
            val = ind.macd(closes)
        elif key == "stoch":
            val = ind.stochastic(cs)
        elif key == "stochrsi":
            val = ind.stoch_rsi(closes)
        elif key == "cci":
            val = ind.cci(cs)
        elif key == "willr":
            val = ind.williams_r(cs)
        elif key == "roc":
            val = ind.roc(closes)
        elif key == "adx":
            val = ind.adx(cs)
        elif key == "atr":
            val = ind.atr(cs)
        elif key == "obv":
            val = ind.obv(cs)
        elif key == "mfi":
            val = ind.mfi(cs)
        elif key == "cmf":
            val = ind.cmf(cs)
        else:
            val = None
        self._cache[key] = val
        return val

    # ------------------------------------------------------------- geometry
    def _scale_y(self, price: float, hi: float, lo: float, top: float, h: float) -> float:
        if self.settings.get("log") and price > 0 and hi > 0 and lo > 0:
            lh, ll = math.log(hi), math.log(lo)
            f = (lh - math.log(max(price, 1e-9))) / ((lh - ll) or 1)
        else:
            f = (hi - price) / ((hi - lo) or 1)
        if self.settings.get("invert"):
            f = 1 - f
        return top + f * h

    def _unscale_y(self, y: float, hi: float, lo: float, top: float, h: float) -> float:
        f = (y - top) / (h or 1)
        if self.settings.get("invert"):
            f = 1 - f
        if self.settings.get("log") and hi > 0 and lo > 0:
            lh, ll = math.log(hi), math.log(lo)
            return math.exp(lh - f * (lh - ll))
        return hi - f * (hi - lo)

    def _px(self, idx: float) -> float:
        g = self._geo
        return (idx - g["left"]) * g["cw"] + g["cw"] / 2

    def _py(self, price: float) -> float:
        g = self._geo
        return self._scale_y(price, g["hi"], g["lo"], g["top"], g["price_h"])

    def _xy_to_data(self, x: float, y: float, magnet: bool = True):
        g = self._geo
        if not g:
            return None
        idx = g["left"] + x / g["cw"] - 0.5
        price = self._unscale_y(y, g["hi"], g["lo"], g["top"], g["price_h"])
        if magnet and self.settings.get("magnet"):
            i = int(round(idx))
            if 0 <= i < len(self._disp):
                c = self._disp[i]
                price = min((c.o, c.h, c.l, c.c), key=lambda v: abs(self._py(v) - y))
                idx = float(i)
        return idx, price

    def _axis_text(self, price: float, base: float) -> str:
        if self.settings.get("percent") and base:
            return f"{(price / base - 1) * 100:+.2f}%"
        if abs(price) >= 100000:
            return f"{price:,.0f}"
        return f"{price:,.2f}"

    # ----------------------------------------------------------- hit testing
    def _shape_points(self, sh: dict) -> list[tuple[float, float]]:
        return [(self._px(pt[0]), self._py(pt[1])) for pt in sh["pts"]]

    def _shape_hit(self, sh: dict, x: float, y: float) -> bool:
        g = self._geo
        if not g:
            return False
        t = sh["type"]
        P = self._shape_points(sh)
        tol = HIT_TOL
        try:
            if t == "hline":
                return abs(y - P[0][1]) <= tol
            if t == "hray":
                return abs(y - P[0][1]) <= tol and x >= P[0][0] - tol
            if t == "vline":
                return abs(x - P[0][0]) <= tol
            if t == "text":
                w = QFontMetrics(self._f_ui).horizontalAdvance(sh.get("text", "")) + 8
                return P[0][0] - 4 <= x <= P[0][0] + w and P[0][1] - 18 <= y <= P[0][1] + 6
            if t in ("trend", "arrow", "measure"):
                return _seg_dist(x, y, *P[0], *P[1]) <= tol
            if t == "ray":
                return _seg_dist(x, y, *P[0], *P[1], extend_b=True) <= tol
            if t == "extended":
                return _seg_dist(x, y, *P[0], *P[1], extend_a=True, extend_b=True) <= tol
            if t == "rect":
                x1, x2 = sorted((P[0][0], P[1][0]))
                y1, y2 = sorted((P[0][1], P[1][1]))
                outer = x1 - tol <= x <= x2 + tol and y1 - tol <= y <= y2 + tol
                inner = x1 + tol < x < x2 - tol and y1 + tol < y < y2 - tol
                return outer and not inner
            if t == "ellipse":
                cx, cy = (P[0][0] + P[1][0]) / 2, (P[0][1] + P[1][1]) / 2
                rx, ry = abs(P[1][0] - P[0][0]) / 2, abs(P[1][1] - P[0][1]) / 2
                if rx < 1 or ry < 1:
                    return False
                d = math.hypot((x - cx) / rx, (y - cy) / ry)
                return abs(d - 1.0) * min(rx, ry) <= tol
            if t == "channel" and len(sh["pts"]) >= 3:
                a, b, c = sh["pts"][:3]
                off = c[1] - (a[1] + (b[1] - a[1]) * ((c[0] - a[0]) / ((b[0] - a[0]) or 1e-9)))
                q0 = (self._px(a[0]), self._py(a[1] + off))
                q1 = (self._px(b[0]), self._py(b[1] + off))
                return min(_seg_dist(x, y, *P[0], *P[1]), _seg_dist(x, y, *q0, *q1)) <= tol
            if t in ("fib", "fibext"):
                x1 = min(P[0][0], P[1][0])
                if x < x1 - tol:
                    return False
                p0, p1 = sh["pts"][0][1], sh["pts"][1][1]
                levels = FIB_LEVELS if t == "fib" else FIB_EXT_LEVELS
                for lv in levels:
                    price = p1 + (p0 - p1) * lv if t == "fib" else p0 + (p1 - p0) * lv
                    if abs(y - self._py(price)) <= tol:
                        return True
                return False
            if t in ("long", "short"):
                entry, target = sh["pts"][0][1], sh["pts"][1][1]
                stop = entry - (target - entry) * 0.5
                x1, x2 = sorted((P[0][0], P[1][0]))
                x2 = max(x2, x1 + 20)
                ys = sorted((self._py(target), self._py(stop)))
                return x1 - tol <= x <= x2 + tol and ys[0] - tol <= y <= ys[1] + tol
        except Exception:
            return False
        return False

    def _shape_at(self, x: float, y: float) -> Optional[int]:
        for i in range(len(self._shapes) - 1, -1, -1):
            if self._shape_hit(self._shapes[i], x, y):
                return i
        return None

    def _handle_at(self, x: float, y: float) -> Optional[int]:
        if not self.has_selection() or not self._geo:
            return None
        for i, (hx, hy) in enumerate(self._shape_points(self._shapes[self._sel])):
            if abs(x - hx) <= 8 and abs(y - hy) <= 8:
                return i
        return None

    # ---------------------------------------------------------- interaction
    def _tick(self):
        if not self.isVisible():
            return
        self._pulse += 0.14
        if self._reveal < 1.0:
            self._reveal = min(1.0, self._reveal + 0.07)
            self._invalidate()
            return
        if self._state == "loading":
            self._invalidate()
        elif self._state == "ready":
            self.update()

    def _kinetic_tick(self):
        if abs(self._vel) < 0.03 or self._drag is not None:
            self._kinetic.stop()
            return
        self._right -= self._vel
        self._vel *= 0.90
        before = self._right
        self._clamp_view()
        if before != self._right:
            self._vel = 0.0
        self._invalidate()

    def _zone(self, x: float, y: float) -> str:
        w, h = self.width(), self.height()
        if x >= w - AXIS_W and y >= h - TIME_H:
            return "corner"
        if x >= w - AXIS_W:
            return "yaxis"
        if y >= h - TIME_H:
            return "xaxis"
        return "plot"

    def wheelEvent(self, e):  # noqa: N802
        if not self.has_data():
            return
        self._kinetic.stop()
        pos = e.position()
        up = e.angleDelta().y() > 0
        mods = e.modifiers() if hasattr(e, "modifiers") else Qt.KeyboardModifier.NoModifier
        zone = self._zone(pos.x(), pos.y())
        if zone == "yaxis" or (mods & Qt.KeyboardModifier.ControlModifier):
            self.zoom_price(-1 if up else 1, pos.y() if zone != "yaxis" else None)
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.pan(-self._bars * 0.1 if up else self._bars * 0.1)
        else:
            self.zoom(-1 if up else 1, pos.x())

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self._draft is not None:
                self._draft = None
            elif self.tool != "cursor":
                self.set_tool("cursor")
                self.toolChanged.emit("cursor")
            else:
                self._select(None)
            self._invalidate()
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if not self.delete_selected():
                self.undo_drawing()
        elif k == Qt.Key.Key_Left:
            self.pan(-max(1.0, self._bars * 0.05))
        elif k == Qt.Key.Key_Right:
            self.pan(max(1.0, self._bars * 0.05))
        elif k == Qt.Key.Key_Up:
            self.zoom_price(-1)
        elif k == Qt.Key.Key_Down:
            self.zoom_price(1)
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom(-1)
        elif k == Qt.Key.Key_Minus:
            self.zoom(1)
        elif k == Qt.Key.Key_Home:
            self.fit()
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if not self.has_data():
            return
        pos = e.position()
        if self._zone(pos.x(), pos.y()) in ("yaxis", "corner"):
            self.set_auto_scale(True)
        elif self.tool == "cursor" and self._shape_at(pos.x(), pos.y()) is None:
            self.fit()

    def mousePressEvent(self, e):  # noqa: N802
        self.setFocus()
        if not self.has_data() or not self._geo:
            return
        if hasattr(e, "button") and e.button() == Qt.MouseButton.RightButton:
            return
        self._kinetic.stop()
        pos = e.position()
        x, y = pos.x(), pos.y()
        zone = self._zone(x, y)
        g = self._geo
        if zone == "corner":
            self.set_auto_scale(not self._y_auto)
            return
        if zone == "yaxis":
            self._drag = {"mode": "yaxis", "y0": y, "hi": g["hi"], "lo": g["lo"]}
            return
        if zone == "xaxis":
            self._drag = {"mode": "xaxis", "x0": x, "bars": self._bars}
            return
        if self.tool != "cursor":
            self._press_draw(x, y)
            return
        handle = self._handle_at(x, y)
        if handle is not None:
            self._drag = {"mode": "point", "pt": handle}
            return
        hit = self._shape_at(x, y)
        if hit is not None:
            self._select(hit)
            self._drag = {"mode": "shape", "x": x, "y": y}
            return
        self._select(None)
        self._vel_samples = [(time.monotonic(), x)]
        self._drag = {"mode": "pan", "x0": x, "y0": y, "right": self._right,
                      "hi": g["hi"], "lo": g["lo"], "y_on": not self._y_auto}

    def _press_draw(self, x: float, y: float):
        d = self._xy_to_data(x, y)
        if d is None:
            return
        need = TOOL_POINTS.get(self.tool, 2)
        if self._draft is not None and self._draft.get("phase") == 2:
            self._draft["pts"][2] = d
            self._commit_draft()
            return
        if need == 1:
            shape = {"type": self.tool, "pts": [d]}
            if self.tool == "text":
                text, ok = QInputDialog.getText(self, "Text note", "Note:")
                if not ok or not text.strip():
                    return
                shape["text"] = text.strip()
            self._add_shape(shape)
            return
        self._draft = {"type": self.tool, "pts": [d, d] + ([d] if need == 3 else []), "phase": 1}
        self._invalidate()

    def _add_shape(self, shape: dict):
        self._shapes.append(shape)
        self._redo.clear()
        if not self.settings.get("keep_tool"):
            self.tool = "cursor"
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.toolChanged.emit("cursor")
            self._sel = None
            self._select(len(self._shapes) - 1)
        self._invalidate()

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        x, y = pos.x(), pos.y()
        self._mouse = pos.toPoint()
        g = self._geo
        if self._draft is not None and g:
            d = self._xy_to_data(x, y)
            if d:
                self._draft["pts"][2 if self._draft.get("phase") == 2 else 1] = d
                self._invalidate()
            return
        drag = self._drag
        if drag and g:
            mode = drag["mode"]
            if mode == "pan":
                self._right = drag["right"] - (x - drag["x0"]) / g["cw"]
                self._clamp_view()
                dy, dx = y - drag["y0"], x - drag["x0"]
                if not drag["y_on"] and abs(dy) > 28 and abs(dy) > abs(dx) * 0.7:
                    drag["y_on"] = True
                    drag["y0"] = y
                    dy = 0.0
                if drag["y_on"]:
                    t_hi, t_lo = self._t(drag["hi"]), self._t(drag["lo"])
                    shift = dy / (g["price_h"] or 1) * (t_hi - t_lo)
                    if self.settings.get("invert"):
                        shift = -shift
                    self._y_hi, self._y_lo = self._ti(t_hi + shift), self._ti(t_lo + shift)
                    self._y_auto = False
                now = time.monotonic()
                self._vel_samples.append((now, x))
                self._vel_samples = [s for s in self._vel_samples if now - s[0] < 0.12]
                self._invalidate()
            elif mode == "yaxis":
                factor = math.exp((y - drag["y0"]) * 0.006)
                t_hi, t_lo = self._t(drag["hi"]), self._t(drag["lo"])
                mid, half = (t_hi + t_lo) / 2, (t_hi - t_lo) / 2 * factor
                self._set_manual(self._ti(mid + half), self._ti(mid - half))
            elif mode == "xaxis":
                right = self._right
                self._bars = drag["bars"] * math.exp(-(x - drag["x0"]) * 0.006)
                self._clamp_view()
                self._right = right
                self._clamp_view()
                self._invalidate()
            elif mode == "shape" and self.has_selection():
                dx, dy = x - drag["x"], y - drag["y"]
                sh = self._shapes[self._sel]
                new_pts = []
                for pt in sh["pts"]:
                    d = self._xy_to_data(self._px(pt[0]) + dx, self._py(pt[1]) + dy, magnet=False)
                    new_pts.append(d if d else pt)
                sh["pts"] = new_pts
                drag["x"], drag["y"] = x, y
                self._invalidate()
            elif mode == "point" and self.has_selection():
                d = self._xy_to_data(x, y)
                if d:
                    self._shapes[self._sel]["pts"][drag["pt"]] = d
                    self._invalidate()
            return
        # plain hover
        zone = self._zone(x, y)
        if zone == "yaxis":
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif zone == "xaxis":
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif zone == "corner":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self.tool != "cursor":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            hover = self._shape_at(x, y) if (g and self._state == "ready") else None
            if hover != self._hover_shape:
                self._hover_shape = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover is not None else Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._draft is not None:
            if self._draft.get("phase") == 1 and TOOL_POINTS.get(self._draft["type"], 2) == 3:
                self._draft["phase"] = 2
            elif self._draft.get("phase") == 1:
                self._commit_draft()
            return
        drag, self._drag = self._drag, None
        if drag and drag["mode"] == "pan" and self._geo and len(self._vel_samples) >= 2:
            (t0, x0), (t1, x1) = self._vel_samples[0], self._vel_samples[-1]
            if t1 - t0 > 0.01 and time.monotonic() - t1 < 0.06:
                self._vel = ((x1 - x0) / (t1 - t0)) / self._geo["cw"] * 0.016
                if abs(self._vel) > 0.08:
                    self._kinetic.start(16)

    def _commit_draft(self):
        d, self._draft = self._draft, None
        if d:
            a, b = d["pts"][0], d["pts"][1]
            if abs(a[0] - b[0]) > 0.01 or abs(a[1] - b[1]) > 1e-9:
                d.pop("phase", None)
                self._add_shape(d)
                return
        self._invalidate()

    def contextMenuEvent(self, e):  # noqa: N802
        if not self.has_data():
            return
        pos = e.pos()
        hit = self._shape_at(pos.x(), pos.y()) if self._geo else None
        menu = QMenu(self)
        if hit is not None:
            self._select(hit)
            menu.addAction(f"Delete {TOOL_LABELS.get(self._shapes[hit]['type'], 'drawing').lower()}",
                           self.delete_selected)
            menu.addSeparator()
        if self._shapes:
            menu.addAction(f"Delete all drawings ({len(self._shapes)})", self.clear_drawings)
        menu.addAction("Reset view", self.fit)
        menu.addAction("Fit all bars", self.fit_all)
        act = menu.addAction("Auto price scale")
        act.setCheckable(True)
        act.setChecked(self._y_auto)
        act.triggered.connect(lambda on: self.set_auto_scale(on))
        menu.exec(e.globalPos())

    def leaveEvent(self, e):  # noqa: N802
        self._mouse = QPoint(-1, -1)
        self._hover_shape = None
        self.update()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._invalidate()

    # -------------------------------------------------------------- painting
    def paintEvent(self, e):  # noqa: N802
        dpr = self.devicePixelRatioF()
        if (self._dirty or self._pm is None or self._pm.width() != int(self.width() * dpr)
                or self._pm.height() != int(self.height() * dpr)):
            self._render_static()
            self._dirty = False
        p = QPainter(self)
        if self._pm is not None:
            p.drawPixmap(0, 0, self._pm)
        if self._state == "ready" and self._geo:
            self._paint_dynamic(p)
        p.end()

    def _render_static(self):
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            if self._bg_pm is None or self._bg_pm.size() != pm.size():
                bg = QPixmap(pm.size())
                bg.setDevicePixelRatio(dpr)
                bp = QPainter(bg)
                self._paint_background(bp)
                bp.end()
                self._bg_pm = bg
            p.drawPixmap(0, 0, self._bg_pm)
            if self._state != "ready" or not self._disp:
                self._paint_state(p)
                self._geo = None
            else:
                self._paint_chart(p)
        finally:
            p.end()
        self._pm = pm

    def _paint_background(self, p: QPainter):
        W, H = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor("#0B1130"))
        g.setColorAt(1, QColor("#05070F"))
        p.fillRect(QRectF(0, 0, W, H), QBrush(g))
        for cx, cy, rad, col in ((W * 0.85, 0, W * 0.55, QColor(61, 169, 255, 26)),
                                 (W * 0.1, H, W * 0.5, QColor(25, 230, 195, 18))):
            rg = QRadialGradient(cx, cy, max(rad, 10))
            rg.setColorAt(0, col)
            rg.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.fillRect(QRectF(0, 0, W, H), QBrush(rg))

    def _paint_state(self, p: QPainter):
        W, H = self.width(), self.height()
        if self._state == "loading":
            cx, cy = W / 2, H / 2 - 14
            p.setPen(QPen(QColor(Theme.BORDER_HI), 3))
            p.drawEllipse(QPointF(cx, cy), 19, 19)
            pen = QPen(QColor(Theme.ACCENT), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(cx - 19, cy - 19, 38, 38), int(-self._pulse * 55 * 16) % (360 * 16), 110 * 16)
        p.setFont(self._f_ui)
        p.setPen(QColor(Theme.BEAR if self._state == "error" else Theme.TEXT_MUTED))
        p.drawText(QRectF(24, H / 2 + 16, W - 48, 80),
                   int(Qt.AlignmentFlag.AlignHCenter.value | Qt.TextFlag.TextWordWrap.value), self._message)

    def _paint_chart(self, p: QPainter):
        W, H = self.width(), self.height()
        top = TOP_PAD
        subs = [k for k in SUB_KEYS if self.ind.get(k)]
        plot_w = W - AXIS_W
        sub_h = 0
        if subs:
            sub_h = max(48, min(int(H * 0.17), (H - top - TIME_H - 130) // max(1, len(subs))))
            while subs and H - top - TIME_H - len(subs) * sub_h < 120:
                subs = subs[:-1]
        price_h = H - top - TIME_H - len(subs) * sub_h
        if price_h < 40 or plot_w < 60:
            self._geo = None
            return

        n = len(self._disp)
        left = self._right - self._bars
        cw = plot_w / self._bars
        i0 = max(0, int(math.floor(left)))
        i1 = max(i0, min(n, int(math.ceil(self._right)) + 1))
        view = self._disp[i0:i1]

        if self._y_auto:
            src = view or self._disp[-20:]
            hi, lo = max(c.h for c in src), min(c.l for c in src)
            for key in ("boll", "keltner", "donchian"):
                if self.ind.get(key) and view:
                    up, _mid, dn = self._calc(key)
                    su = [v for v in up[i0:i1] if v is not None]
                    sd = [v for v in dn[i0:i1] if v is not None]
                    if su:
                        hi = max(hi, max(su))
                    if sd:
                        lo = min(lo, min(sd))
            pad = (hi - lo) * 0.08 or max(abs(hi) * 0.01, 0.01)
            hi, lo = hi + pad, lo - pad * 1.6   # extra room below for volume
            if self.settings.get("log"):
                lo = max(lo, min(c.l for c in src) * 0.9, 1e-9)
        else:
            hi, lo = self._y_hi, self._y_lo
        if hi <= lo:
            hi = lo + 1
        base_price = (view[0].c if view else self._disp[-1].c) or 1.0

        self._geo = {"left": left, "cw": cw, "i0": i0, "i1": i1, "hi": hi, "lo": lo, "top": top,
                     "price_h": price_h, "plot_w": plot_w, "subs": subs, "sub_h": sub_h,
                     "base": base_price, "H": H, "W": W}
        # fast local mappers (the generic ones cost too much when thousands of points are drawn)
        half = cw / 2

        def px(i):
            return (i - left) * cw + half

        if self.settings.get("log") or self.settings.get("invert"):
            py = self._py
        else:
            k = price_h / (hi - lo)

            def py(v):
                return top + (hi - v) * k
        # when zoomed far out, plot every Nth point: more than ~1 point per 2px is invisible
        self._stride = max(1, int((i1 - i0) / max(100.0, plot_w / 2)))
        shown = max(1, int(math.ceil(len(view) * self._reveal))) if view else 0
        grid = self.settings.get("grid", "both")

        # watermark
        if self.settings.get("watermark"):
            p.setFont(self._f_water)
            p.setPen(QColor(255, 255, 255, 13))
            p.drawText(QRectF(0, top, plot_w, price_h), Qt.AlignmentFlag.AlignCenter,
                       self.symbol.split(".")[0].lstrip("^"))

        # price grid + axis
        p.setFont(self._f_axis)
        ticks = max(4, min(10, int(price_h / 62)))
        for i in range(ticks + 1):
            gy = top + price_h * i / ticks
            if grid in ("both", "horizontal"):
                p.setPen(QPen(QColor(Theme.GRID), 1))
                p.drawLine(QPointF(0, gy), QPointF(plot_w, gy))
            price = self._unscale_y(gy, hi, lo, top, price_h)
            p.setPen(QColor(Theme.TEXT_MUTED))
            p.drawText(QRectF(plot_w + 8, gy - 8, AXIS_W - 10, 16), Qt.AlignmentFlag.AlignVCenter,
                       self._axis_text(price, base_price))
        # time grid + axis
        step = max(1, int(math.ceil(118 / max(cw, 0.01))))
        first = (int(math.floor(left)) // step) * step
        for i in range(first, int(math.ceil(self._right)) + 1, step):
            x = px(i)
            if x < -2 or x > plot_w + 2:
                continue
            if grid in ("both", "vertical"):
                p.setPen(QPen(QColor(Theme.GRID), 1))
                p.drawLine(QPointF(x, top), QPointF(x, H - TIME_H))
            if 0 <= i < len(self._disp_labels) and 46 <= x <= plot_w - 40:
                p.setPen(QColor(Theme.TEXT_MUTED))
                p.drawText(QRectF(x - 52, H - TIME_H + 4, 104, 16), Qt.AlignmentFlag.AlignCenter, self._disp_labels[i])
        # axis borders
        p.setPen(QPen(QColor(Theme.BORDER), 1))
        p.drawLine(QPointF(plot_w, 0), QPointF(plot_w, H))
        p.drawLine(QPointF(0, H - TIME_H), QPointF(W, H - TIME_H))

        p.save()
        p.setClipRect(QRectF(0, 0, plot_w, top + price_h))
        if view:
            if self.ind.get("volume"):
                vmax = max((c.v for c in view), default=0) or 1
                vh = price_h * 0.17
                bw = max(1.0, cw * 0.62)
                up_c, dn_c = QColor(Theme.BULL), QColor(Theme.BEAR)
                up_c.setAlpha(62)
                dn_c.setAlpha(62)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                for i, c in enumerate(view[:shown]):
                    bh = c.v / vmax * vh
                    p.fillRect(QRectF(px(i0 + i) - bw / 2, top + price_h - bh, bw, bh),
                               up_c if c.c >= c.o else dn_c)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._draw_overlays(p, i0, i1, shown, px, py, cw, plot_w)
            self._draw_price(p, view, shown, i0, px, py, cw, top, price_h)
            self._draw_compare(p, i0, i1, px, py)
        self._draw_shapes(p, px, py, plot_w, top, price_h)
        p.restore()

        # last price line + tag
        if self.settings.get("last_price") and self._disp:
            last = self._disp[-1]
            ly = py(last.c)
            if top <= ly <= top + price_h:
                col = QColor(Theme.BULL if last.c >= last.o else Theme.BEAR)
                p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(0, ly), QPointF(plot_w, ly))
                tag = QRectF(plot_w + 1, ly - 10, AXIS_W - 3, 20)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(tag, 4, 4)
                p.setPen(QColor("#04120B" if last.c >= last.o else "#1B0509"))
                p.setFont(make_font(8.5, QFont.Weight.Bold))
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, self._axis_text(last.c, base_price))

        y0 = top + price_h
        for key in subs:
            self._draw_sub(p, key, y0, sub_h, plot_w, cw, i0, i1, shown, px)
            y0 += sub_h

        # corner: auto-scale toggle
        corner = QRectF(plot_w + 6, H - TIME_H + 4, AXIS_W - 12, TIME_H - 8)
        p.setPen(QPen(QColor(Theme.ACCENT if self._y_auto else Theme.BORDER_HI), 1))
        p.setBrush(QColor(25, 230, 195, 40) if self._y_auto else QColor(Theme.SURFACE_2))
        p.drawRoundedRect(corner, 4, 4)
        p.setFont(make_font(7.5, QFont.Weight.Bold, spacing=1))
        p.setPen(QColor(Theme.ACCENT if self._y_auto else Theme.TEXT_FAINT))
        p.drawText(corner, Qt.AlignmentFlag.AlignCenter, "AUTO")

        if self._replay_at is not None:
            p.setFont(make_font(8.5, QFont.Weight.Bold, spacing=1))
            p.setPen(QColor(Theme.WARN))
            p.drawText(QRectF(plot_w - 190, top + 6, 182, 16), Qt.AlignmentFlag.AlignRight,
                       f"REPLAY  {self._replay_at} / {len(self._candles)}")

    def _draw_overlays(self, p, i0, i1, shown, px, py, cw, plot_w):
        st = self._stride

        def thin(series):
            seg = series[i0:i1][:shown]
            return [QPointF(px(i0 + i), py(seg[i])) for i in range(0, len(seg), st) if seg[i] is not None]

        def polyline(series, color, width=1.4, style=Qt.PenStyle.SolidLine, glow=False):
            pts = thin(series)
            if len(pts) > 1:
                poly = QPolygonF(pts)
                if glow and len(pts) <= 400:
                    gc = QColor(color)
                    gc.setAlpha(46)
                    p.setPen(QPen(gc, width + 3.2))
                    p.drawPolyline(poly)
                p.setPen(QPen(QColor(color), width, style))
                p.drawPolyline(poly)

        def band(up, mid, lo_s, color, fill_alpha=26):
            u, d = thin(up), thin(lo_s)
            if len(u) > 1 and len(d) > 1:
                path = QPainterPath()
                path.moveTo(u[0])
                for pt in u[1:]:
                    path.lineTo(pt)
                for pt in reversed(d):
                    path.lineTo(pt)
                path.closeSubpath()
                c = QColor(color)
                c.setAlpha(fill_alpha)
                p.fillPath(path, QBrush(c))
            polyline(up, color, 1.1)
            polyline(lo_s, color, 1.1)
            if mid is not None:
                polyline(mid, Theme.TEXT_FAINT, 1.0, Qt.PenStyle.DashLine)

        if self.ind.get("boll"):
            band(*self._calc("boll"), Theme.PURPLE)
        if self.ind.get("keltner"):
            up, _mid, lo_s = self._calc("keltner")
            band(up, None, lo_s, Theme.BLUE, 20)
        if self.ind.get("donchian"):
            band(*self._calc("donchian"), Theme.ORANGE, 16)
        if self.ind.get("vwap"):
            polyline(self._calc("vwap"), "#FFFFFF", 1.4)
        for key in MA_SPECS:
            if self.ind.get(key):
                polyline(self._calc(key), MA_SPECS[key][2], 1.5, glow=True)
        if self.ind.get("supertrend"):
            line, direction = self._calc("supertrend")
            seg, seg_dir = [], None
            for i in range(i0, min(i1, i0 + shown)):
                v = line[i]
                if v is None:
                    continue
                if seg_dir is not None and direction[i] != seg_dir and len(seg) > 1:
                    p.setPen(QPen(QColor(Theme.BULL if seg_dir == 1 else Theme.BEAR), 1.9))
                    p.drawPolyline(QPolygonF(seg))
                    seg = []
                seg_dir = direction[i]
                seg.append(QPointF(px(i), py(v)))
            if len(seg) > 1:
                p.setPen(QPen(QColor(Theme.BULL if seg_dir == 1 else Theme.BEAR), 1.9))
                p.drawPolyline(QPolygonF(seg))
        if self.ind.get("psar"):
            vals, rising = self._calc("psar")
            p.setPen(Qt.PenStyle.NoPen)
            r = max(1.2, min(2.8, cw * 0.15))
            for i in range(i0, min(i1, i0 + shown)):
                if vals[i] is None:
                    continue
                p.setBrush(QColor(Theme.BULL if rising[i] else Theme.BEAR))
                p.drawEllipse(QPointF(px(i), py(vals[i])), r, r)
            p.setBrush(Qt.BrushStyle.NoBrush)
        if self.ind.get("pivots"):
            pv = self._calc("pivots")
            for name, color in (("P", Theme.GOLD), ("R1", Theme.BEAR), ("S1", Theme.BULL),
                                ("R2", Theme.BEAR), ("S2", Theme.BULL)):
                polyline(pv[name], color, 1.0, Qt.PenStyle.DotLine)
        if self.ind.get("ichimoku"):
            self._draw_ichimoku(p, i0, i1, shown, px, py)

    def _draw_ichimoku(self, p, i0, i1, shown, px, py):
        tenkan, kijun, span_b, chikou, shift = self._calc("ichimoku")
        n = len(self._disp)
        a_pts, b_pts = [], []
        for i in range(i0, min(n + shift, int(math.ceil(self._right)) + 2)):
            j = i - shift
            if 0 <= j < n and tenkan[j] is not None and kijun[j] is not None and span_b[j] is not None:
                a_pts.append(QPointF(px(i), py((tenkan[j] + kijun[j]) / 2)))
                b_pts.append(QPointF(px(i), py(span_b[j])))
        if len(a_pts) > 1:
            path = QPainterPath()
            path.moveTo(a_pts[0])
            for pt in a_pts[1:]:
                path.lineTo(pt)
            for pt in reversed(b_pts):
                path.lineTo(pt)
            path.closeSubpath()
            c = QColor(Theme.BULL if a_pts[-1].y() < b_pts[-1].y() else Theme.BEAR)
            c.setAlpha(34)
            p.fillPath(path, QBrush(c))
            p.setPen(QPen(QColor(Theme.BULL), 1))
            p.drawPolyline(QPolygonF(a_pts))
            p.setPen(QPen(QColor(Theme.BEAR), 1))
            p.drawPolyline(QPolygonF(b_pts))
        for series, color, style in ((tenkan, Theme.ACCENT_2, Qt.PenStyle.SolidLine),
                                     (kijun, Theme.PINK, Qt.PenStyle.SolidLine),
                                     (chikou, Theme.TEXT_FAINT, Qt.PenStyle.DashLine)):
            pts = [QPointF(px(i0 + i), py(v)) for i, v in enumerate(series[i0:i1][:shown]) if v is not None]
            if len(pts) > 1:
                p.setPen(QPen(QColor(color), 1.3, style))
                p.drawPolyline(QPolygonF(pts))

    def _draw_price(self, p, view, shown, i0, px, py, cw, top, price_h):
        vis = view[:shown]
        if not vis:
            return
        body = max(1.0, cw * 0.68)
        t = self.chart_type
        bottom = top + price_h
        if t in ("Line", "Area", "Baseline", "Step"):
            pts = []
            for i, c in enumerate(vis):
                x, y = px(i0 + i), py(c.c)
                if t == "Step" and pts:
                    pts.append(QPointF(x, pts[-1].y()))
                pts.append(QPointF(x, y))
            if len(pts) < 2:
                return
            if t == "Baseline":
                by = py(sum(c.c for c in view) / len(view))
                p.setPen(QPen(QColor(Theme.TEXT_FAINT), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(pts[0].x(), by), QPointF(pts[-1].x(), by))
                for color, above in ((Theme.BULL, True), (Theme.BEAR, False)):
                    path = QPainterPath()
                    path.moveTo(QPointF(pts[0].x(), by))
                    for pt in pts:
                        path.lineTo(QPointF(pt.x(), min(pt.y(), by) if above else max(pt.y(), by)))
                    path.lineTo(QPointF(pts[-1].x(), by))
                    path.closeSubpath()
                    c = QColor(color)
                    c.setAlpha(64)
                    p.fillPath(path, QBrush(c))
                p.setPen(QPen(QColor(Theme.TEXT), 1.6))
                p.drawPolyline(QPolygonF(pts))
                return
            if t == "Area":
                self._fill_under(p, pts, top, bottom, 105)
            glow = QColor(Theme.ACCENT)
            glow.setAlpha(55)
            p.setPen(QPen(glow, 5))
            p.drawPolyline(QPolygonF(pts))
            p.setPen(QPen(QColor(Theme.ACCENT), 2))
            p.drawPolyline(QPolygonF(pts))
            return
        # soft depth fill under the closes so candle charts don't look flat
        self._fill_under(p, [QPointF(px(i0 + i), py(vis[i].c)) for i in range(0, len(vis), self._stride)],
                         top, bottom, 26)
        if t in ("Bars", "HLC"):
            for i, c in enumerate(vis):
                x = px(i0 + i)
                p.setPen(QPen(QColor(Theme.BULL if c.c >= c.o else Theme.BEAR), max(1.2, min(2.2, cw * 0.16))))
                p.drawLine(QPointF(x, py(c.h)), QPointF(x, py(c.l)))
                if t == "Bars":
                    p.drawLine(QPointF(x - body / 2, py(c.o)), QPointF(x, py(c.o)))
                p.drawLine(QPointF(x, py(c.c)), QPointF(x + body / 2, py(c.c)))
            return
        if cw < 2.4:   # zoomed far out: one crisp high-low line per bar is all that is visible
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            up_pen, dn_pen = QPen(QColor(Theme.BULL), 1), QPen(QColor(Theme.BEAR), 1)
            for i, c in enumerate(vis):
                x = px(i0 + i)
                p.setPen(up_pen if c.c >= c.o else dn_pen)
                p.drawLine(QPointF(x, py(c.h)), QPointF(x, py(c.l)))
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            return
        hollow = t == "Hollow"
        radius = 1.6 if body >= 6 else 0.0
        wick_w = 1.0 if cw < 9 else 1.4
        for i, c in enumerate(vis):
            x = px(i0 + i)
            up = c.c >= c.o
            col = QColor(Theme.BULL if up else Theme.BEAR)
            p.setPen(QPen(col, wick_w))
            p.drawLine(QPointF(x, py(c.h)), QPointF(x, py(c.l)))
            y1, y2 = py(c.o), py(c.c)
            rect = QRectF(x - body / 2, min(y1, y2), body, max(1.0, abs(y1 - y2)))
            if hollow and up:
                p.setBrush(QColor("#080C1E"))
                p.setPen(QPen(col, 1.2))
                p.drawRoundedRect(rect, radius, radius)
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif radius:
                p.setPen(Qt.PenStyle.NoPen)
                grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
                grad.setColorAt(0, col.lighter(118))
                grad.setColorAt(1, col.darker(112))
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(rect, radius, radius)
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                p.fillRect(rect, col)

    @staticmethod
    def _fill_under(p, pts, top, bottom, alpha):
        if len(pts) < 2:
            return
        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        path.lineTo(QPointF(pts[-1].x(), bottom))
        path.lineTo(QPointF(pts[0].x(), bottom))
        path.closeSubpath()
        g = QLinearGradient(0, top, 0, bottom)
        g.setColorAt(0, QColor(25, 230, 195, alpha))
        g.setColorAt(1, QColor(25, 230, 195, 0))
        p.fillPath(path, QBrush(g))

    def _draw_compare(self, p, i0, i1, px, py):
        if not self._compare:
            return
        _sym, closes = self._compare
        shift = len(self._disp) - len(closes)
        pairs = [(i, closes[i - shift]) for i in range(i0, i1) if 0 <= i - shift < len(closes) and closes[i - shift]]
        if len(pairs) < 2:
            return
        base_main = self._disp[pairs[0][0]].c
        base_cmp = pairs[0][1]
        pts = [QPointF(px(i), py(base_main * (v / base_cmp))) for i, v in pairs]
        p.setPen(QPen(QColor(Theme.GOLD), 1.7, Qt.PenStyle.DashLine))
        p.drawPolyline(QPolygonF(pts))

    def _draw_sub(self, p, key, y0, h, plot_w, cw, i0, i1, shown, px):
        p.setPen(QPen(QColor(Theme.BORDER), 1))
        p.drawLine(QPointF(0, y0), QPointF(plot_w + AXIS_W, y0))
        inner_top, inner_h = y0 + 15, h - 21
        p.setFont(make_font(7.5, QFont.Weight.Bold, spacing=1))
        p.setPen(QColor(Theme.TEXT_MUTED))
        p.drawText(QPointF(9, y0 + 13), INDICATOR_LABELS.get(key, key).upper())
        p.setFont(self._f_small)
        p.save()
        p.setClipRect(QRectF(0, y0, plot_w + AXIS_W, h))

        def scale(v, lo_v, hi_v):
            return inner_top + (hi_v - v) / ((hi_v - lo_v) or 1) * inner_h

        st = self._stride

        def draw(series, color, lo_v, hi_v, width=1.4):
            seg = series[i0:i1][:shown]
            pts = [QPointF(px(i0 + i), scale(seg[i], lo_v, hi_v))
                   for i in range(0, len(seg), st) if seg[i] is not None]
            if len(pts) > 1:
                p.setPen(QPen(QColor(color), width))
                p.drawPolyline(QPolygonF(pts))

        def level(v, lo_v, hi_v):
            yy = scale(v, lo_v, hi_v)
            p.setPen(QPen(QColor(Theme.BORDER_HI), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(0, yy), QPointF(plot_w, yy))
            p.setPen(QColor(Theme.TEXT_FAINT))
            p.drawText(QPointF(plot_w + 8, yy + 4), f"{v:g}")

        def auto_range(*series_list):
            vals = [v for s in series_list for v in s[i0:i1] if v is not None]
            if not vals:
                return 0.0, 1.0
            lo_v, hi_v = min(vals), max(vals)
            if hi_v == lo_v:
                hi_v += 1
            pad = (hi_v - lo_v) * 0.1
            return lo_v - pad, hi_v + pad

        if key == "rsi":
            level(70, 0, 100)
            level(30, 0, 100)
            draw(self._calc("rsi"), Theme.PURPLE, 0, 100, 1.6)
        elif key == "stoch":
            k, d = self._calc("stoch")
            level(80, 0, 100)
            level(20, 0, 100)
            draw(k, Theme.ACCENT_2, 0, 100)
            draw(d, Theme.WARN, 0, 100)
        elif key == "stochrsi":
            k, d = self._calc("stochrsi")
            level(80, 0, 100)
            level(20, 0, 100)
            draw(k, Theme.PINK, 0, 100)
            draw(d, Theme.GOLD, 0, 100)
        elif key == "willr":
            level(-20, -100, 0)
            level(-80, -100, 0)
            draw(self._calc("willr"), Theme.ACCENT_2, -100, 0, 1.5)
        elif key == "cci":
            lo_v, hi_v = auto_range(self._calc("cci"))
            level(100, lo_v, hi_v)
            level(-100, lo_v, hi_v)
            draw(self._calc("cci"), Theme.ORANGE, lo_v, hi_v, 1.5)
        elif key == "adx":
            plus, minus, adx_s = self._calc("adx")
            level(25, 0, 60)
            draw(plus, Theme.BULL, 0, 60)
            draw(minus, Theme.BEAR, 0, 60)
            draw(adx_s, Theme.GOLD, 0, 60, 1.7)
        elif key == "mfi":
            level(80, 0, 100)
            level(20, 0, 100)
            draw(self._calc("mfi"), Theme.BLUE, 0, 100, 1.5)
        elif key in ("atr", "obv", "roc", "cmf"):
            s = self._calc(key)
            lo_v, hi_v = auto_range(s)
            if key in ("roc", "cmf"):
                level(0, lo_v, hi_v)
            draw(s, {"atr": Theme.GOLD, "obv": Theme.ACCENT_2, "roc": Theme.PINK, "cmf": Theme.BLUE}[key],
                 lo_v, hi_v, 1.6)
        elif key == "macd":
            ml, sl, hs = self._calc("macd")
            lo_v, hi_v = auto_range(ml, sl, hs)
            amp = max(abs(lo_v), abs(hi_v)) or 1
            zero = scale(0, -amp, amp)
            bw = max(1.0, cw * 0.62)
            pos_c, neg_c = QColor(Theme.BULL), QColor(Theme.BEAR)
            pos_c.setAlpha(165)
            neg_c.setAlpha(165)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for i, hv in enumerate(hs[i0:i1][:shown]):
                if hv is None:
                    continue
                y = scale(hv, -amp, amp)
                p.fillRect(QRectF(px(i0 + i) - bw / 2, min(zero, y), bw, abs(y - zero)),
                           pos_c if hv >= 0 else neg_c)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            draw(ml, Theme.ACCENT_2, -amp, amp)
            draw(sl, Theme.WARN, -amp, amp)
        p.restore()

    # --- drawings ---------------------------------------------------------
    def _draw_shapes(self, p, px, py, plot_w, top, price_h):
        def xy(pt):
            return QPointF(px(pt[0]), py(pt[1]))

        items = [(i, sh) for i, sh in enumerate(self._shapes)]
        if self._draft:
            items.append((-1, self._draft))
        for index, sh in items:
            t, pts = sh["type"], sh["pts"]
            selected = index == self._sel and index >= 0
            boost = 0.9 if selected else 0.0
            p.setFont(self._f_small)
            try:
                if t == "hline":
                    y = py(pts[0][1])
                    p.setPen(QPen(QColor(Theme.GOLD), 1.3 + boost, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(0, y), QPointF(plot_w, y))
                    p.drawText(QPointF(8, y - 5), f"{pts[0][1]:,.2f}")
                elif t == "hray":
                    a = xy(pts[0])
                    p.setPen(QPen(QColor(Theme.GOLD), 1.3 + boost, Qt.PenStyle.DashLine))
                    p.drawLine(a, QPointF(plot_w, a.y()))
                    p.drawText(a + QPointF(6, -5), f"{pts[0][1]:,.2f}")
                elif t == "vline":
                    x = px(pts[0][0])
                    p.setPen(QPen(QColor(Theme.GOLD), 1.3 + boost, Qt.PenStyle.DashLine))
                    p.drawLine(QPointF(x, top), QPointF(x, top + price_h))
                elif t == "text":
                    a = xy(pts[0])
                    p.setFont(self._f_ui)
                    p.setPen(QColor(Theme.TEXT))
                    p.drawText(a, sh.get("text", ""))
                elif t in ("trend", "ray", "extended", "arrow", "measure"):
                    a, b = xy(pts[0]), xy(pts[1])
                    la, lb = a, b
                    if t in ("ray", "extended"):
                        dx, dy = b.x() - a.x(), b.y() - a.y()
                        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                            f = (plot_w * 3) / max(1e-6, math.hypot(dx, dy))
                            lb = QPointF(a.x() + dx * f, a.y() + dy * f)
                            if t == "extended":
                                la = QPointF(a.x() - dx * f, a.y() - dy * f)
                    col = Theme.ACCENT if t != "measure" else (Theme.BULL if pts[1][1] >= pts[0][1] else Theme.BEAR)
                    p.setPen(QPen(QColor(col), 1.7 + boost,
                                  Qt.PenStyle.DashLine if t == "measure" else Qt.PenStyle.SolidLine))
                    p.drawLine(la, lb)
                    if t == "arrow":
                        self._arrow_head(p, a, b, QColor(Theme.ACCENT))
                    if t == "measure":
                        p0, p1 = pts[0][1], pts[1][1]
                        chg = (p1 - p0) / p0 * 100 if p0 else 0
                        bars = abs(int(round(pts[1][0])) - int(round(pts[0][0])))
                        text = f"{p1 - p0:+,.2f}  ({chg:+.2f}%)  ·  {bars} bars"
                        tw = p.fontMetrics().horizontalAdvance(text) + 14
                        chip = QRectF(b.x() + 8, b.y() - 20, tw, 18)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor(col))
                        p.drawRoundedRect(chip, 4, 4)
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(QColor("#04120B"))
                        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
                elif t == "rect":
                    r = QRectF(xy(pts[0]), xy(pts[1])).normalized()
                    p.setPen(QPen(QColor(Theme.BLUE), 1.3 + boost))
                    p.setBrush(QColor(91, 140, 255, 30))
                    p.drawRoundedRect(r, 2, 2)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                elif t == "ellipse":
                    r = QRectF(xy(pts[0]), xy(pts[1])).normalized()
                    p.setPen(QPen(QColor(Theme.PURPLE), 1.3 + boost))
                    p.setBrush(QColor(167, 139, 250, 28))
                    p.drawEllipse(r)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                elif t == "channel":
                    self._draw_channel(p, pts, px, py, boost)
                elif t in ("fib", "fibext"):
                    levels = FIB_LEVELS if t == "fib" else FIB_EXT_LEVELS
                    a, b = xy(pts[0]), xy(pts[1])
                    x1 = min(a.x(), b.x())
                    p0, p1 = pts[0][1], pts[1][1]
                    p.setPen(QPen(QColor(Theme.TEXT_FAINT), 1, Qt.PenStyle.DotLine))
                    p.drawLine(a, b)
                    last_label_y = None
                    for lv in levels:
                        price = p1 + (p0 - p1) * lv if t == "fib" else p0 + (p1 - p0) * lv
                        y = py(price)
                        col = QColor(Theme.GOLD if lv in (0.5, 0.618, 1.618) else Theme.PURPLE)
                        p.setPen(QPen(col, 1.0 + boost * 0.6))
                        p.drawLine(QPointF(x1, y), QPointF(plot_w, y))
                        if last_label_y is None or abs(y - last_label_y) >= 13:
                            p.drawText(QPointF(x1 + 5, y - 4), f"{lv:.3f}   {price:,.2f}")
                            last_label_y = y
                elif t in ("long", "short"):
                    self._draw_position(p, t, pts, px, py)
                if selected:
                    self._draw_handles(p, sh)
            except Exception:
                continue  # one bad shape must never break the chart

    def _draw_handles(self, p, sh):
        p.setPen(QPen(QColor(Theme.ACCENT), 1.6))
        p.setBrush(QColor("#FFFFFF"))
        for hx, hy in self._shape_points(sh):
            p.drawEllipse(QPointF(hx, hy), 4.5, 4.5)
        p.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _arrow_head(p, a: QPointF, b: QPointF, color: QColor):
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        size = 10
        left = QPointF(b.x() - size * math.cos(ang - 0.4), b.y() - size * math.sin(ang - 0.4))
        right = QPointF(b.x() - size * math.cos(ang + 0.4), b.y() - size * math.sin(ang + 0.4))
        p.setBrush(color)
        p.setPen(QPen(color, 1))
        p.drawPolygon(QPolygonF([b, left, right]))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_channel(self, p, pts, px, py, boost=0.0):
        if len(pts) < 3:
            return
        a, b, c = pts[0], pts[1], pts[2]
        offset = c[1] - (a[1] + (b[1] - a[1]) * ((c[0] - a[0]) / ((b[0] - a[0]) or 1e-9)))
        pa, pb = QPointF(px(a[0]), py(a[1])), QPointF(px(b[0]), py(b[1]))
        qa, qb = QPointF(px(a[0]), py(a[1] + offset)), QPointF(px(b[0]), py(b[1] + offset))
        path = QPainterPath()
        path.moveTo(pa)
        path.lineTo(pb)
        path.lineTo(qb)
        path.lineTo(qa)
        path.closeSubpath()
        col = QColor(Theme.ACCENT)
        col.setAlpha(28)
        p.fillPath(path, QBrush(col))
        p.setPen(QPen(QColor(Theme.ACCENT), 1.6 + boost))
        p.drawLine(pa, pb)
        p.drawLine(qa, qb)

    def _draw_position(self, p, kind, pts, px, py):
        entry, target = pts[0][1], pts[1][1]
        stop = entry - (target - entry) * 0.5
        x1, x2 = sorted((px(pts[0][0]), px(pts[1][0])))
        width = max(20.0, x2 - x1)
        prof, loss = QColor(Theme.BULL), QColor(Theme.BEAR)
        prof.setAlpha(50)
        loss.setAlpha(50)
        y_e, y_t, y_s = py(entry), py(target), py(stop)
        p.fillRect(QRectF(x1, min(y_e, y_t), width, abs(y_t - y_e)), prof)
        p.fillRect(QRectF(x1, min(y_e, y_s), width, abs(y_s - y_e)), loss)
        p.setPen(QPen(QColor(Theme.TEXT), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(x1, y_e), QPointF(x1 + width, y_e))
        risk, reward = abs(entry - stop), abs(target - entry)
        p.setPen(QColor(Theme.TEXT))
        p.setFont(self._f_small)
        p.drawText(QPointF(x1 + 5, min(y_t, y_s) - 6),
                   f"{'LONG' if kind == 'long' else 'SHORT'}   R:R {reward / risk if risk else 0:.2f}"
                   f"   target {target:,.2f}   stop {stop:,.2f}")

    # --- dynamic layer ----------------------------------------------------
    def _chip(self, p: QPainter, text: str, x: float, y: float, fg: str = Theme.TEXT, bg: str = Theme.SURFACE_4):
        p.setFont(self._f_axis)
        w = p.fontMetrics().horizontalAdvance(text) + 16
        x = max(4.0, min(self.width() - w - 4.0, x))
        r = QRectF(x, y, w, 20)
        p.setPen(QPen(QColor(Theme.BORDER_HI), 1))
        p.setBrush(QColor(bg))
        p.drawRoundedRect(r, 5, 5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QColor(fg))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_dynamic(self, p: QPainter):
        g = self._geo
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        plot_w, top, price_h, H = g["plot_w"], g["top"], g["price_h"], g["H"]
        n = len(self._disp)
        if not n:
            return

        # pulsing last-price dot
        if self.settings.get("last_price") and self._reveal >= 1.0:
            last = self._disp[-1]
            lx, ly = self._px(n - 1), self._py(last.c)
            if 0 <= lx <= plot_w and top <= ly <= top + price_h:
                col = QColor(Theme.BULL if last.c >= last.o else Theme.BEAR)
                pulse = 0.5 + 0.5 * math.sin(self._pulse)
                halo = QColor(col)
                halo.setAlpha(int(30 + 70 * (1 - pulse)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(lx, ly), 4 + 7 * pulse, 4 + 7 * pulse)
                p.setBrush(col)
                p.drawEllipse(QPointF(lx, ly), 3.2, 3.2)
                p.setBrush(Qt.BrushStyle.NoBrush)

        mx, my = self._mouse.x(), self._mouse.y()
        hover = None
        in_plot = 0 <= mx < plot_w and 0 <= my < H - TIME_H
        if in_plot and (self._drag is None or self._drag["mode"] in ("shape", "point")):
            idx = int(round(g["left"] + mx / g["cw"] - 0.5))
            snap = 0 <= idx < n
            cx = self._px(idx) if snap else float(mx)
            hover = idx if snap else None
            p.setPen(QPen(QColor(147, 160, 192, 150), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx, top), QPointF(cx, H - TIME_H))
            p.drawLine(QPointF(0, my), QPointF(plot_w, my))
            if top <= my <= top + price_h:
                price = self._unscale_y(my, g["hi"], g["lo"], top, price_h)
                tag = QRectF(plot_w + 1, my - 10, AXIS_W - 3, 20)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(Theme.SURFACE_4))
                p.drawRoundedRect(tag, 4, 4)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QColor(Theme.TEXT))
                p.setFont(self._f_axis)
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, self._axis_text(price, g["base"]))
            if snap and idx < len(self._disp_labels):
                self._chip(p, self._disp_labels[idx], cx - 50, H - TIME_H + 2)
            # hover name for drawings
            if self.tool == "cursor" and self._hover_shape is not None and self._hover_shape < len(self._shapes) \
                    and self._drag is None:
                name = TOOL_LABELS.get(self._shapes[self._hover_shape]["type"], "Drawing")
                tail = "drag to move · Del to delete" if self._hover_shape == self._sel else "click to select"
                self._chip(p, f"{name}  ·  {tail}", mx + 14, my + 16, Theme.ACCENT)
            elif self.tool != "cursor":
                need = TOOL_POINTS.get(self.tool, 2)
                hint = "click to place" if need == 1 else (
                    "click to set width" if self._draft and self._draft.get("phase") == 2 else "press and drag")
                self._chip(p, f"{TOOL_LABELS.get(self.tool, '')}  ·  {hint}", mx + 14, my + 16, Theme.GOLD)
        if self.settings.get("legend"):
            self._paint_legend(p, hover if hover is not None else n - 1, g)
        if self.has_selection():
            self._chip(p, f"{self.selected_label()} selected  ·  drag to move  ·  Del to delete  ·  Esc to deselect",
                       10, top + price_h - 28, Theme.ACCENT, "#10203A")
        elif not self._y_auto:
            self._chip(p, "Manual price scale  ·  double-click the price axis to reset", 10,
                       top + price_h - 28, Theme.TEXT_MUTED, "#10203A")

    def _paint_legend(self, p: QPainter, i: int, g: dict):
        if not (0 <= i < len(self._disp)):
            return
        c = self._disp[i]
        prev = self._disp[i - 1].c if i > 0 else c.o
        chg = (c.c - prev) / prev * 100 if prev else 0.0
        price_col = Theme.BULL if c.c >= prev else Theme.BEAR
        base = self.symbol.split(".")[0].lstrip("^")
        head = base
        sub = "  ·  ".join(x for x in (self.display_name[:26], self.timeframe, self.chart_type) if x)
        ohlc = f"O {c.o:,.2f}   H {c.h:,.2f}   L {c.l:,.2f}   C {c.c:,.2f}   {chg:+.2f}%"
        rows: list[tuple[str, str, str]] = []
        for key in MA_SPECS:
            if self.ind.get(key):
                v = self._calc(key)[i]
                if v is not None:
                    rows.append((INDICATOR_LABELS[key], f"{v:,.2f}", MA_SPECS[key][2]))
        if self.ind.get("vwap"):
            v = self._calc("vwap")[i]
            if v is not None:
                rows.append(("VWAP", f"{v:,.2f}", "#FFFFFF"))
        if self.ind.get("volume"):
            vol = f"{c.v / 1e7:.2f}Cr" if c.v >= 1e9 else f"{c.v / 1e6:.2f}M" if c.v >= 1e6 else f"{c.v:,.0f}"
            rows.append(("Vol", vol, Theme.TEXT_MUTED))
        if self._compare:
            rows.append(("vs", self._compare[0].split(".")[0], Theme.GOLD))
        rows = rows[:8]

        fm_t, fm_l = QFontMetrics(self._f_title), QFontMetrics(self._f_legend)
        head_w = fm_t.horizontalAdvance(head) + 12 + QFontMetrics(self._f_small).horizontalAdvance(sub)
        row_text = "     ".join(f"{n} {v}" for n, v, _ in rows)
        w = max(head_w, fm_l.horizontalAdvance(ohlc), fm_l.horizontalAdvance(row_text)) + 26
        w = min(w, max(180.0, g["plot_w"] - 20))
        h = 48 + (18 if rows else 0)
        box = QRectF(10, 8, w, h)
        p.setPen(QPen(QColor(Theme.BORDER_HI), 1))
        p.setBrush(QColor(14, 20, 40, 225))
        p.drawRoundedRect(box, 8, 8)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setFont(self._f_title)
        p.setPen(QColor(Theme.TEXT))
        p.drawText(QPointF(box.x() + 12, box.y() + 19), head)
        p.setFont(self._f_small)
        p.setPen(QColor(Theme.TEXT_MUTED))
        p.drawText(QPointF(box.x() + 12 + fm_t.horizontalAdvance(head) + 10, box.y() + 19), sub)
        p.setFont(self._f_legend)
        p.setPen(QColor(price_col))
        p.drawText(QPointF(box.x() + 12, box.y() + 38), ohlc)
        x = box.x() + 12
        for name, val, color in rows:
            text = f"{name} {val}"
            if x + fm_l.horizontalAdvance(text) > box.right() - 8:
                break
            p.setPen(QColor(color))
            p.drawText(QPointF(x, box.y() + 56), text)
            x += fm_l.horizontalAdvance(text) + fm_l.horizontalAdvance("     ")
