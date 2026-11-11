"""
CORTEX - desktop stock scanner and charting workstation.

  cortex_theme.py       colour system, vector icons, animated widgets
  cortex_indicators.py  indicator maths
  cortex_chart.py       native chart engine
  cortex_app.py         engine threads, views, main window  (this file)
  scanner.py            data engine (downloads, scan loop, news)

How the app is organised (the way a trading terminal works):
  - one ACTIVE SYMBOL drives the chart, the news feed and the dashboard
  - the WATCHLIST on the right is always there: live quotes, click to open
  - the SEARCH box in the top bar finds any stock and makes it active

Run:  python cortex_app.py   (or double-click RUN_CORTEX.bat)
"""

from __future__ import annotations

import math
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF,
    QSize, Qt, QThread, QTimer, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QBrush, QColor, QDesktopServices, QFont,
    QFontMetrics, QIcon, QKeySequence, QLinearGradient, QPainter, QPen,
    QPixmap, QRadialGradient, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QPushButton, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import cortex_chart as CC
from cortex_chart import CandleChart
from cortex_indicators import Candle
from cortex_theme import (
    AuroraBanner, BarList, Equalizer, GlowCard, IconButton, NumItem,
    ProgressStrip, PulseDot, Radar, SegmentedControl, Sparkline, StatCard,
    Theme, TickerTape, TipBubble, Toast, clear_layout, divider, draw_icon, group_label,
    make_font, make_table, paint_backdrop, scroll_page, stylesheet,
)

APP_VERSION = "2.1"
DEFAULT_WATCHLIST = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "AAPL", "NVDA", "MSFT"]


# ===========================================================================
# Engine access — every engine call happens on a worker thread
# ===========================================================================
_LOCK = threading.Lock()
_ENGINES: dict[str, Any] = {}
_ENGINE_ERROR = ""
LIVE_WORKERS: set = set()


def load_scanner():
    global _ENGINE_ERROR
    try:
        import scanner
        return scanner
    except Exception as exc:
        _ENGINE_ERROR = str(exc)
        return None


def engine_for(role: str):
    """One engine per role so background threads never share state."""
    with _LOCK:
        if role not in _ENGINES:
            sc = load_scanner()
            if sc is None:
                raise RuntimeError(f"Data engine unavailable ({_ENGINE_ERROR}). "
                                   "Run: pip install -r requirements.txt")
            _ENGINES[role] = sc.ScannerEngine()
        return _ENGINES[role]


def launch(worker: QThread) -> QThread:
    LIVE_WORKERS.add(worker)
    worker.finished.connect(lambda w=worker: LIVE_WORKERS.discard(w))
    worker.start()
    return worker


def ui_state() -> dict:
    sc = load_scanner()
    if sc is None:
        return {}
    try:
        return dict(sc.load_ui_state() or {})
    except Exception:
        return {}


def ui_state_update(**changes):
    sc = load_scanner()
    if sc is None:
        return
    try:
        state = dict(sc.load_ui_state() or {})
        state.update(changes)
        sc.save_ui_state(state)
    except Exception:
        pass


TF_MAP = {"1m": ("5d", "1m"), "3m": ("1mo", "5m"), "5m": ("1mo", "5m"), "15m": ("1mo", "15m"),
          "30m": ("2mo", "30m"), "1H": ("6mo", "1h"), "2H": ("1y", "1h"), "4H": ("1y", "1h"),
          "1D": ("2y", "1d"), "1W": ("5y", "1wk"), "1M": ("10y", "1mo")}
TF_GROUP = {"2H": 2, "4H": 4}
INDEX_SYMBOLS = [("NIFTY 50", "^NSEI"), ("SENSEX", "^BSESN"), ("BANK NIFTY", "^NSEBANK"),
                 ("S&P 500", "^GSPC"), ("NASDAQ", "^IXIC"), ("DOW", "^DJI")]
SYMBOL_RE = re.compile(r"^[A-Z0-9^][A-Z0-9^.&\-=]{0,19}$")


def parse_symbols(text: str) -> list[str]:
    """Turn free text ('tcs, infy  AAPL\\nNVDA') into a clean symbol list."""
    out: list[str] = []
    for token in re.split(r"[\s,;]+", text.upper()):
        if token and SYMBOL_RE.match(token) and token not in out:
            out.append(token)
    return out


def frame_to_candles(df, tf: str) -> tuple[list[Candle], list[str]]:
    intraday = tf in ("1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H")
    fmt = "%d %b %H:%M" if intraday else "%d %b %Y"
    candles, labels = [], []
    for stamp, row in zip(df.index, df.itertuples(index=False)):
        try:
            o, h, l, c, v = (float(x) for x in row[:5])
        except Exception:
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c)):
            continue
        candles.append(Candle(o, h, l, c, v if math.isfinite(v) else 0.0))
        try:
            labels.append(stamp.strftime(fmt))
        except Exception:
            labels.append(str(stamp))
    group = TF_GROUP.get(tf)
    if group and candles:
        merged, mlabels = [], []
        for i in range(0, len(candles), group):
            grp = candles[i:i + group]
            merged.append(Candle(grp[0].o, max(g.h for g in grp), min(g.l for g in grp),
                                 grp[-1].c, sum(g.v for g in grp)))
            mlabels.append(labels[i])
        candles, labels = merged, mlabels
    return candles, labels


def market_sessions(now: Optional[datetime] = None) -> list[tuple[str, bool, str]]:
    """Open/closed status for Indian and US markets (UTC based, US DST aware)."""
    now = now or datetime.now(timezone.utc)
    weekday = now.weekday() < 5
    mins = now.hour * 60 + now.minute

    def us_dst(d: datetime) -> bool:
        if 4 <= d.month <= 10:
            return True
        if d.month == 3:
            second_sunday = 14 - ((datetime(d.year, 3, 1, tzinfo=timezone.utc).weekday() + 1) % 7)
            return d.day > second_sunday or (d.day == second_sunday and d.hour >= 7)
        if d.month == 11:
            first_sunday = 7 - ((datetime(d.year, 11, 1, tzinfo=timezone.utc).weekday() + 1) % 7)
            return d.day < first_sunday or (d.day == first_sunday and d.hour < 6)
        return False

    out = [("NSE", weekday and 3 * 60 + 45 <= mins < 10 * 60, "09:15–15:30 IST")]
    shift = 0 if us_dst(now) else 60
    out.append(("NYSE", weekday and 13 * 60 + 30 + shift <= mins < 20 * 60 + shift, "09:30–16:00 ET"))
    return out


def icon_pixmap(kind: str, size: int = 18, color: str = Theme.TEXT_MUTED) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    draw_icon(p, kind, QRectF(1, 1, size - 2, size - 2), QColor(color))
    p.end()
    return pm


# ------------------------------------------------------------------ workers
class WarmWorker(QThread):
    ready = Signal()
    failed = Signal(str)

    def run(self):
        try:
            engine_for("chart")
            self.ready.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ChartWorker(QThread):
    loaded = Signal(int, str, str, object, object)
    failed = Signal(int, str)

    def __init__(self, token: int, symbol: str, tf: str, role: str = "chart"):
        super().__init__()
        self.token, self.symbol, self.tf, self.role = token, symbol, tf, role

    def run(self):
        try:
            eng = engine_for(self.role)
            sc = load_scanner()
            period, interval = TF_MAP.get(self.tf, ("2y", "1d"))
            sym = self.symbol if self.symbol.startswith("^") else eng.normalize_symbol(self.symbol)
            df = sc._clean_history_frame(eng._download_single_history(sym, period=period, interval=interval))
            if df is None or df.empty:
                self.failed.emit(self.token, f"No price data returned for {sym}. "
                                             "Check the symbol, or your internet connection.")
                return
            candles, labels = frame_to_candles(df, self.tf)
            if not candles:
                self.failed.emit(self.token, f"No usable price data for {sym}.")
                return
            self.loaded.emit(self.token, sym, self.tf, candles, labels)
        except Exception as exc:
            self.failed.emit(self.token, str(exc))


class NewsWorker(QThread):
    loaded = Signal(int, str, list)
    failed = Signal(int, str)

    def __init__(self, token: int, ticker: str, name: str = ""):
        super().__init__()
        self.token, self.ticker, self.name = token, ticker, name

    def run(self):
        try:
            items = engine_for("news").fetch_news_for_result(
                {"ticker": self.ticker, "company_name": self.name}, limit=40)
            self.loaded.emit(self.token, self.ticker, list(items or []))
        except Exception as exc:
            self.failed.emit(self.token, str(exc))


class SuggestWorker(QThread):
    loaded = Signal(int, list)

    def __init__(self, token: int, query: str):
        super().__init__()
        self.token, self.query = token, query

    def run(self):
        try:
            hits = engine_for("search").search_tickers(self.query, limit=10) or []
            out = [(str(h.get("symbol", "")), str(h.get("company_name", "")), str(h.get("exchange", "")))
                   for h in hits]
            self.loaded.emit(self.token, [h for h in out if h[0]])
        except Exception:
            self.loaded.emit(self.token, [])


class IndexWorker(QThread):
    loaded = Signal(list)

    def run(self):
        out = []
        try:
            eng = engine_for("index")
            sc = load_scanner()
            for name, sym in INDEX_SYMBOLS:
                try:
                    df = sc._clean_history_frame(eng._download_single_history(sym, period="1mo", interval="1d"))
                    closes = [float(x) for x in df["Close"].tolist() if math.isfinite(float(x))]
                    if len(closes) >= 2 and closes[-2]:
                        out.append((name, closes[-1], (closes[-1] - closes[-2]) / closes[-2] * 100, closes[-22:]))
                except Exception:
                    continue
        except Exception:
            pass
        self.loaded.emit(out)


class QuoteWorker(QThread):
    loaded = Signal(dict)

    def __init__(self, symbols: list[str]):
        super().__init__()
        self.symbols = list(symbols)

    def run(self):
        try:
            self.loaded.emit(dict(engine_for("quotes").fetch_quote_snapshots(self.symbols) or {}))
        except Exception:
            self.loaded.emit({})


class ScanWorker(QThread):
    event = Signal(dict)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, plan: dict, stop_event: threading.Event, skip: set):
        super().__init__()
        self.plan, self.stop_event, self.skip = plan, stop_event, set(skip)

    def run(self):
        try:
            eng = engine_for("scan")
            sc = load_scanner()
            for key, value in (self.plan.get("settings") or {}).items():
                if hasattr(eng.settings, key):
                    current = getattr(eng.settings, key)
                    setattr(eng.settings, key, type(current)(value))
            eng.configure_runtime(int(self.plan.get("workers", 8)))
            for job in self.plan["jobs"]:
                if self.stop_event.is_set():
                    break
                exchange = job.get("exchange") or sc.GLOBAL_EXCHANGE
                self.event.emit({"type": "job", "label": job["label"]})
                if job.get("full"):
                    tickers = eng.build_full_exchange_list(exchange)
                else:
                    tickers = eng.resolve_tickers(job.get("tickers", []), exchange=exchange)
                tickers = [t for t in tickers if t not in self.skip]
                self.event.emit({"type": "job_total", "count": len(tickers)})
                if not tickers:
                    continue
                results, _errors, _summary = eng.scan_market(
                    tickers, exchange=exchange, turbo_mode=bool(self.plan.get("turbo", True)),
                    enrich_metadata=True, progress_callback=lambda ev: self.event.emit(dict(ev)),
                    stop_event=self.stop_event)
                self.event.emit({"type": "final_results", "results": list(results or [])})
            self.done.emit({"aborted": self.stop_event.is_set()})
        except Exception as exc:
            self.failed.emit(str(exc))


# ===========================================================================
# Splash
# ===========================================================================
class SplashScreen(QWidget):
    finished = Signal()

    def __init__(self, duration_ms=2200):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setFixedSize(QSize(520, 340))
        self._phase = 0.0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(16)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addStretch(2)
        self.word = QLabel("CORTEX")
        self.word.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.word.setFont(make_font(36, QFont.Weight.Black, spacing=11))
        self.word.setStyleSheet(f"color: {Theme.TEXT};")
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(50)
        glow.setColor(QColor(25, 230, 195, 180))
        glow.setOffset(0, 0)
        self.word.setGraphicsEffect(glow)
        root.addWidget(self.word)
        tag = QLabel("MULTI-EXCHANGE SIGNAL SCANNER")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: 10px; letter-spacing: 4px; font-weight: 600;")
        root.addWidget(tag)
        root.addSpacing(22)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(Equalizer(13))
        row.addStretch(1)
        root.addLayout(row)
        root.addSpacing(12)
        self.status = QLabel("Starting engine...")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: 11px;")
        root.addWidget(self.status)
        root.addStretch(2)
        self._msgs = ["Starting engine...", "Loading exchanges...", "Syncing watchlist...", "Ready."]
        self._i = 0
        self._mt = QTimer(self)
        self._mt.timeout.connect(self._cycle)
        self._mt.start(max(350, duration_ms // 4))
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(500)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        QTimer.singleShot(duration_ms, self._begin_close)

    def _tick(self):
        self._phase = (self._phase + 0.02) % (math.pi * 2)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        g = QLinearGradient(0, 0, r.width(), r.height())
        g.setColorAt(0, QColor("#111A40"))
        g.setColorAt(1, QColor("#070A18"))
        p.fillRect(r, QBrush(g))
        for color, off, rad in ((QColor(25, 230, 195, 60), 0.0, 260), (QColor(91, 140, 255, 56), 2.4, 240),
                                (QColor(167, 139, 250, 44), 4.2, 220)):
            cx = r.center().x() + math.cos(self._phase + off) * 80
            cy = r.center().y() - 14 + math.sin(self._phase + off) * 34
            rg = QRadialGradient(cx, cy, rad)
            rg.setColorAt(0, color)
            rg.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
            p.fillRect(r, QBrush(rg))
        p.setPen(QPen(QColor(Theme.BORDER_HI), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.end()

    def _cycle(self):
        self._i = min(self._i + 1, len(self._msgs) - 1)
        self.status.setText(self._msgs[self._i])

    def _begin_close(self):
        self._mt.stop()
        self.finished.emit()
        out = QPropertyAnimation(self, b"windowOpacity", self)
        out.setDuration(260)
        out.setStartValue(self.windowOpacity())
        out.setEndValue(0.0)
        out.finished.connect(self._close_now)
        out.start()
        self._out = out

    def _close_now(self):
        self._t.stop()
        self.close()

    def start(self):
        sc = QApplication.primaryScreen()
        if sc:
            g = sc.availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        self.show()
        self._fade.start()


# ===========================================================================
# Smart search — suggestions float over the page instead of pushing it down
# ===========================================================================
LOCAL_SYMBOLS: list[tuple[str, str, str]] = [
    ("AAPL", "Apple Inc.", "NASDAQ"), ("MSFT", "Microsoft Corp.", "NASDAQ"), ("NVDA", "NVIDIA Corp.", "NASDAQ"),
    ("AMZN", "Amazon.com Inc.", "NASDAQ"), ("GOOGL", "Alphabet Inc.", "NASDAQ"), ("META", "Meta Platforms", "NASDAQ"),
    ("TSLA", "Tesla Inc.", "NASDAQ"), ("AMD", "Advanced Micro Devices", "NASDAQ"), ("NFLX", "Netflix Inc.", "NASDAQ"),
    ("INTC", "Intel Corp.", "NASDAQ"), ("JPM", "JPMorgan Chase", "NYSE"), ("V", "Visa Inc.", "NYSE"),
    ("DIS", "Walt Disney Co.", "NYSE"), ("BA", "Boeing Co.", "NYSE"), ("KO", "Coca-Cola Co.", "NYSE"),
    ("WMT", "Walmart Inc.", "NYSE"), ("TCS.NS", "Tata Consultancy Services", "NSE"), ("INFY.NS", "Infosys Ltd.", "NSE"),
    ("RELIANCE.NS", "Reliance Industries", "NSE"), ("HDFCBANK.NS", "HDFC Bank", "NSE"),
    ("ICICIBANK.NS", "ICICI Bank", "NSE"), ("SBIN.NS", "State Bank of India", "NSE"), ("WIPRO.NS", "Wipro Ltd.", "NSE"),
    ("TATAMOTORS.NS", "Tata Motors", "NSE"), ("ITC.NS", "ITC Ltd.", "NSE"), ("BHARTIARTL.NS", "Bharti Airtel", "NSE"),
    ("ADANIENT.NS", "Adani Enterprises", "NSE"), ("HINDUNILVR.NS", "Hindustan Unilever", "NSE"),
    ("LT.NS", "Larsen & Toubro", "NSE"), ("AXISBANK.NS", "Axis Bank", "NSE"), ("MARUTI.NS", "Maruti Suzuki", "NSE"),
    ("SUNPHARMA.NS", "Sun Pharma", "NSE"),
]
LOCAL_NAMES = {s: n for s, n, _ in LOCAL_SYMBOLS}


def local_search(query: str, limit: int = 8, strong_only: bool = False):
    q = query.strip().lower()
    if not q:
        return []
    scored = []
    for sym, name, exch in LOCAL_SYMBOLS:
        base, nm = sym.split(".")[0].lower(), name.lower()
        s = 0.0
        if base == q:
            s = 100
        elif base.startswith(q):
            s = 80 - (len(base) - len(q)) * 0.5
        elif q in base:
            s = 60
        elif nm.startswith(q):
            s = 55
        elif q in nm:
            s = 40
        else:
            it = iter(nm)
            if len(q) >= 3 and all(ch in it for ch in q):
                s = 20
        if s > 0 and not (strong_only and s < 40):
            scored.append((s, (sym, name, exch)))
    scored.sort(key=lambda x: (-x[0], x[1][0]))
    return [s for _, s in scored[:limit]]


class SmartSearch(QWidget):
    picked = Signal(str, str)

    def __init__(self, placeholder="Search any stock — name or symbol...", popup_width: int = 340):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.addAction(QIcon(icon_pixmap("search", 16)), QLineEdit.ActionPosition.LeadingPosition)
        lay.addWidget(self.edit)
        self.suggest = QListWidget()
        self.suggest.setObjectName("Suggest")
        self.suggest.setVisible(False)
        self.suggest.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.suggest.setCursor(Qt.CursorShape.PointingHandCursor)
        self._popup_width = popup_width
        self._token = 0
        self._local: list = []
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._ask_engine)
        self.edit.textEdited.connect(self._on_text)
        self.edit.returnPressed.connect(self._on_return)
        self.suggest.itemClicked.connect(lambda it: self._emit(*it.data(Qt.ItemDataRole.UserRole)))
        self.edit.installEventFilter(self)
        self.destroyed.connect(lambda _=None, w=self.suggest: self._drop_popup(w))

    @staticmethod
    def _drop_popup(popup):
        try:
            popup.hide()
            popup.deleteLater()
        except RuntimeError:
            pass

    def _place(self):
        win = self.window()
        if self.suggest.parent() is not win:
            self.suggest.setParent(win)
        w = max(self.edit.width(), self._popup_width)
        h = min(300, 12 + self.suggest.count() * 33)
        pos = self.edit.mapTo(win, QPoint(0, self.edit.height() + 5))
        pos.setX(max(6, min(win.width() - w - 6, pos.x())))
        if pos.y() + h > win.height() - 6:
            pos.setY(max(6, self.edit.mapTo(win, QPoint(0, 0)).y() - h - 5))
        self.suggest.setGeometry(pos.x(), pos.y(), w, h)
        self.suggest.show()
        self.suggest.raise_()

    def hide_popup(self):
        self.suggest.setVisible(False)

    def _fill(self, results):
        self.suggest.clear()
        if not results:
            self.hide_popup()
            return
        for sym, name, exch in results[:9]:
            it = QListWidgetItem(f"{sym.split('.')[0]:<11} {name[:30]}  ·  {exch}")
            it.setData(Qt.ItemDataRole.UserRole, (sym, name))
            self.suggest.addItem(it)
        self.suggest.setCurrentRow(0)
        self._place()

    def _on_text(self, text):
        self._token += 1
        self._local = local_search(text, 8)
        self._fill(self._local)
        if len(text.strip()) >= 2:
            self._debounce.start()

    def _ask_engine(self):
        q = self.edit.text().strip()
        if len(q) >= 2:
            w = SuggestWorker(self._token, q)
            w.loaded.connect(self._on_engine)
            launch(w)

    def _on_engine(self, token, hits):
        if token != self._token:
            return
        strong = local_search(self.edit.text(), 8, strong_only=True)
        merged, seen = [], set()
        for row in strong + list(hits) + list(self._local):
            if row[0] not in seen:
                seen.add(row[0])
                merged.append(row)
        if merged:
            self._fill(merged)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.edit:
            if event.type() == QEvent.Type.KeyPress and self.suggest.isVisible():
                k, row = event.key(), self.suggest.currentRow()
                if k == Qt.Key.Key_Down:
                    self.suggest.setCurrentRow(min(row + 1, self.suggest.count() - 1))
                    return True
                if k == Qt.Key.Key_Up:
                    self.suggest.setCurrentRow(max(row - 1, 0))
                    return True
                if k == Qt.Key.Key_Escape:
                    self.hide_popup()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                QTimer.singleShot(180, self._maybe_hide)
        return super().eventFilter(obj, event)

    def _maybe_hide(self):
        try:
            if not self.edit.hasFocus() and not self.suggest.underMouse():
                self.hide_popup()
        except RuntimeError:
            pass

    def _emit(self, sym, name):
        self._token += 1
        self.edit.clear()
        self.hide_popup()
        self.picked.emit(sym, name)

    def _on_return(self):
        if self.suggest.isVisible() and self.suggest.currentItem():
            self._emit(*self.suggest.currentItem().data(Qt.ItemDataRole.UserRole))
            return
        text = self.edit.text().strip()
        res = local_search(text, 1, strong_only=True)
        if res:
            self._emit(res[0][0], res[0][1])
        elif text and SYMBOL_RE.match(text.upper()):
            self._emit(text.upper(), "")


# ===========================================================================
# Sidebar
# ===========================================================================
NAV_ITEMS = [("Dashboard", "dashboard", "Market overview"), ("Scanner", "scanner", "Find setups"),
             ("Charts", "charts", "Technical charts"), ("News", "news", "Headlines")]


class NavItem(QWidget):
    clicked = Signal(int)

    def __init__(self, index: int, icon: str, label: str):
        super().__init__()
        self.index, self.icon, self.label = index, icon, label
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover, self._active = 0.0, False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.valueChanged.connect(self._set_hover)

    def _set_hover(self, v):
        self._hover = float(v)
        self.update()

    def set_active(self, on: bool):
        self._active = on
        self.update()

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e):  # noqa: N802
        self._animate(1.0)
        if self.width() < 100:
            TipBubble.show_for(self, self.label, "right")

    def leaveEvent(self, e):  # noqa: N802
        self._animate(0.0)
        TipBubble.hide_for(self)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 3, 0, -3)
        collapsed = self.width() < 100
        p.setPen(Qt.PenStyle.NoPen)
        if self._active:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0, QColor(25, 230, 195, 60))
            g.setColorAt(1, QColor(61, 169, 255, 16))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(r, 10, 10)
        elif self._hover > 0.01:
            p.setBrush(QColor(255, 255, 255, int(18 * self._hover)))
            p.drawRoundedRect(r, 10, 10)
        if self._active:
            col = QColor(Theme.ACCENT)
        else:
            a, b = QColor(Theme.TEXT_MUTED), QColor(Theme.TEXT)
            t = self._hover
            col = QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                         int(a.blue() + (b.blue() - a.blue()) * t))
        size = 20 + 1.5 * self._hover
        x = (self.width() - size) / 2 if collapsed else 13
        draw_icon(p, self.icon, QRectF(x, (self.height() - size) / 2, size, size), col, 1.7)
        if not collapsed:
            p.setFont(make_font(10.5, QFont.Weight.Bold if self._active else QFont.Weight.Medium))
            p.setPen(col)
            p.drawText(QRectF(46, 0, self.width() - 50, self.height()), Qt.AlignmentFlag.AlignVCenter, self.label)
        p.end()


class Sidebar(QWidget):
    navigate = Signal(int)
    toggled = Signal()
    FULL, MINI = 208, 64

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(self.FULL)
        self._collapsed = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 16, 12, 14)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(7)
        self.logo_dot = PulseDot(Theme.ACCENT, 16)
        self.wordmark = QLabel("CORTEX")
        self.wordmark.setObjectName("Wordmark")
        self.menu_btn = IconButton("menu", "Collapse / expand sidebar", 30, flat=True, tip_side="right")
        self.menu_btn.clicked.connect(self.toggled.emit)
        top.addWidget(self.logo_dot)
        top.addWidget(self.wordmark)
        top.addStretch(1)
        top.addWidget(self.menu_btn)
        lay.addLayout(top)
        self.tagline = QLabel("SIGNAL SCANNER")
        self.tagline.setObjectName("Tagline")
        self.tagline.setContentsMargins(23, 0, 0, 0)
        lay.addWidget(self.tagline)
        lay.addSpacing(16)
        self.items: list[NavItem] = []
        for i, (name, icon, _crumb) in enumerate(NAV_ITEMS):
            item = NavItem(i, icon, name)
            item.clicked.connect(self.navigate.emit)
            lay.addWidget(item)
            self.items.append(item)
        lay.addStretch(1)
        status = QHBoxLayout()
        status.setSpacing(7)
        self.engine_dot = PulseDot(Theme.WARN, 14)
        self.engine_lbl = QLabel("Starting engine")
        self.engine_lbl.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: 10px; font-weight: 600;")
        status.addWidget(self.engine_dot)
        status.addWidget(self.engine_lbl)
        status.addStretch(1)
        lay.addLayout(status)
        self.version = QLabel(f"v{APP_VERSION}")
        self.version.setStyleSheet(f"color: {Theme.TEXT_FAINT}; font-size: 9px;")
        self.version.setContentsMargins(21, 0, 0, 0)
        lay.addWidget(self.version)

        self.indicator = QFrame(self)
        self.indicator.setObjectName("NavIndicator")
        self.indicator.setGeometry(3, 0, 4, 20)
        self._ind_anim = QPropertyAnimation(self.indicator, b"geometry", self)
        self._ind_anim.setDuration(280)
        self._ind_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._w_anim = QVariantAnimation(self)
        self._w_anim.setDuration(240)
        self._w_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._w_anim.valueChanged.connect(lambda v: self.setFixedWidth(int(v)))
        self._w_anim.finished.connect(lambda: self.move_indicator(False))
        self._current = 0

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, on: bool, animate: bool = True):
        if on == self._collapsed:
            return
        self._collapsed = on
        for w in (self.wordmark, self.tagline, self.engine_lbl, self.version, self.logo_dot):
            w.setVisible(not on)
        target = self.MINI if on else self.FULL
        self._w_anim.stop()
        if animate and self.isVisible():
            self._w_anim.setStartValue(self.width())
            self._w_anim.setEndValue(target)
            self._w_anim.start()
        else:
            self.setFixedWidth(target)

    def set_engine(self, text: str, color: str):
        self.engine_lbl.setText(text)
        self.engine_dot.set_color(color)

    def set_current(self, index: int, animate: bool = True):
        self._current = index
        for i, item in enumerate(self.items):
            item.set_active(i == index)
        self.move_indicator(animate)

    def move_indicator(self, animate: bool = True):
        if not (0 <= self._current < len(self.items)):
            return
        g = self.items[self._current].geometry()
        target = QRect(3, g.y() + 11, 4, max(8, g.height() - 22))
        self._ind_anim.stop()
        if animate and self.isVisible():
            self._ind_anim.setStartValue(self.indicator.geometry())
            self._ind_anim.setEndValue(target)
            self._ind_anim.start()
        else:
            self.indicator.setGeometry(target)
        self.indicator.raise_()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self.move_indicator(False))


# ===========================================================================
# Watchlist — always visible, live quotes, the heart of the app
# ===========================================================================
class WatchRow(QWidget):
    activated = Signal(str)
    remove_requested = Signal(str)
    menu_requested = Signal(str, QPoint)

    def __init__(self, symbol: str, name: str = ""):
        super().__init__()
        self.symbol, self.name = symbol, name
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quote: Optional[dict] = None
        self._selected, self._hover = False, False
        self._flash, self._flash_col = 0.0, QColor(Theme.BULL)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(950)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_flash)
        self.rm = IconButton("close", "Remove from watchlist", 20, flat=True, tip_side="left")
        self.rm.setParent(self)
        self.rm.setVisible(False)
        self.rm.clicked.connect(lambda: self.remove_requested.emit(self.symbol))
        self._f_sym = make_font(10.5, QFont.Weight.Bold)
        self._f_sub = make_font(8, QFont.Weight.Medium)
        self._f_price = make_font(10, QFont.Weight.DemiBold)
        self._f_chg = make_font(8.5, QFont.Weight.Bold)

    def _set_flash(self, v):
        self._flash = float(v)
        self.update()

    def change(self) -> Optional[float]:
        if not self.quote:
            return None
        try:
            return float(self.quote.get("change_pct"))
        except (TypeError, ValueError):
            return None

    def set_quote(self, quote: Optional[dict], flash: bool = True):
        old = float(self.quote.get("price", 0) or 0) if self.quote else None
        self.quote = quote
        if quote and flash and old is not None:
            new = float(quote.get("price", 0) or 0)
            if new != old:
                self._flash_col = QColor(Theme.BULL if new > old else Theme.BEAR)
                self._anim.stop()
                self._anim.start()
        self.update()

    def set_selected(self, on: bool):
        if on != self._selected:
            self._selected = on
            self.update()

    def resizeEvent(self, e):  # noqa: N802
        self.rm.move(self.width() - 24, (self.height() - 20) // 2)

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self.rm.setVisible(True)
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self.rm.setVisible(False)
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.symbol)

    def contextMenuEvent(self, e):  # noqa: N802
        self.menu_requested.emit(self.symbol, e.globalPos())

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        p.setPen(Qt.PenStyle.NoPen)
        if self._selected:
            g = QLinearGradient(r.left(), 0, r.right(), 0)
            g.setColorAt(0, QColor(25, 230, 195, 52))
            g.setColorAt(1, QColor(61, 169, 255, 12))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(r, 9, 9)
            p.setBrush(QColor(Theme.ACCENT))
            p.drawRoundedRect(QRectF(r.left(), r.top() + 9, 3, r.height() - 18), 1.5, 1.5)
        elif self._hover:
            p.setBrush(QColor(255, 255, 255, 14))
            p.drawRoundedRect(r, 9, 9)
        if self._flash > 0.01:
            c = QColor(self._flash_col)
            c.setAlpha(int(70 * self._flash))
            p.setBrush(c)
            p.drawRoundedRect(r, 9, 9)
        left = r.left() + 12
        right = r.right() - (26 if self._hover else 10)
        base = self.symbol.split(".")[0].lstrip("^")
        p.setFont(self._f_sym)
        p.setPen(QColor(Theme.TEXT))
        p.drawText(QRectF(left, r.top() + 5, 130, 18), Qt.AlignmentFlag.AlignVCenter, base)
        exch = (self.quote or {}).get("exchange") or ("NSE" if self.symbol.endswith(".NS") else
                                                       "BSE" if self.symbol.endswith(".BO") else "")
        sub = self.name or exch
        p.setFont(self._f_sub)
        p.setPen(QColor(Theme.TEXT_FAINT))
        sub = QFontMetrics(self._f_sub).elidedText(sub, Qt.TextElideMode.ElideRight, int(max(40, right - left - 84)))
        p.drawText(QRectF(left, r.top() + 24, right - left - 80, 16), Qt.AlignmentFlag.AlignVCenter, sub)
        if self.quote:
            price = str(self.quote.get("price_label") or f"{float(self.quote.get('price', 0) or 0):,.2f}")
            chg = self.change() or 0.0
            col = QColor(Theme.BULL if chg >= 0 else Theme.BEAR)
            p.setFont(self._f_price)
            p.setPen(QColor(Theme.TEXT))
            p.drawText(QRectF(right - 120, r.top() + 5, 120, 18),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, price)
            text = f"{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%"
            p.setFont(self._f_chg)
            tw = QFontMetrics(self._f_chg).horizontalAdvance(text) + 12
            chip = QRectF(right - tw, r.top() + 25, tw, 16)
            bg = QColor(col)
            bg.setAlpha(38)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(chip, 5, 5)
            p.setPen(col)
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
        else:
            p.setFont(self._f_price)
            p.setPen(QColor(Theme.TEXT_FAINT))
            p.drawText(QRectF(right - 60, r.top() + 5, 60, 36),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "—")
        p.end()


class WatchlistPanel(QFrame):
    symbol_activated = Signal(str, str)      # symbol, name
    news_requested = Signal(str, str)
    quotes_changed = Signal(list)            # [(symbol, quote dict)]
    list_changed = Signal(str, list)         # list name, symbols
    notify = Signal(str, str)                # text, kind

    WIDTH = 296

    def __init__(self):
        super().__init__()
        self.setObjectName("WatchPanel")
        self.setFixedWidth(self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 10, 10)
        lay.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("WATCHLIST")
        title.setStyleSheet(f"font-size: 11px; font-weight: 800; letter-spacing: 2px; color: {Theme.TEXT};")
        head.addWidget(title)
        self.live = PulseDot(Theme.TEXT_FAINT, 12)
        head.addWidget(self.live)
        head.addStretch(1)
        self.refresh_btn = IconButton("refresh", "Refresh quotes now", 26, flat=True)
        self.new_btn = IconButton("plus", "New watchlist", 26, flat=True)
        self.del_btn = IconButton("trash", "Delete this watchlist", 26, flat=True)
        for b in (self.refresh_btn, self.new_btn, self.del_btn):
            head.addWidget(b)
        lay.addLayout(head)

        self.combo = QComboBox()
        lay.addWidget(self.combo)
        self.search = SmartSearch("Add a stock to this list…", popup_width=self.WIDTH - 24)
        lay.addWidget(self.search)
        self.sort = SegmentedControl(["My order", "% Change", "A–Z"], 0)
        self.sort.on_change(lambda _i: self._rebuild())
        lay.addWidget(self.sort)

        self.rows_host = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_host)
        self.rows_lay.setContentsMargins(0, 0, 4, 0)
        self.rows_lay.setSpacing(1)
        lay.addWidget(scroll_page(self.rows_host), 1)
        self.foot = QLabel("Quotes load when the engine is ready.")
        self.foot.setObjectName("Mini")
        self.foot.setWordWrap(True)
        lay.addWidget(self.foot)

        self.lists: dict[str, list[str]] = {"My Watchlist": list(DEFAULT_WATCHLIST)}
        self.active = "My Watchlist"
        self.names: dict[str, str] = dict(LOCAL_NAMES)
        self.quotes: dict[str, dict] = {}
        self.rows: dict[str, WatchRow] = {}
        self._selected = ""
        self._busy = False
        self._pending = False
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.refresh_quotes)

        self.combo.activated.connect(lambda _i: self.set_active(self.combo.currentText()))
        self.search.picked.connect(self.add_symbol)
        self.refresh_btn.clicked.connect(self.refresh_quotes)
        self.new_btn.clicked.connect(self.new_list)
        self.del_btn.clicked.connect(self.delete_list)
        self._sync_combo()
        self._rebuild()

    # --- state
    def load_state(self):
        state = ui_state()
        lists = state.get("watchlists")
        clean: dict[str, list[str]] = {}
        if isinstance(lists, dict):
            for name, symbols in lists.items():
                if isinstance(symbols, list):
                    clean[str(name)] = [str(s).upper() for s in symbols if str(s).strip()]
        if not clean:
            legacy = state.get("watchlist_symbols")
            clean = {"My Watchlist": [str(s).upper() for s in legacy] if isinstance(legacy, list) and legacy
                     else list(DEFAULT_WATCHLIST)}
        self.lists = clean
        active = str(state.get("active_watchlist") or "")
        self.active = active if active in clean else next(iter(clean))
        names = state.get("symbol_names")
        if isinstance(names, dict):
            self.names.update({str(k): str(v) for k, v in names.items()})
        self._sync_combo()
        self._rebuild()
        self.list_changed.emit(self.active, self.symbols())

    def save_state(self):
        ui_state_update(watchlists=self.lists, active_watchlist=self.active,
                        watchlist_symbols=self.symbols(),
                        symbol_names={k: v for k, v in self.names.items() if k not in LOCAL_NAMES})

    def symbols(self) -> list[str]:
        return list(self.lists.get(self.active, []))

    def all_symbols(self) -> list[str]:
        out: list[str] = []
        for symbols in self.lists.values():
            for s in symbols:
                if s not in out:
                    out.append(s)
        return out

    def name_of(self, symbol: str) -> str:
        return self.names.get(symbol, "")

    def _sync_combo(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems(list(self.lists))
        self.combo.setCurrentText(self.active)
        self.combo.blockSignals(False)
        self.del_btn.setEnabled(len(self.lists) > 1)

    # --- list operations
    def set_active(self, name: str):
        if name in self.lists and name != self.active:
            self.active = name
            self._sync_combo()
            self._rebuild()
            self.save_state()
            self.list_changed.emit(self.active, self.symbols())
            self.refresh_quotes()

    def new_list(self, name: Optional[str] = None):
        if not isinstance(name, str) or not name:
            name, ok = QInputDialog.getText(self, "New watchlist", "Name:")
            if not ok:
                return
        name = str(name).strip()[:28]
        if not name or name in self.lists:
            self.notify.emit("A watchlist with that name already exists." if name else "Name can't be empty.", "warn")
            return
        self.lists[name] = []
        self.active = name
        self._sync_combo()
        self._rebuild()
        self.save_state()
        self.list_changed.emit(self.active, self.symbols())
        self.notify.emit(f"Created watchlist “{name}”", "ok")

    def delete_list(self):
        if len(self.lists) <= 1:
            return
        gone = self.active
        del self.lists[gone]
        self.active = next(iter(self.lists))
        self._sync_combo()
        self._rebuild()
        self.save_state()
        self.list_changed.emit(self.active, self.symbols())
        self.notify.emit(f"Deleted watchlist “{gone}”", "info")
        self.refresh_quotes()

    def add_symbol(self, symbol: str, name: str = ""):
        symbol = symbol.strip().upper()
        if not symbol:
            return
        if name and name.upper() != symbol:
            self.names[symbol] = name
        current = self.lists.setdefault(self.active, [])
        if symbol in current:
            self.notify.emit(f"{symbol.split('.')[0]} is already in this list.", "info")
            return
        current.append(symbol)
        self._rebuild()
        self.save_state()
        self.list_changed.emit(self.active, self.symbols())
        self.notify.emit(f"Added {symbol.split('.')[0]} to {self.active}", "ok")
        self.refresh_quotes()

    def remove_symbol(self, symbol: str):
        current = self.lists.get(self.active, [])
        if symbol in current:
            current.remove(symbol)
            self._rebuild()
            self.save_state()
            self.list_changed.emit(self.active, self.symbols())

    def move_symbol(self, symbol: str, delta: int):
        current = self.lists.get(self.active, [])
        if symbol not in current:
            return
        i = current.index(symbol)
        j = max(0, min(len(current) - 1, i + delta))
        if i != j:
            current.insert(j, current.pop(i))
            self.sort.set_index(0)
            self.save_state()
            self.list_changed.emit(self.active, self.symbols())

    def set_selected(self, symbol: str):
        self._selected = symbol
        for sym, row in self.rows.items():
            row.set_selected(sym == symbol)

    # --- rows
    def _quote_for(self, symbol: str) -> Optional[dict]:
        q = self.quotes.get(symbol)
        if q is None:
            base = symbol.split(".")[0]
            for key, value in self.quotes.items():
                if key.split(".")[0] == base:
                    return value
        return q

    def _rebuild(self):
        clear_layout(self.rows_lay)
        self.rows = {}
        symbols = self.symbols()
        mode = self.sort.index()
        if mode == 1:
            symbols.sort(key=lambda s: -(float((self._quote_for(s) or {}).get("change_pct", -1e9) or 0)))
        elif mode == 2:
            symbols.sort()
        if not symbols:
            empty = QLabel("This list is empty.\nSearch above to add your first stock.")
            empty.setObjectName("Empty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows_lay.addWidget(empty)
        for sym in symbols:
            row = WatchRow(sym, self.name_of(sym))
            row.set_quote(self._quote_for(sym), flash=False)
            row.set_selected(sym == self._selected)
            row.activated.connect(lambda s: self.symbol_activated.emit(s, self.name_of(s)))
            row.remove_requested.connect(self.remove_symbol)
            row.menu_requested.connect(self._row_menu)
            self.rows_lay.addWidget(row)
            self.rows[sym] = row
        self.rows_lay.addStretch(1)

    def _row_menu(self, symbol: str, pos: QPoint):
        menu = QMenu(self)
        menu.addAction("Open chart", lambda: self.symbol_activated.emit(symbol, self.name_of(symbol)))
        menu.addAction("Show news", lambda: self.news_requested.emit(symbol, self.name_of(symbol)))
        menu.addSeparator()
        menu.addAction("Move up", lambda: self.move_symbol(symbol, -1))
        menu.addAction("Move down", lambda: self.move_symbol(symbol, 1))
        menu.addSeparator()
        menu.addAction("Remove from watchlist", lambda: self.remove_symbol(symbol))
        menu.exec(pos)

    # --- quotes
    def start_auto(self):
        self._timer.start()
        self.refresh_quotes()

    def refresh_quotes(self):
        symbols = self.symbols()
        if not symbols:
            return
        if self._busy:
            self._pending = True      # a refresh is in flight; run again when it lands
            return
        self._busy = True
        self.foot.setText("Refreshing quotes…")
        w = QuoteWorker(symbols[:60])
        w.loaded.connect(self._on_quotes)
        launch(w)

    def _on_quotes(self, quotes: dict):
        self._busy = False
        if self._pending:
            self._pending = False
            QTimer.singleShot(0, self.refresh_quotes)
        if not quotes:
            self.live.set_color(Theme.BEAR)
            self.foot.setText("Quotes unavailable — check your internet connection.")
            return
        self.quotes.update(quotes)
        self.live.set_color(Theme.BULL)
        for sym, row in self.rows.items():
            row.set_quote(self._quote_for(sym))
        if self.sort.index() == 1:
            self._rebuild()
        self.foot.setText(f"Updated {datetime.now().strftime('%H:%M:%S')} · refreshes every minute")
        self.quotes_changed.emit([(s, self._quote_for(s)) for s in self.symbols() if self._quote_for(s)])


# ===========================================================================
# Dashboard
# ===========================================================================
class IndexCard(GlowCard):
    clicked = Signal(str)

    def __init__(self, name: str, symbol: str):
        super().__init__("Card")
        self.symbol = symbol
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(92)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(13, 10, 13, 8)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.name = QLabel(name)
        self.name.setObjectName("CardLabel")
        top.addWidget(self.name)
        top.addStretch(1)
        self.chg = QLabel("")
        top.addWidget(self.chg)
        lay.addLayout(top)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 17px; font-weight: 800;")
        lay.addWidget(self.value)
        self.spark = Sparkline(28)
        lay.addWidget(self.spark, 1)

    def set_quote(self, value: float, chg: float, series: list[float]):
        col = Theme.BULL if chg >= 0 else Theme.BEAR
        self.value.setText(f"{value:,.2f}")
        self.chg.setText(f"{'▲' if chg >= 0 else '▼'} {abs(chg):.2f}%")
        self.chg.setStyleSheet(f"color: {col}; font-size: 11px; font-weight: 800;")
        self.spark.set_data(series, col)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.symbol)


class MoverList(QWidget):
    """Painted list of movers with magnitude bars; rows are clickable."""

    clicked = Signal(str)
    ROW = 30

    def __init__(self, positive: bool):
        super().__init__()
        self._pos = positive
        self._rows: list[tuple[str, float]] = []
        self._grow = 0.0
        self._hover = -1
        self._anim = None
        self.setMouseTracking(True)
        self.setMinimumHeight(self.ROW * 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_rows(self, rows: list[tuple[str, float]]):
        self._rows = rows[:4]
        a = QVariantAnimation(self)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setDuration(600)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.valueChanged.connect(self._set_grow)
        a.start()
        self._anim = a

    def _set_grow(self, v):
        self._grow = float(v)
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        self._hover = int(e.position().y() // self.ROW)
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        i = int(e.position().y() // self.ROW)
        if 0 <= i < len(self._rows):
            self.clicked.emit(self._rows[i][0])

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        if not self._rows:
            p.setFont(make_font(9))
            p.setPen(QColor(Theme.TEXT_FAINT))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No data yet")
            p.end()
            return
        col = QColor(Theme.BULL if self._pos else Theme.BEAR)
        mx = max(abs(c) for _, c in self._rows) or 1
        for i, (sym, chg) in enumerate(self._rows):
            y = i * self.ROW
            if i == self._hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 14))
                p.drawRoundedRect(QRectF(0, y + 1, w, self.ROW - 2), 7, 7)
            p.setFont(make_font(9.5, QFont.Weight.Bold))
            p.setPen(QColor(Theme.TEXT))
            p.drawText(QRectF(8, y, 92, self.ROW), Qt.AlignmentFlag.AlignVCenter, sym.split(".")[0][:11])
            bar_max = max(10.0, w - 100 - 66)
            bw = max(3.0, bar_max * abs(chg) / mx * self._grow)
            g = QLinearGradient(100, 0, 100 + bw, 0)
            c0 = QColor(col)
            c0.setAlpha(70)
            g.setColorAt(0, c0)
            g.setColorAt(1, col)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(100, y + 10, bw, self.ROW - 20), 5, 5)
            p.setFont(make_font(9, QFont.Weight.Bold))
            p.setPen(col)
            p.drawText(QRectF(w - 64, y, 58, self.ROW), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{chg:+.2f}%")
        p.end()


class BreadthBar(QWidget):
    """Advancers vs decliners in the watchlist, as one proportional bar."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(40)
        self._adv, self._dec, self._grow = 0, 0, 0.0
        self._anim = None

    def set_values(self, adv: int, dec: int):
        self._adv, self._dec = adv, dec
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
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, total = self.width(), self._adv + self._dec
        p.setFont(make_font(8.5, QFont.Weight.Bold))
        if not total:
            p.setPen(QColor(Theme.TEXT_FAINT))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, "Breadth appears once quotes load")
            p.end()
            return
        p.setPen(QColor(Theme.BULL))
        p.drawText(QRectF(0, 0, w / 2, 16), Qt.AlignmentFlag.AlignVCenter, f"{self._adv} advancing")
        p.setPen(QColor(Theme.BEAR))
        p.drawText(QRectF(w / 2, 0, w / 2, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"{self._dec} declining")
        split = w * self._adv / total
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(Theme.BORDER))
        p.drawRoundedRect(QRectF(0, 21, w, 8), 4, 4)
        if self._adv:
            p.setBrush(QColor(Theme.BULL))
            p.drawRoundedRect(QRectF(0, 21, max(4.0, (split - 2) * self._grow), 8), 4, 4)
        if self._dec:
            dw = max(4.0, (w - split - 2) * self._grow)
            p.setBrush(QColor(Theme.BEAR))
            p.drawRoundedRect(QRectF(w - dw, 21, dw, 8), 4, 4)
        p.end()


class HeadlineRow(QFrame):
    def __init__(self, item: dict, when: Optional[datetime]):
        super().__init__()
        self._link = str(item.get("link", "") or "")
        if self._link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        col = SENTIMENT_COLORS.get(str(item.get("sentiment_tag", "")).lower(), Theme.TEXT_MUTED)
        self.setStyleSheet("QFrame:hover { background: rgba(255,255,255,0.045); border-radius: 8px; }")
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(10)
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {col}; border-radius: 1px;")
        h.addWidget(bar)
        col_lay = QVBoxLayout()
        col_lay.setSpacing(1)
        title = QLabel(str(item.get("title", "")))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 12px; font-weight: 600; background: transparent;")
        stamp = when.strftime("%d %b · %H:%M") if when else str(item.get("published_label", ""))
        meta = QLabel(f"{item.get('source', '')}  ·  {stamp}")
        meta.setStyleSheet(f"color: {Theme.TEXT_FAINT}; font-size: 10px; background: transparent;")
        col_lay.addWidget(title)
        col_lay.addWidget(meta)
        h.addLayout(col_lay, 1)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._link and e.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl(self._link))


def news_when(item: dict) -> Optional[datetime]:
    raw = str(item.get("published_at", "") or "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def sort_news(items: list) -> list[tuple[Optional[datetime], dict]]:
    """Newest first; undated items go last."""
    dated = [(news_when(i), i) for i in items]
    dated.sort(key=lambda x: (x[0] is not None, x[0] or datetime.min), reverse=True)
    return dated


class DashboardView(QWidget):
    go_scan = Signal()
    go_news = Signal()
    open_symbol = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 9, 12)
        lay.setSpacing(12)

        idx = QHBoxLayout()
        idx.setSpacing(10)
        self.index_cards: dict[str, IndexCard] = {}
        for name, sym in INDEX_SYMBOLS:
            card = IndexCard(name, sym)
            card.clicked.connect(self.open_symbol.emit)
            self.index_cards[name] = card
            idx.addWidget(card)
        lay.addLayout(idx)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        # featured symbol
        self.feature = AuroraBanner(218)
        fl = QVBoxLayout(self.feature)
        fl.setContentsMargins(20, 15, 20, 12)
        fl.setSpacing(4)
        frow = QHBoxLayout()
        frow.setSpacing(10)
        fcol = QVBoxLayout()
        fcol.setSpacing(0)
        tag = QLabel("IN FOCUS")
        tag.setStyleSheet(f"color: {Theme.ACCENT}; font-size: 9px; letter-spacing: 3px; font-weight: 800;")
        self.f_symbol = QLabel("—")
        self.f_symbol.setStyleSheet("font-size: 24px; font-weight: 800;")
        self.f_name = QLabel("Pick a stock from your watchlist")
        self.f_name.setObjectName("Body")
        fcol.addWidget(tag)
        fcol.addWidget(self.f_symbol)
        fcol.addWidget(self.f_name)
        frow.addLayout(fcol)
        frow.addStretch(1)
        pcol = QVBoxLayout()
        pcol.setSpacing(0)
        self.f_price = QLabel("")
        self.f_price.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.f_price.setStyleSheet("font-size: 24px; font-weight: 800;")
        self.f_chg = QLabel("")
        self.f_chg.setAlignment(Qt.AlignmentFlag.AlignRight)
        pcol.addWidget(self.f_price)
        pcol.addWidget(self.f_chg)
        frow.addLayout(pcol)
        fl.addLayout(frow)
        self.f_spark = Sparkline(96, 2.2)
        fl.addWidget(self.f_spark, 1)
        fbtn = QHBoxLayout()
        fbtn.setSpacing(8)
        self.f_range = QLabel("")
        self.f_range.setObjectName("Mini")
        fbtn.addWidget(self.f_range)
        fbtn.addStretch(1)
        b_chart = QPushButton("Open chart")
        b_chart.setObjectName("Primary")
        b_news = QPushButton("News")
        b_news.setObjectName("Ghost")
        b_scan = QPushButton("Run a scan")
        b_scan.setObjectName("Ghost")
        for b in (b_scan, b_news, b_chart):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            fbtn.addWidget(b)
        b_chart.clicked.connect(lambda: self.open_symbol.emit(self._featured) if self._featured else None)
        b_news.clicked.connect(self.go_news.emit)
        b_scan.clicked.connect(self.go_scan.emit)
        fl.addLayout(fbtn)
        mid.addWidget(self.feature, 3)

        movers = QFrame()
        movers.setObjectName("Panel")
        ml = QVBoxLayout(movers)
        ml.setContentsMargins(16, 13, 16, 12)
        ml.setSpacing(4)
        mt = QLabel("Watchlist movers")
        mt.setObjectName("SectionTitle")
        ml.addWidget(mt)
        ml.addWidget(group_label("Top gainers"))
        self.gainers = MoverList(True)
        self.gainers.clicked.connect(self.open_symbol.emit)
        ml.addWidget(self.gainers)
        ml.addWidget(group_label("Top losers"))
        self.losers = MoverList(False)
        self.losers.clicked.connect(self.open_symbol.emit)
        ml.addWidget(self.losers)
        ml.addWidget(divider())
        self.breadth_title = group_label("Watchlist breadth")
        ml.addWidget(self.breadth_title)
        self.breadth = BreadthBar()
        ml.addWidget(self.breadth)
        ml.addStretch(1)
        mid.addWidget(movers, 2)
        lay.addLayout(mid)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.cards = {"scanned": StatCard("Last scan · symbols"), "qualified": StatCard("Qualified", Theme.ACCENT),
                      "passrate": StatCard("Pass rate"), "best": StatCard("Top score", Theme.GOLD)}
        for c in self.cards.values():
            row.addWidget(c)
        lay.addLayout(row)

        grid = QHBoxLayout()
        grid.setSpacing(12)
        sig = QFrame()
        sig.setObjectName("Panel")
        sg = QVBoxLayout(sig)
        sg.setContentsMargins(16, 13, 16, 12)
        sg.setSpacing(7)
        head = QHBoxLayout()
        st = QLabel("Top signals")
        st.setObjectName("SectionTitle")
        head.addWidget(st)
        head.addStretch(1)
        hint = QLabel("double-click to chart")
        hint.setObjectName("Mini")
        head.addWidget(hint)
        sg.addLayout(head)
        self.sig_stack = QStackedWidget()
        empty = QLabel("No scan yet this session.\nRun a scan and the strongest setups land here.")
        empty.setObjectName("Empty")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sig_table = make_table(["Symbol", "Price", "Chg %", "RVOL", "Score"], 28)
        self.sig_table.setMinimumHeight(200)
        self.sig_table.cellDoubleClicked.connect(self._open_sig)
        self.sig_stack.addWidget(empty)
        self.sig_stack.addWidget(self.sig_table)
        sg.addWidget(self.sig_stack, 1)
        grid.addWidget(sig, 3)

        news = QFrame()
        news.setObjectName("Panel")
        nl = QVBoxLayout(news)
        nl.setContentsMargins(16, 13, 12, 10)
        nl.setSpacing(4)
        nh = QHBoxLayout()
        self.news_title = QLabel("Latest headlines")
        self.news_title.setObjectName("SectionTitle")
        nh.addWidget(self.news_title)
        nh.addStretch(1)
        more = QPushButton("All news")
        more.setObjectName("Tool")
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(self.go_news.emit)
        nh.addWidget(more)
        nl.addLayout(nh)
        self.news_box = QVBoxLayout()
        self.news_box.setSpacing(1)
        nl.addLayout(self.news_box)
        nl.addStretch(1)
        grid.addWidget(news, 3)

        side = QVBoxLayout()
        side.setSpacing(12)
        sec = QFrame()
        sec.setObjectName("Panel")
        sl = QVBoxLayout(sec)
        sl.setContentsMargins(16, 13, 16, 12)
        sl.setSpacing(7)
        s1 = QLabel("Sector mix")
        s1.setObjectName("SectionTitle")
        sl.addWidget(s1)
        self.sector_empty = QLabel("Appears after a scan.")
        self.sector_empty.setObjectName("Mini")
        sl.addWidget(self.sector_empty)
        self.sector_bars = BarList()
        self.sector_bars.setVisible(False)
        sl.addWidget(self.sector_bars)
        sl.addStretch(1)
        side.addWidget(sec, 1)
        hist = QFrame()
        hist.setObjectName("Panel")
        hl = QVBoxLayout(hist)
        hl.setContentsMargins(16, 13, 16, 12)
        hl.setSpacing(5)
        s2 = QLabel("Recent scans")
        s2.setObjectName("SectionTitle")
        hl.addWidget(s2)
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(3)
        hl.addLayout(self.history_box)
        hl.addStretch(1)
        side.addWidget(hist, 1)
        grid.addLayout(side, 2)
        lay.addLayout(grid, 1)
        outer.addWidget(scroll_page(inner))
        self._featured = ""
        self.set_headlines("", [])

    # --- data in
    def set_indices(self, quotes: list):
        for name, value, chg, series in quotes:
            card = self.index_cards.get(name)
            if card:
                card.set_quote(value, chg, series)

    def set_featured(self, symbol: str, name: str, candles: list[Candle]):
        self._featured = symbol
        self.f_symbol.setText(symbol.split(".")[0].lstrip("^"))
        self.f_name.setText(name or symbol)
        if not candles:
            self.f_price.setText("")
            self.f_chg.setText("")
            return
        last = candles[-1].c
        prev = candles[-2].c if len(candles) > 1 else last
        chg = (last - prev) / prev * 100 if prev else 0.0
        col = Theme.BULL if chg >= 0 else Theme.BEAR
        self.f_price.setText(f"{last:,.2f}")
        self.f_chg.setText(f"{'▲' if chg >= 0 else '▼'} {abs(last - prev):,.2f}  ({abs(chg):.2f}%)")
        self.f_chg.setStyleSheet(f"color: {col}; font-size: 12px; font-weight: 800;")
        window = candles[-120:]
        trend = Theme.BULL if window[-1].c >= window[0].c else Theme.BEAR
        self.f_spark.set_data([c.c for c in window], trend)
        hi, lo = max(c.h for c in window), min(c.l for c in window)
        self.f_range.setText(f"{len(window)}-bar range   L {lo:,.2f}   H {hi:,.2f}")

    def set_movers(self, quotes: list):
        rows = []
        for sym, q in quotes:
            try:
                rows.append((sym, float(q.get("change_pct"))))
            except (TypeError, ValueError, AttributeError):
                continue
        ups = sorted([r for r in rows if r[1] > 0], key=lambda r: -r[1])
        downs = sorted([r for r in rows if r[1] < 0], key=lambda r: r[1])
        self.gainers.set_rows(ups)
        self.losers.set_rows(downs)
        avg = sum(c for _s, c in rows) / len(rows) if rows else 0.0
        self.breadth.set_values(len(ups), len(downs))
        self.breadth_title.setText(f"WATCHLIST BREADTH  ·  AVG {avg:+.2f}%" if rows else "WATCHLIST BREADTH")

    def set_headlines(self, symbol: str, items: list):
        clear_layout(self.news_box)
        base = symbol.split(".")[0].lstrip("^")
        self.news_title.setText(f"Latest headlines · {base}" if base else "Latest headlines")
        if not items:
            l = QLabel("Headlines for the stock in focus appear here." if not symbol
                       else f"No headlines found for {base}.")
            l.setObjectName("Mini")
            l.setWordWrap(True)
            self.news_box.addWidget(l)
            return
        for when, item in sort_news(items)[:4]:
            self.news_box.addWidget(HeadlineRow(item, when))

    def _open_sig(self, row, _c):
        it = self.sig_table.item(row, 0)
        if it:
            self.open_symbol.emit(str(it.data(Qt.ItemDataRole.UserRole) or it.text()))

    def load_history(self):
        history = list(ui_state().get("scan_history", []) or [])
        self._render_history(history)
        if history:
            last = history[0]
            self.cards["scanned"].value.animate_to(float(last.get("symbols_scanned", 0) or 0))
            self.cards["scanned"].hint.setText(str(last.get("scope", "last scan")))
            self.cards["qualified"].value.animate_to(float(last.get("qualified", 0) or 0))
            self.cards["qualified"].hint.setText("above your threshold")
            self.cards["passrate"].value.fmt = "{:.2f}%"
            self.cards["passrate"].value.animate_to(float(last.get("pass_rate", 0) or 0))
            self.cards["passrate"].hint.setText("qualified / scanned")
            self.cards["best"].hint.setText("run a scan this session")
        else:
            for key, text in (("scanned", "run your first scan"), ("qualified", "above your threshold"),
                              ("passrate", "qualified / scanned"), ("best", "best confluence score")):
                self.cards[key].hint.setText(text)

    def _render_history(self, history):
        clear_layout(self.history_box)
        if not history:
            l = QLabel("No scans recorded yet.")
            l.setObjectName("Mini")
            self.history_box.addWidget(l)
            return
        for h in history[:4]:
            text = (f"<span style='color:{Theme.TEXT}; font-weight:600'>{h.get('scope', 'Scan')}</span><br>"
                    f"<span style='color:{Theme.TEXT_FAINT}'>{str(h.get('timestamp', ''))[:16]} · "
                    f"{int(h.get('symbols_scanned', 0) or 0):,} scanned · "
                    f"<span style='color:{Theme.ACCENT}'>{h.get('qualified', 0)} qualified</span></span>")
            l = QLabel(text)
            l.setTextFormat(Qt.TextFormat.RichText)
            l.setStyleSheet("font-size: 10px; padding: 2px 0;")
            l.setWordWrap(True)
            self.history_box.addWidget(l)

    def update_from_scan(self, summary: dict, results: list[dict]):
        self.cards["scanned"].value.animate_to(summary.get("scanned", 0))
        self.cards["scanned"].hint.setText(summary.get("scope", ""))
        self.cards["qualified"].value.animate_to(summary.get("qualified", 0))
        self.cards["passrate"].value.fmt = "{:.2f}%"
        self.cards["passrate"].value.animate_to(summary.get("pass_rate", 0.0))
        ranked = sorted(results, key=lambda r: float(r.get("confluence_percent", 0) or 0), reverse=True)
        if ranked:
            self.cards["best"].value.fmt = "{:.0f}%"
            self.cards["best"].value.animate_to(float(ranked[0].get("confluence_percent", 0) or 0))
            self.cards["best"].hint.setText(str(ranked[0].get("ticker", "")).split(".")[0])
        else:
            self.cards["best"].value.set_text("—")
            self.cards["best"].hint.setText("nothing qualified")
        top = ranked[:7]
        self.sig_table.setRowCount(len(top))
        for r, res in enumerate(top):
            sym = str(res.get("ticker", ""))
            chg = float(res.get("change_pct", 0) or 0)
            a = QTableWidgetItem(sym.split(".")[0])
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            a.setData(Qt.ItemDataRole.UserRole, sym)
            cells = [a, QTableWidgetItem(str(res.get("price_label") or res.get("price", ""))),
                     NumItem(f"{chg:+.2f}%", chg),
                     NumItem(f"{float(res.get('rvol', 0) or 0):.1f}×", float(res.get('rvol', 0) or 0)),
                     NumItem(f"{float(res.get('confluence_percent', 0) or 0):.0f}%",
                             float(res.get('confluence_percent', 0) or 0))]
            cells[1].setForeground(QColor(Theme.TEXT_MUTED))
            cells[2].setForeground(QColor(Theme.BULL if chg >= 0 else Theme.BEAR))
            cells[4].setForeground(QColor(Theme.ACCENT))
            for c, it in enumerate(cells):
                self.sig_table.setItem(r, c, it)
        self.sig_stack.setCurrentIndex(1 if top else 0)
        counts: dict[str, int] = {}
        for res in results:
            sector = str(res.get("sector") or "").strip()
            if sector:
                counts[sector] = counts.get(sector, 0) + 1
        rows = sorted(counts.items(), key=lambda x: -x[1])
        self.sector_bars.setVisible(bool(rows))
        self.sector_empty.setVisible(not rows)
        if rows:
            self.sector_bars.set_rows(rows)
        else:
            self.sector_empty.setText("No sector data for this scan.")
        self._render_history(list(ui_state().get("scan_history", []) or []))


# ===========================================================================
# Scanner
# ===========================================================================
EXCHANGES = ["NASDAQ", "NYSE", "NSE", "BSE", "F&O"]
PRESETS = ["NIFTY 50", "Nifty Bank", "S&P 500 Large Caps", "Magnificent 7", "Pharma Stocks", "F&O stocks (NSE)"]
RESULT_HEADERS = ["Symbol", "Company", "Price", "Chg %", "RVOL", "RSI", "Score", "Trend"]
RULE_FIELDS = [  # key, label, kind, min, max, step
    ("bb_length", "Bollinger length", "int", 5, 100, 1),
    ("bb_std", "Bollinger deviation", "float", 0.5, 5.0, 0.1),
    ("macd_fast", "MACD fast", "int", 2, 50, 1),
    ("macd_slow", "MACD slow", "int", 5, 100, 1),
    ("macd_signal", "MACD signal", "int", 2, 50, 1),
    ("rsi_period", "RSI period", "int", 2, 50, 1),
    ("volume_multiplier", "Volume surge ×", "float", 0.5, 10.0, 0.05),
]
RULE_DEFAULTS = {"bb_length": 20, "bb_std": 2.0, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                 "rsi_period": 14, "volume_multiplier": 1.25}


class ModeCard(QFrame):
    chosen = Signal(int)

    def __init__(self, index: int, icon: str, title: str, desc: str):
        super().__init__()
        self.setObjectName("ModeCard")
        self.index, self.icon = index, icon
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(62)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 8, 8, 8)
        lay.setSpacing(1)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 12px; font-weight: 800;")
        d = QLabel(desc)
        d.setObjectName("Mini")
        lay.addWidget(self.title)
        lay.addWidget(d)
        self.set_active(False)

    def set_active(self, on: bool):
        self._active = on
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        p = QPainter(self)
        draw_icon(p, self.icon, QRectF(11, (self.height() - 20) / 2, 20, 20),
                  QColor(Theme.ACCENT if self._active else Theme.TEXT_MUTED), 1.7)
        p.end()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit(self.index)


class ScannerView(QWidget):
    open_chart = Signal(str)
    scan_state = Signal(str)
    scan_finished = Signal(dict, list)
    notify = Signal(str, str)

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        cfg = QFrame()
        cfg.setObjectName("Panel")
        cfg.setFixedWidth(336)
        outer = QVBoxLayout(cfg)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(9)
        body = QWidget()
        c = QVBoxLayout(body)
        c.setContentsMargins(16, 15, 12, 6)
        c.setSpacing(8)

        c.addWidget(group_label("1 · What to scan"))
        grid = QGridLayout()
        grid.setSpacing(7)
        self.mode_cards: list[ModeCard] = []
        for i, (icon, title, desc) in enumerate((("watch", "Watchlist", "Your active list"),
                                                 ("news", "Preset list", "NIFTY 50, S&P…"),
                                                 ("text", "Custom", "Type any symbols"),
                                                 ("scanner", "Exchange", "Full sweep"))):
            card = ModeCard(i, icon, title, desc)
            card.chosen.connect(self.set_mode)
            self.mode_cards.append(card)
            grid.addWidget(card, i // 2, i % 2)
        c.addLayout(grid)

        self.mode_stack = QStackedWidget()
        # watchlist
        w0 = QWidget()
        w0.setObjectName("Clear")
        l0 = QVBoxLayout(w0)
        l0.setContentsMargins(0, 2, 0, 0)
        l0.setSpacing(5)
        self.watch_info = QLabel("")
        self.watch_info.setWordWrap(True)
        self.watch_info.setStyleSheet("font-size: 12px; font-weight: 600;")
        l0.addWidget(self.watch_info)
        self.watch_syms = QLabel("")
        self.watch_syms.setObjectName("Mini")
        self.watch_syms.setWordWrap(True)
        l0.addWidget(self.watch_syms)
        hint0 = QLabel("Add or remove stocks in the Watchlist panel on the right.")
        hint0.setObjectName("Mini")
        hint0.setWordWrap(True)
        l0.addWidget(hint0)
        l0.addStretch(1)
        self.mode_stack.addWidget(w0)
        # preset
        w1 = QWidget()
        w1.setObjectName("Clear")
        l1 = QVBoxLayout(w1)
        l1.setContentsMargins(0, 2, 0, 0)
        l1.setSpacing(6)
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        l1.addWidget(self.preset)
        self.preset_hint = QLabel("")
        self.preset_hint.setObjectName("Mini")
        l1.addWidget(self.preset_hint)
        self.tweak_btn = QPushButton("Tweak these symbols  →")
        self.tweak_btn.setObjectName("Ghost")
        self.tweak_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tweak_btn.clicked.connect(self._preset_to_custom)
        l1.addWidget(self.tweak_btn)
        l1.addStretch(1)
        self.mode_stack.addWidget(w1)
        # custom
        w2 = QWidget()
        w2.setObjectName("Clear")
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 2, 0, 0)
        l2.setSpacing(6)
        self.custom = QPlainTextEdit()
        self.custom.setPlaceholderText("Type or paste symbols, separated by spaces, commas or new lines.\n"
                                       "Examples:  RELIANCE.NS, TCS.NS, AAPL, NVDA")
        self.custom.setFixedHeight(104)
        self.custom.textChanged.connect(self._custom_count)
        l2.addWidget(self.custom)
        crow = QHBoxLayout()
        crow.setSpacing(5)
        self.custom_count = QLabel("0 symbols")
        self.custom_count.setObjectName("Mini")
        crow.addWidget(self.custom_count)
        crow.addStretch(1)
        b_w = QPushButton("+ Watchlist")
        b_c = QPushButton("Clear")
        for b in (b_w, b_c):
            b.setObjectName("Tool")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            crow.addWidget(b)
        b_w.clicked.connect(lambda: self._append_custom(self._watch))
        b_c.clicked.connect(self.custom.clear)
        l2.addLayout(crow)
        l2.addStretch(1)
        self.mode_stack.addWidget(w2)
        # exchange
        w3 = QWidget()
        w3.setObjectName("Clear")
        l3 = QVBoxLayout(w3)
        l3.setContentsMargins(0, 2, 0, 0)
        l3.setSpacing(4)
        eg = QGridLayout()
        eg.setSpacing(3)
        self.exch_boxes: dict[str, QCheckBox] = {}
        for i, ex in enumerate(EXCHANGES):
            b = QCheckBox(ex.replace("&", "&&"))
            b.setChecked(ex == "NSE")
            self.exch_boxes[ex] = b
            eg.addWidget(b, i // 2, i % 2)
        l3.addLayout(eg)
        allrow = QHBoxLayout()
        allrow.setSpacing(5)
        b_all = QPushButton("Select all")
        b_none = QPushButton("Clear")
        for b in (b_all, b_none):
            b.setObjectName("Tool")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            allrow.addWidget(b)
        allrow.addStretch(1)
        b_all.clicked.connect(lambda: [b.setChecked(True) for b in self.exch_boxes.values()])
        b_none.clicked.connect(lambda: [b.setChecked(False) for b in self.exch_boxes.values()])
        l3.addLayout(allrow)
        warn = QLabel("Full sweeps cover thousands of symbols and take several minutes.")
        warn.setObjectName("Mini")
        warn.setWordWrap(True)
        l3.addWidget(warn)
        l3.addStretch(1)
        self.mode_stack.addWidget(w3)
        c.addWidget(self.mode_stack)

        c.addWidget(divider())
        c.addWidget(group_label("2 · Signal rules"))
        srow = QHBoxLayout()
        self.confluence = QSlider(Qt.Orientation.Horizontal)
        self.confluence.setRange(40, 100)
        self.confluence.setValue(60)
        self.conf_lbl = QLabel("60%")
        self.conf_lbl.setStyleSheet(f"color: {Theme.ACCENT}; font-size: 12px; font-weight: 800;")
        self.conf_lbl.setFixedWidth(38)
        self.confluence.valueChanged.connect(lambda v: self.conf_lbl.setText(f"{v}%"))
        srow.addWidget(self.confluence)
        srow.addWidget(self.conf_lbl)
        c.addLayout(srow)
        ch = QLabel("Confluence needed to qualify. Lower = more results.")
        ch.setObjectName("Mini")
        c.addWidget(ch)
        self.rules_toggle = QPushButton("Fine-tune indicators  ▾")
        self.rules_toggle.setObjectName("Tool")
        self.rules_toggle.setCheckable(True)
        self.rules_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        c.addWidget(self.rules_toggle)
        self.rules_box = QWidget()
        self.rules_box.setObjectName("Clear")
        rg = QGridLayout(self.rules_box)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setHorizontalSpacing(8)
        rg.setVerticalSpacing(5)
        self.rule_inputs: dict[str, Any] = {}
        for r, (key, label, kind, lo, hi, step) in enumerate(RULE_FIELDS):
            lab = QLabel(label)
            lab.setObjectName("Body")
            box = QSpinBox() if kind == "int" else QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setSingleStep(step)
            if kind == "float":
                box.setDecimals(2)
            box.setValue(RULE_DEFAULTS[key])
            box.setFixedWidth(84)
            self.rule_inputs[key] = box
            rg.addWidget(lab, r, 0)
            rg.addWidget(box, r, 1)
        reset = QPushButton("Reset to defaults")
        reset.setObjectName("Tool")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self.reset_rules)
        rg.addWidget(reset, len(RULE_FIELDS), 0, 1, 2)
        self.rules_box.setVisible(False)
        self.rules_toggle.toggled.connect(self._toggle_rules)
        c.addWidget(self.rules_box)

        c.addWidget(divider())
        c.addWidget(group_label("3 · Speed"))
        wrow = QHBoxLayout()
        self.workers = QSlider(Qt.Orientation.Horizontal)
        self.workers.setRange(2, 24)
        self.workers.setValue(8)
        self.workers_lbl = QLabel("8 workers")
        self.workers_lbl.setObjectName("Mini")
        self.workers_lbl.setFixedWidth(62)
        self.workers.valueChanged.connect(lambda v: self.workers_lbl.setText(f"{v} workers"))
        wrow.addWidget(self.workers)
        wrow.addWidget(self.workers_lbl)
        c.addLayout(wrow)
        self.turbo = QCheckBox("Turbo mode (batched downloads)")
        self.turbo.setChecked(True)
        c.addWidget(self.turbo)
        c.addStretch(1)
        outer.addWidget(scroll_page(body), 1)

        brow = QHBoxLayout()
        brow.setContentsMargins(16, 0, 16, 0)
        brow.setSpacing(7)
        self.run_btn = QPushButton("▶  Run scan")
        self.run_btn.setObjectName("Primary")
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setEnabled(False)
        brow.addWidget(self.run_btn, 1)
        brow.addWidget(self.stop_btn)
        outer.addLayout(brow)
        crow2 = QHBoxLayout()
        crow2.setContentsMargins(16, 0, 16, 0)
        self.cont_btn = QPushButton("↻  Continue where it stopped")
        self.cont_btn.setObjectName("Ghost")
        self.cont_btn.setEnabled(False)
        crow2.addWidget(self.cont_btn)
        outer.addLayout(crow2)
        for b in (self.run_btn, self.stop_btn, self.cont_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(cfg)

        right = QVBoxLayout()
        right.setSpacing(11)
        tel = QHBoxLayout()
        tel.setSpacing(11)
        self.tcards = {"scanned": StatCard("Scanned"), "qualified": StatCard("Qualified", Theme.ACCENT),
                       "rate": StatCard("Speed / min"), "eta": StatCard("Time left")}
        for v in self.tcards.values():
            tel.addWidget(v)
        right.addLayout(tel)

        prog = QFrame()
        prog.setObjectName("Panel")
        ph = QHBoxLayout(prog)
        ph.setContentsMargins(15, 10, 15, 10)
        ph.setSpacing(14)
        self.radar = Radar(54)
        ph.addWidget(self.radar)
        pl2 = QVBoxLayout()
        pl2.setSpacing(7)
        self.status_lbl = QLabel("Ready. Choose what to scan, then press Run.")
        self.status_lbl.setObjectName("Body")
        self.status_lbl.setWordWrap(True)
        pl2.addWidget(self.status_lbl)
        self.prog = ProgressStrip()
        pl2.addWidget(self.prog)
        ph.addLayout(pl2, 1)
        right.addWidget(prog)

        res = QFrame()
        res.setObjectName("Panel")
        rl = QVBoxLayout(res)
        rl.setContentsMargins(15, 12, 15, 11)
        rl.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(7)
        rt = QLabel("Qualified results")
        rt.setObjectName("SectionTitle")
        head.addWidget(rt)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Mini")
        head.addWidget(self.count_lbl)
        head.addStretch(1)
        for text in ("Min score",):
            l = QLabel(text)
            l.setObjectName("Mini")
            head.addWidget(l)
        self.min_score = QSpinBox()
        self.min_score.setRange(0, 100)
        self.min_score.setSuffix("%")
        self.min_score.setFixedWidth(66)
        head.addWidget(self.min_score)
        l = QLabel("Min RVOL")
        l.setObjectName("Mini")
        head.addWidget(l)
        self.min_rvol = QDoubleSpinBox()
        self.min_rvol.setRange(0, 20)
        self.min_rvol.setSingleStep(0.5)
        self.min_rvol.setDecimals(1)
        self.min_rvol.setSuffix("×")
        self.min_rvol.setFixedWidth(66)
        head.addWidget(self.min_rvol)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by name…")
        self.filter.setFixedWidth(170)
        self.filter.setClearButtonEnabled(True)
        head.addWidget(self.filter)
        self.export_btn = IconButton("export", "Export results to CSV", 30)
        self.export_btn.clicked.connect(self._export)
        head.addWidget(self.export_btn)
        rl.addLayout(head)
        self.res_stack = QStackedWidget()
        self.empty = QLabel("Results stream in here live while the scan runs.\n"
                            "Double-click any row to open its chart.")
        self.empty.setObjectName("Empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = make_table(RESULT_HEADERS)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(self._row_open)
        self.res_stack.addWidget(self.empty)
        self.res_stack.addWidget(self.table)
        rl.addWidget(self.res_stack, 1)
        right.addWidget(res, 1)
        lay.addLayout(right, 1)

        self._mode = 0
        self._watch: list[str] = []
        self._watch_name = "My Watchlist"
        self._stop = threading.Event()
        self._plan: Optional[dict] = None
        self._done: set[str] = set()
        self._results: dict[str, dict] = {}
        self._errors = 0
        self._base = {"scanned": 0, "total": 0}
        self._last_t: dict = {}
        self._latest: Optional[dict] = None
        self._started = datetime.now()
        self._running = False
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(120)
        self._ui_timer.timeout.connect(self._flush)

        self.preset.currentIndexChanged.connect(self._preset_hint)
        self.filter.textChanged.connect(lambda _t: self._apply_filter())
        self.min_score.valueChanged.connect(lambda _v: self._apply_filter())
        self.min_rvol.valueChanged.connect(lambda _v: self._apply_filter())
        self.run_btn.clicked.connect(lambda: self.start_scan(False))
        self.cont_btn.clicked.connect(lambda: self.start_scan(True))
        self.stop_btn.clicked.connect(self.stop_scan)
        self.set_mode(0)
        self._preset_hint()
        self._reset_cards()
        self.set_watchlist(self._watch_name, list(DEFAULT_WATCHLIST))

    # --- configuration
    def set_mode(self, idx: int):
        self._mode = idx
        for card in self.mode_cards:
            card.set_active(card.index == idx)
        self.mode_stack.setCurrentIndex(idx)
        self.mode_stack.setFixedHeight({0: 92, 1: 108, 2: 172, 3: 136}.get(idx, 150))

    def set_watchlist(self, name: str, symbols: list[str]):
        self._watch_name, self._watch = name, list(symbols)
        self.watch_info.setText(f"“{name}” · {len(symbols)} symbol{'s' if len(symbols) != 1 else ''}")
        preview = ", ".join(s.split(".")[0] for s in symbols[:14])
        self.watch_syms.setText(preview + (f"  +{len(symbols) - 14} more" if len(symbols) > 14 else ""))

    def _preset_symbols(self) -> list[str]:
        sc = load_scanner()
        return list((getattr(sc, "PRESET_LISTS", {}) or {}).get(self.preset.currentText(), [])) if sc else []

    def _preset_hint(self):
        name = self.preset.currentText()
        if name.startswith("F&O"):
            self.preset_hint.setText("All F&O stocks listed on NSE.")
            self.tweak_btn.setEnabled(False)
            return
        n = len(self._preset_symbols())
        self.preset_hint.setText(f"{n} symbols" if n else "List unavailable in the engine.")
        self.tweak_btn.setEnabled(bool(n))

    def _preset_to_custom(self):
        symbols = self._preset_symbols()
        if symbols:
            self.custom.setPlainText(", ".join(symbols))
            self.set_mode(2)
            self.notify.emit(f"Loaded {len(symbols)} symbols — edit them freely, then Run.", "info")

    def _append_custom(self, symbols: list[str]):
        merged = parse_symbols(self.custom.toPlainText())
        for s in symbols:
            if s not in merged:
                merged.append(s)
        self.custom.setPlainText(", ".join(merged))

    def _custom_count(self):
        n = len(parse_symbols(self.custom.toPlainText()))
        self.custom_count.setText(f"{n} symbol{'s' if n != 1 else ''}")

    def _toggle_rules(self, on: bool):
        self.rules_box.setVisible(on)
        self.rules_toggle.setText("Fine-tune indicators  ▴" if on else "Fine-tune indicators  ▾")

    def reset_rules(self):
        for key, box in self.rule_inputs.items():
            box.setValue(RULE_DEFAULTS[key])

    def rule_settings(self) -> dict:
        out = {key: box.value() for key, box in self.rule_inputs.items()}
        out["confluence_percent"] = self.confluence.value()
        return out

    def _build_plan(self) -> Optional[dict]:
        if self._mode == 0:
            if not self._watch:
                self.status_lbl.setText("Your watchlist is empty — add stocks in the Watchlist panel.")
                return None
            jobs = [{"label": self._watch_name, "tickers": list(self._watch)}]
            scope = f"{self._watch_name} ({len(self._watch)})"
        elif self._mode == 1:
            name = self.preset.currentText()
            if name.startswith("F&O"):
                jobs = [{"label": "F&O stocks", "exchange": "F&O", "full": True}]
            else:
                tickers = self._preset_symbols()
                if not tickers:
                    self.status_lbl.setText(f"Preset '{name}' is not available in the engine.")
                    return None
                jobs = [{"label": name, "tickers": tickers}]
            scope = name
        elif self._mode == 2:
            tickers = parse_symbols(self.custom.toPlainText())
            if not tickers:
                self.status_lbl.setText("Type at least one symbol in the Custom box.")
                return None
            jobs = [{"label": "Custom list", "tickers": tickers}]
            scope = f"Custom list ({len(tickers)})"
        else:
            picked = [e for e, b in self.exch_boxes.items() if b.isChecked()]
            if not picked:
                self.status_lbl.setText("Tick at least one exchange.")
                return None
            jobs = [{"label": e, "exchange": e, "full": True} for e in picked]
            scope = "Full exchange (" + ", ".join(picked) + ")"
        if self.rule_inputs["macd_fast"].value() >= self.rule_inputs["macd_slow"].value():
            self.status_lbl.setText("MACD fast must be smaller than MACD slow — fix it under Fine-tune indicators.")
            return None
        return {"jobs": jobs, "scope": scope, "workers": self.workers.value(),
                "turbo": self.turbo.isChecked(), "settings": self.rule_settings()}

    def _reset_cards(self):
        for key in ("scanned", "qualified", "rate", "eta"):
            self.tcards[key].value.set_text("—")
        self.tcards["scanned"].hint.setText("symbols checked")
        self.tcards["qualified"].hint.setText("passed your rules")
        self.tcards["rate"].hint.setText("symbols per minute")
        self.tcards["eta"].hint.setText("estimated")

    # --- run / stop / continue
    def start_scan(self, resume: bool):
        if self._running:
            return
        if not resume:
            plan = self._build_plan()
            if plan is None:
                return
            self._plan = plan
            self._done, self._results, self._errors = set(), {}, 0
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            self.table.setSortingEnabled(True)
            self.res_stack.setCurrentIndex(0)
            self.count_lbl.setText("")
            self._base = {"scanned": 0, "total": 0}
            self._started = datetime.now()
        elif self._plan is None:
            return
        else:
            self._base = {"scanned": len(self._done), "total": len(self._done)}
        self._last_t, self._latest = {}, None
        self._stop = threading.Event()
        self._running = True
        self.run_btn.setEnabled(False)
        self.cont_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.prog.set_active(True)
        self.radar.set_active(True)
        if not resume:
            self.prog.set_progress(0.0)
        self.status_lbl.setText(("Continuing: " if resume else "Starting: ") + self._plan["scope"] + "…")
        self.scan_state.emit("running")
        self._ui_timer.start()
        w = ScanWorker(self._plan, self._stop, self._done if resume else set())
        w.event.connect(self._on_event)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        launch(w)

    def stop_scan(self):
        if not self._running:
            return
        self._stop.set()
        self.stop_btn.setEnabled(False)
        self.status_lbl.setText("Stopping — finishing the batch already downloading…")

    def _on_event(self, ev: dict):
        kind = ev.get("type")
        if kind == "job":
            self._base["scanned"] += int(self._last_t.get("scanned", 0))
            self._base["total"] += int(self._last_t.get("total", 0))
            self._last_t = {}
            self.status_lbl.setText(f"Preparing {ev.get('label', '')}…")
        elif kind == "status":
            if not self._stop.is_set():
                self.status_lbl.setText(str(ev.get("message", "")))
        elif kind == "symbol_result":
            t = str((ev.get("result") or {}).get("ticker", ""))
            if t:
                self._done.add(t)
        elif kind == "symbol_error":
            t = str(ev.get("ticker") or ev.get("symbol") or "")
            if t:
                self._done.add(t)
            self._errors += 1
        elif kind == "match":
            self._add_result(dict(ev.get("result") or {}))
        elif kind == "final_results":
            for res in ev.get("results", []):
                t = str(res.get("ticker", ""))
                if t in self._results:
                    self._results[t].update(res)
        if isinstance(ev.get("telemetry"), dict):
            self._last_t = ev["telemetry"]
            self._latest = ev["telemetry"]

    def _flush(self):
        t = self._latest
        if not t:
            return
        self._latest = None
        scanned = self._base["scanned"] + int(t.get("scanned", 0))
        total = self._base["total"] + int(t.get("total", 0))
        self.tcards["scanned"].value.set_text(f"{scanned:,}")
        self.tcards["scanned"].hint.setText(f"of {total:,} symbols")
        self.tcards["qualified"].value.set_text(f"{len(self._results):,}")
        self.tcards["rate"].value.set_text(f"{float(t.get('rate_per_minute', 0) or 0):,.0f}")
        self.tcards["eta"].value.set_text(str(t.get("eta_label", "--:--")))
        self.prog.set_progress(scanned / total if total else 0.0)

    def _add_result(self, res: dict):
        sym = str(res.get("ticker", ""))
        if not sym or sym in self._results:
            return
        self._results[sym] = res
        chg = float(res.get("change_pct", 0) or 0)
        score = float(res.get("confluence_percent", 0) or 0)
        price = float(res.get("price", 0) or 0)
        rvol = float(res.get("rvol", 0) or 0)
        rsi_v = float(res.get("rsi", 0) or 0)
        trend = str(res.get("trend", "") or "")
        self.table.setSortingEnabled(False)
        r = self.table.rowCount()
        self.table.insertRow(r)
        a = QTableWidgetItem(sym.split(".")[0])
        a.setData(Qt.ItemDataRole.UserRole, sym)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        name_item = QTableWidgetItem(str(res.get("company_name", "") or ""))
        name_item.setForeground(QColor(Theme.TEXT_MUTED))
        cells = [a, name_item, NumItem(str(res.get("price_label") or f"{price:,.2f}"), price),
                 NumItem(f"{chg:+.2f}%", chg), NumItem(f"{rvol:.1f}×", rvol),
                 NumItem(f"{rsi_v:.0f}", rsi_v), NumItem(f"{score:.0f}%", score), QTableWidgetItem(trend)]
        cells[3].setForeground(QColor(Theme.BULL if chg >= 0 else Theme.BEAR))
        cells[6].setForeground(QColor(Theme.ACCENT))
        cells[7].setForeground(QColor(Theme.BULL if ("up" in trend.lower() or "bull" in trend.lower())
                                      else Theme.TEXT_MUTED))
        flash = QColor(25, 230, 195, 52)
        for ci, it in enumerate(cells):
            it.setBackground(flash)
            self.table.setItem(r, ci, it)
        self.table.setSortingEnabled(True)
        QTimer.singleShot(900, lambda items=cells: [self._unflash(i) for i in items])
        self.res_stack.setCurrentIndex(1)
        self._apply_filter()

    @staticmethod
    def _unflash(item):
        try:
            item.setBackground(QBrush())
        except RuntimeError:
            pass

    def _apply_filter(self):
        q = self.filter.text().strip().lower()
        min_score, min_rvol = self.min_score.value(), self.min_rvol.value()
        shown = 0
        for r in range(self.table.rowCount()):
            a, b = self.table.item(r, 0), self.table.item(r, 1)
            hay = f"{a.text() if a else ''} {b.text() if b else ''}".lower()
            rv, sc = self.table.item(r, 4), self.table.item(r, 6)
            hide = (bool(q) and q not in hay) \
                or (sc is not None and getattr(sc, "value", 0) < min_score) \
                or (rv is not None and getattr(rv, "value", 0) < min_rvol)
            self.table.setRowHidden(r, hide)
            shown += 0 if hide else 1
        total = len(self._results)
        self.count_lbl.setText(f"· {shown} of {total}" if shown != total else (f"· {total}" if total else ""))

    def _export(self):
        if not self._results:
            self.status_lbl.setText("Nothing to export yet — run a scan first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export results", "cortex_scan.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            keys = ["ticker", "company_name", "price", "change_pct", "rvol", "rsi",
                    "confluence_percent", "trend", "sector", "industry"]
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(keys)
                for res in self._results.values():
                    w.writerow([res.get(k, "") for k in keys])
            self.notify.emit(f"Exported {len(self._results)} rows", "ok")
        except Exception as exc:
            self.status_lbl.setText(f"Export failed: {exc}")

    def _row_open(self, row, _c):
        it = self.table.item(row, 0)
        if it:
            self.open_chart.emit(str(it.data(Qt.ItemDataRole.UserRole) or it.text()))

    def _finish(self):
        self._running = False
        self._ui_timer.stop()
        if self._last_t:
            self._latest = self._last_t
            self._flush()
        self.prog.set_active(False)
        self.radar.set_active(False)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.scan_state.emit("idle")

    def _summary(self) -> dict:
        scanned, qualified = len(self._done), len(self._results)
        secs = int((datetime.now() - self._started).total_seconds())
        return {"scanned": scanned, "qualified": qualified,
                "pass_rate": round(qualified / scanned * 100, 2) if scanned else 0.0,
                "scope": (self._plan or {}).get("scope", "Scan"),
                "duration": f"{secs // 60:02d}:{secs % 60:02d}"}

    def _on_done(self, payload: dict):
        self._finish()
        s = self._summary()
        err = f" · {self._errors} symbols had no data" if self._errors else ""
        if payload.get("aborted"):
            self.cont_btn.setEnabled(True)
            self.status_lbl.setText(f"Stopped at {s['scanned']:,} symbols — {s['qualified']} qualified kept{err}. "
                                    "Press Continue to pick up where it stopped.")
            self.notify.emit(f"Scan stopped — {s['qualified']} qualified so far", "warn")
        else:
            self.cont_btn.setEnabled(False)
            self.prog.set_progress(1.0)
            self.status_lbl.setText(f"Done in {s['duration']} — {s['scanned']:,} scanned, "
                                    f"{s['qualified']} qualified{err}.")
            history = list(ui_state().get("scan_history", []) or [])
            history.insert(0, {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "scope": s["scope"],
                               "symbols_scanned": s["scanned"], "qualified": s["qualified"],
                               "pass_rate": s["pass_rate"], "duration": s["duration"]})
            ui_state_update(scan_history=history[:25])
            self.notify.emit(f"Scan finished — {s['qualified']} of {s['scanned']:,} qualified", "ok")
        self.scan_finished.emit(s, list(self._results.values()))

    def _on_failed(self, message: str):
        self._finish()
        self.cont_btn.setEnabled(bool(self._done))
        self.status_lbl.setText(f"Scan failed: {message}")
        self.notify.emit("Scan failed — see the status line", "error")

    def shutdown(self):
        self._stop.set()


# ===========================================================================
# Charts
# ===========================================================================
class ChartsView(QWidget):
    loaded = Signal(str, str, object)        # symbol, timeframe, candles
    add_to_watch = Signal(str, str)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        sym_box = QVBoxLayout()
        sym_box.setSpacing(0)
        line = QHBoxLayout()
        line.setSpacing(9)
        self.sym_lbl = QLabel("—")
        self.sym_lbl.setStyleSheet("font-size: 19px; font-weight: 800;")
        self.price_lbl = QLabel("")
        self.price_lbl.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.chg_lbl = QLabel("")
        line.addWidget(self.sym_lbl)
        line.addWidget(self.price_lbl)
        line.addWidget(self.chg_lbl)
        line.addStretch(1)
        sym_box.addLayout(line)
        self.name_lbl = QLabel("Pick a stock from the watchlist or the search bar")
        self.name_lbl.setObjectName("Mini")
        sym_box.addWidget(self.name_lbl)
        sym_host = QWidget()
        sym_host.setObjectName("Clear")
        sym_host.setLayout(sym_box)
        sym_host.setMinimumWidth(230)
        top.addWidget(sym_host)
        self.star_btn = IconButton("watch", "Add to watchlist", 30)
        self.star_btn.clicked.connect(lambda: self.add_to_watch.emit(self._symbol, self._name) if self._symbol else None)
        top.addWidget(self.star_btn)
        top.addWidget(divider(True))

        self.type_combo = QComboBox()
        self.type_combo.addItems(CC.CHART_TYPES)
        top.addWidget(self.type_combo)
        tf_host = QFrame()
        tf_host.setObjectName("Flat")
        tfw = QHBoxLayout(tf_host)
        tfw.setContentsMargins(3, 3, 3, 3)
        tfw.setSpacing(1)
        self.tf_group = QButtonGroup(self)
        self.tf_group.setExclusive(True)
        for tf in CC.TIMEFRAMES:
            b = QPushButton(tf)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setMinimumWidth(40)
            b.setStyleSheet("padding: 5px 6px;")
            b.setChecked(tf == "1D")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.tf_group.addButton(b)
            tfw.addWidget(b)
        top.addWidget(tf_host)
        self.ind_btn = QPushButton("Indicators")
        self.ind_btn.setObjectName("Tool")
        self.ind_btn.setIcon(QIcon(icon_pixmap("indicators", 16, Theme.TEXT_MUTED)))
        self.ind_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        top.addWidget(self.ind_btn)
        top.addStretch(1)
        self.set_btn = IconButton("gear", "Chart settings", 30)
        self.cmp_btn = IconButton("compare", "Compare with another stock", 30)
        self.cmp_btn.setCheckable(True)
        self.replay_btn = IconButton("play", "Bar replay", 30)
        self.replay_btn.setCheckable(True)
        for b in (self.set_btn, self.cmp_btn, self.replay_btn):
            top.addWidget(b)
        for kind, tip, fn in (("minus", "Zoom out", lambda: self.chart.zoom(1)),
                              ("plus", "Zoom in", lambda: self.chart.zoom(-1)),
                              ("fit", "Fit all bars", lambda: self.chart.fit_all()),
                              ("reset", "Reset view", lambda: self.chart.fit()),
                              ("camera", "Save chart as image", lambda: self._snapshot())):
            b = IconButton(kind, tip, 30)
            b.clicked.connect(fn)
            top.addWidget(b)
        lay.addLayout(top)

        # optional bar: compare + replay controls (hidden until used)
        self.subbar = QFrame()
        self.subbar.setObjectName("Flat")
        sb = QHBoxLayout(self.subbar)
        sb.setContentsMargins(9, 5, 9, 5)
        sb.setSpacing(7)
        self.cmp_search = SmartSearch("Compare with…")
        self.cmp_search.setFixedWidth(230)
        self.cmp_label = QLabel("")
        self.cmp_label.setStyleSheet(f"color: {Theme.GOLD}; font-size: 11px; font-weight: 700;")
        self.cmp_clear = QPushButton("Clear compare")
        self.cmp_clear.setObjectName("Tool")
        self.cmp_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cmp_clear.clicked.connect(self._clear_compare)
        self.cmp_widgets = [self.cmp_search, self.cmp_label, self.cmp_clear]
        for w in self.cmp_widgets:
            sb.addWidget(w)
        self.replay_label = QLabel("")
        self.replay_label.setStyleSheet(f"color: {Theme.WARN}; font-size: 11px; font-weight: 700;")
        self.replay_widgets: list[QWidget] = [self.replay_label]
        sb.addWidget(self.replay_label)
        for kind, tip, fn in (("stepb", "Back one bar", lambda: self._replay_step(-1)),
                              ("play", "Play / pause", lambda: self._replay_play()),
                              ("stepf", "Forward one bar", lambda: self._replay_step(1)),
                              ("close", "Exit replay", lambda: self.toggle_replay(False))):
            b = IconButton(kind, tip, 26)
            b.clicked.connect(fn)
            sb.addWidget(b)
            self.replay_widgets.append(b)
            if kind == "play":
                self.replay_play_btn = b
        sb.addStretch(1)
        self.subbar.setVisible(False)
        lay.addWidget(self.subbar)

        body = QHBoxLayout()
        body.setSpacing(9)
        rail = QFrame()
        rail.setObjectName("Panel")
        rail.setFixedWidth(86)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(8, 10, 8, 10)
        rl.setSpacing(6)
        head = QLabel("DRAW")
        head.setObjectName("Group")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(head)
        tg = QGridLayout()
        tg.setSpacing(5)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_btns: dict[str, IconButton] = {}
        for i, (key, label, _n) in enumerate(CC.DRAW_TOOLS):
            b = IconButton(key, label, 32, tip_side="right")
            b.setCheckable(True)
            b.setChecked(key == "cursor")
            self.tool_group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self.chart.set_tool(k))
            self.tool_btns[key] = b
            if i == 0:
                tg.addWidget(b, 0, 0, 1, 2, Qt.AlignmentFlag.AlignHCenter)
            else:
                tg.addWidget(b, (i + 1) // 2, (i + 1) % 2)
        rl.addLayout(tg)
        rl.addWidget(divider())
        eg = QGridLayout()
        eg.setSpacing(5)
        self.del_btn = IconButton("delete", "Delete selected drawing  (Del)", 32, tip_side="right")
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(lambda: self.chart.delete_selected())
        undo = IconButton("undo", "Undo last drawing", 32, tip_side="right")
        undo.clicked.connect(lambda: self.chart.undo_drawing())
        redo = IconButton("redo", "Redo drawing", 32, tip_side="right")
        redo.clicked.connect(lambda: self.chart.redo_drawing())
        clear = IconButton("trash", "Delete ALL drawings", 32, tip_side="right")
        clear.clicked.connect(lambda: self.chart.clear_drawings())
        for i, b in enumerate((undo, redo, self.del_btn, clear)):
            eg.addWidget(b, i // 2, i % 2)
        rl.addLayout(eg)
        rl.addStretch(1)
        body.addWidget(rail)

        card = QFrame()
        card.setObjectName("ChartCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(2, 2, 2, 2)
        self.chart = CandleChart()
        cl.addWidget(self.chart)
        body.addWidget(card, 1)
        lay.addLayout(body, 1)

        self._token = 0
        self._symbol, self._name = "", ""
        self._ind_menu = self._build_indicator_menu()
        self._set_menu = self._build_settings_menu()
        self.ind_btn.clicked.connect(lambda: self._ind_menu.exec(
            self.ind_btn.mapToGlobal(QPoint(0, self.ind_btn.height() + 4))))
        self.set_btn.clicked.connect(lambda: self._set_menu.exec(
            self.set_btn.mapToGlobal(QPoint(0, self.set_btn.height() + 4))))
        self.cmp_btn.toggled.connect(lambda _on: self._sync_subbar())
        self.replay_btn.clicked.connect(lambda: self.toggle_replay(self.replay_btn.isChecked()))
        self.cmp_search.picked.connect(lambda sym, name: self._load_compare(sym))
        self.type_combo.currentTextChanged.connect(self.chart.set_type)
        for b in self.tf_group.buttons():
            b.clicked.connect(lambda _=False: self.load(self._symbol, self._name) if self._symbol else None)
        self.chart.toolChanged.connect(self._on_tool_changed)
        self.chart.selectionChanged.connect(lambda: self.del_btn.setEnabled(self.chart.has_selection()))
        self._refresh_ind_label()
        self._sync_subbar()

    def _build_indicator_menu(self) -> QMenu:
        menu = QMenu(self)
        self._ind_actions: dict[str, QAction] = {}
        groups: dict[str, list] = {}
        for key, label, _pane, grp in CC.INDICATOR_CATALOG:
            groups.setdefault(grp, []).append((key, label))
        for grp, items in groups.items():
            sub = menu.addMenu(f"{grp}  ({len(items)})")
            for key, label in items:
                act = QAction(label, sub)
                act.setCheckable(True)
                act.setChecked(self.chart.ind.get(key, False))
                act.toggled.connect(lambda on, k=key: self._toggle_ind(k, on))
                sub.addAction(act)
                self._ind_actions[key] = act
        menu.addSeparator()
        clear = QAction("Remove all indicators", menu)
        clear.triggered.connect(self._clear_indicators)
        menu.addAction(clear)
        return menu

    def _build_settings_menu(self) -> QMenu:
        menu = QMenu(self)
        self._set_actions: dict[str, QAction] = {}
        for key, label in (("log", "Logarithmic scale"), ("percent", "Percent scale"),
                           ("invert", "Invert scale"), ("magnet", "Magnet (snap to OHLC)"),
                           ("keep_tool", "Keep drawing tool active"),
                           ("last_price", "Last price line"), ("legend", "Legend"), ("watermark", "Watermark")):
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(bool(self.chart.settings.get(key)))
            act.toggled.connect(lambda on, k=key: self.chart.set_setting(k, on))
            menu.addAction(act)
            self._set_actions[key] = act
        grid_menu = menu.addMenu("Grid lines")
        grid_group = QActionGroup(grid_menu)
        self._grid_actions: list[QAction] = []
        for value, label in (("both", "Full grid"), ("horizontal", "Horizontal only"),
                             ("vertical", "Vertical only"), ("none", "No grid")):
            act = QAction(label, grid_menu)
            act.setCheckable(True)
            act.setChecked(self.chart.settings.get("grid") == value)
            act.triggered.connect(lambda _=False, v=value: self.chart.set_setting("grid", v))
            grid_group.addAction(act)
            grid_menu.addAction(act)
            self._grid_actions.append(act)
        menu.addSeparator()
        menu.addAction("Auto price scale", lambda: self.chart.set_auto_scale(True))
        menu.addAction("Reset view", lambda: self.chart.fit())
        return menu

    def _toggle_ind(self, key: str, on: bool):
        self.chart.set_indicator(key, on)
        self._refresh_ind_label()

    def _clear_indicators(self):
        for key, act in self._ind_actions.items():
            act.setChecked(False)
            self.chart.set_indicator(key, False)
        self._refresh_ind_label()

    def _refresh_ind_label(self):
        n = len(self.chart.active_indicators())
        self.ind_btn.setText(f"Indicators · {n}" if n else "Indicators")

    def _on_tool_changed(self, tool: str):
        btn = self.tool_btns.get(tool)
        if btn:
            btn.setChecked(True)

    def timeframe(self) -> str:
        b = self.tf_group.checkedButton()
        return b.text() if b else "1D"

    def load(self, symbol: str, name: str = ""):
        if not symbol:
            return
        self._symbol, self._name = symbol, name or self._name if symbol == self._symbol else name
        self._token += 1
        self.sym_lbl.setText(symbol.split(".")[0].lstrip("^"))
        self.name_lbl.setText(self._name or symbol)
        self.price_lbl.setText("")
        self.chg_lbl.setText("")
        self.chart.set_loading(symbol, self.timeframe())
        if self.chart.replay_active():
            self.replay_btn.setChecked(False)
        w = ChartWorker(self._token, symbol, self.timeframe())
        w.loaded.connect(self._on_loaded)
        w.failed.connect(self._on_failed)
        launch(w)

    def _on_loaded(self, token, symbol, tf, candles, labels):
        if token != self._token:
            return
        self.chart.set_candles(symbol, tf, candles, labels, self._name)
        self.cmp_label.setText("")
        last = candles[-1]
        prev = candles[-2].c if len(candles) > 1 else last.o
        chg = (last.c - prev) / prev * 100 if prev else 0.0
        col = Theme.BULL if chg >= 0 else Theme.BEAR
        self.price_lbl.setText(f"{last.c:,.2f}")
        self.chg_lbl.setText(f"{'▲' if chg >= 0 else '▼'} {abs(last.c - prev):,.2f} ({abs(chg):.2f}%)")
        self.chg_lbl.setStyleSheet(f"color: {col}; font-size: 12px; font-weight: 800;")
        self.name_lbl.setText(f"{self._name or symbol}  ·  {len(candles):,} bars  ·  {tf}")
        self._sync_subbar()
        self.loaded.emit(symbol, tf, candles)

    def _on_failed(self, token, message):
        if token == self._token:
            self.chart.set_error(message)
            self.name_lbl.setText("Couldn't load this symbol")

    # --- compare
    def _load_compare(self, symbol: str):
        if not symbol or not self.chart.has_data():
            return
        self.cmp_label.setText(f"loading {symbol.split('.')[0]}…")
        w = ChartWorker(-1, symbol, self.timeframe(), role="compare")
        w.loaded.connect(self._on_compare)
        w.failed.connect(lambda _t, msg: self.cmp_label.setText("compare failed"))
        launch(w)

    def _on_compare(self, _token, symbol, _tf, candles, _labels):
        self.chart.set_compare(symbol, [c.c for c in candles])
        self.cmp_label.setText(f"vs {symbol.split('.')[0]} (dashed, rebased)")

    def _clear_compare(self):
        self.chart.clear_compare()
        self.cmp_label.setText("")

    # --- replay
    def toggle_replay(self, on: Optional[bool] = None):
        if not self.chart.has_data():
            self.replay_btn.setChecked(False)
            return
        want = (not self.chart.replay_active()) if on is None else on
        if want:
            self.chart.start_replay()
        else:
            self.chart.stop_replay()
        self.replay_btn.setChecked(self.chart.replay_active())
        self._sync_subbar()

    def _replay_step(self, n: int):
        self.chart.replay_step(n)
        self._sync_subbar()

    def _replay_play(self):
        if self.chart.replay_active():
            self.chart.replay_play(not self.chart.replay_playing())
            self._sync_subbar()

    def _sync_subbar(self):
        cmp_on = self.cmp_btn.isChecked()
        rep_on = self.chart.replay_active()
        for w in self.cmp_widgets:
            w.setVisible(cmp_on)
        for w in self.replay_widgets:
            w.setVisible(rep_on)
        if rep_on:
            at, total = self.chart.replay_progress()
            self.replay_label.setText(f"REPLAY  bar {at} / {total}")
            self.replay_play_btn.set_kind("pause" if self.chart.replay_playing() else "play")
        self.subbar.setVisible(cmp_on or rep_on)

    def _snapshot(self):
        name = (self._symbol.split(".")[0] or "chart") + "_" + self.timeframe() + ".png"
        path, _ = QFileDialog.getSaveFileName(self, "Save chart image", name, "PNG image (*.png)")
        if path:
            self.chart.grab().save(path)


# ===========================================================================
# News
# ===========================================================================
SENTIMENT_COLORS = {"bullish": Theme.BULL, "bearish": Theme.BEAR, "neutral": Theme.TEXT_MUTED}


class NewsCard(GlowCard):
    def __init__(self, item: dict, when: Optional[datetime]):
        super().__init__("Card")
        self._link = str(item.get("link", "") or "")
        self.sentiment = str(item.get("sentiment_tag", "Neutral") or "Neutral")
        if self._link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        col = SENTIMENT_COLORS.get(self.sentiment.lower(), Theme.TEXT_MUTED)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 11, 15, 11)
        h.setSpacing(13)
        t = QLabel(when.strftime("%H:%M") if when else "--:--")
        t.setFixedWidth(46)
        t.setStyleSheet("font-size: 14px; font-weight: 800;")
        h.addWidget(t)
        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background: {col}; border-radius: 1px;")
        h.addWidget(bar)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        title = QLabel(str(item.get("title", "")))
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        full = when.strftime("%d %b %Y · %H:%M") if when else str(item.get("published_label", "Unknown time"))
        meta = QLabel(f"{item.get('source', '')}  ·  {full}")
        meta.setStyleSheet(f"color: {Theme.TEXT_FAINT}; font-size: 10px;")
        mid.addWidget(title)
        mid.addWidget(meta)
        h.addLayout(mid, 1)
        tag = QLabel(self.sentiment.upper())
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag.setFixedWidth(74)
        tag.setStyleSheet(f"color: {col}; font-size: 9px; font-weight: 800; border: 1px solid {col}; "
                          f"border-radius: 8px; padding: 3px 5px; letter-spacing: 1px;")
        h.addWidget(tag)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._link and e.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl(self._link))


class NewsView(QWidget):
    open_chart = Signal(str)
    items_loaded = Signal(str, list)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)
        top = QHBoxLayout()
        top.setSpacing(9)
        self.title = QLabel("Headlines")
        self.title.setStyleSheet("font-size: 17px; font-weight: 800;")
        top.addWidget(self.title)
        self.filters = SegmentedControl(["All", "Bullish", "Bearish", "Neutral"], 0)
        self.filters.on_change(lambda _i: self._render())
        top.addWidget(self.filters)
        self.chart_btn = QPushButton("Open chart")
        self.chart_btn.setObjectName("Tool")
        self.chart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chart_btn.clicked.connect(lambda: self.open_chart.emit(self._ticker) if self._ticker else None)
        top.addWidget(self.chart_btn)
        self.reload_btn = IconButton("refresh", "Reload headlines", 30)
        self.reload_btn.clicked.connect(lambda: self.load(self._ticker, self._name) if self._ticker else None)
        top.addWidget(self.reload_btn)
        top.addStretch(1)
        self.info = QLabel("")
        self.info.setObjectName("Mini")
        top.addWidget(self.info)
        lay.addLayout(top)
        self.inner = QWidget()
        self.feed = QVBoxLayout(self.inner)
        self.feed.setContentsMargins(2, 2, 9, 16)
        self.feed.setSpacing(8)
        lay.addWidget(scroll_page(self.inner), 1)
        self._items: list = []
        self._ticker, self._name = "", ""
        self._token = 0
        self._message("Pick a stock from your watchlist or the search bar to load its headlines.")

    def current(self) -> str:
        return self._ticker

    def _clear(self):
        clear_layout(self.feed)

    def _message(self, text: str, loading: bool = False):
        self._clear()
        if loading:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(Equalizer(9))
            row.addStretch(1)
            host = QWidget()
            host.setObjectName("Clear")
            host.setLayout(row)
            self.feed.addSpacing(36)
            self.feed.addWidget(host)
        l = QLabel(text)
        l.setObjectName("Empty")
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.setWordWrap(True)
        self.feed.addWidget(l)
        self.feed.addStretch(1)

    def load(self, symbol: str, name: str = ""):
        if not symbol:
            return
        self._ticker, self._name = symbol, name
        self._token += 1
        self._items = []
        base = symbol.split(".")[0].lstrip("^")
        self.title.setText(f"Headlines · {base}")
        self.info.setText("")
        self._message(f"Fetching headlines for {base}…", loading=True)
        w = NewsWorker(self._token, symbol, name)
        w.loaded.connect(self._on_loaded)
        w.failed.connect(lambda tok, msg: self._message(f"Couldn't load news: {msg}") if tok == self._token else None)
        launch(w)

    def _on_loaded(self, token, ticker, items):
        if token != self._token:
            return
        self._items = sort_news(items)
        self._render()
        self.items_loaded.emit(ticker, list(items))

    def _render(self):
        want = ["all", "bullish", "bearish", "neutral"][self.filters.index()]
        rows = [(w, i) for w, i in self._items
                if want == "all" or str(i.get("sentiment_tag", "")).lower() == want]
        base = self._ticker.split(".")[0].lstrip("^")
        if not self._items:
            if self._ticker and self._token:
                self._message(f"No headlines found for {base}.")
            return
        if not rows:
            self._message(f"No {want} headlines in this set.")
            return
        self._clear()
        today = datetime.now().date()
        current = "?"
        for when, item in rows:
            if when is None:
                label = "UNDATED"
            elif when.date() == today:
                label = "TODAY"
            elif when.date() == today - timedelta(days=1):
                label = "YESTERDAY"
            else:
                label = when.strftime("%A, %d %B %Y").upper()
            if label != current:
                current = label
                head = QLabel(label)
                head.setObjectName("DayHeader")
                self.feed.addWidget(head)
            self.feed.addWidget(NewsCard(item, when))
        self.feed.addStretch(1)
        self.info.setText(f"{len(rows)} headlines · newest first")


# ===========================================================================
# Main window
# ===========================================================================
SIDEBAR_AUTO_COLLAPSE = 1360
WATCH_AUTO_HIDE = 1180


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cortex")
        self.resize(1760, 1010)
        self.setMinimumSize(1080, 700)
        self._backdrop: Optional[QPixmap] = None
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        root.addWidget(self.sidebar)

        center = QWidget()
        center.setObjectName("Clear")
        r = QVBoxLayout(center)
        r.setContentsMargins(20, 14, 20, 12)
        r.setSpacing(9)
        topbar = QHBoxLayout()
        topbar.setSpacing(12)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        self.page_title = QLabel("Dashboard")
        self.page_title.setObjectName("PageTitle")
        self.page_crumb = QLabel("Market overview")
        self.page_crumb.setObjectName("PageCrumb")
        tcol.addWidget(self.page_title)
        tcol.addWidget(self.page_crumb)
        thost = QWidget()
        thost.setObjectName("Clear")
        thost.setLayout(tcol)
        thost.setFixedWidth(170)
        topbar.addWidget(thost)
        self.search = SmartSearch("Search stocks & indices — 'reliance', 'apple'…   Ctrl+K", 420)
        self.search.setMinimumWidth(280)
        self.search.setMaximumWidth(560)
        topbar.addWidget(self.search, 4)
        topbar.addStretch(1)
        self.session_labels: list[QLabel] = []
        for _ in range(2):
            l = QLabel("")
            l.setObjectName("Pill")
            l.setTextFormat(Qt.TextFormat.RichText)
            self.session_labels.append(l)
            topbar.addWidget(l, 0, Qt.AlignmentFlag.AlignVCenter)
        self.clock = QLabel("")
        self.clock.setStyleSheet(f"color: {Theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        topbar.addWidget(self.clock)
        self.status_pill = QLabel("● Starting engine")
        self.status_pill.setObjectName("Pill")
        topbar.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.watch_btn = IconButton("panel", "Show / hide watchlist", 30)
        self.watch_btn.setCheckable(True)
        self.watch_btn.setChecked(True)
        topbar.addWidget(self.watch_btn)
        r.addLayout(topbar)

        self.ticker = TickerTape()
        r.addWidget(self.ticker)

        self.stack = QStackedWidget()
        self.dashboard, self.scanner = DashboardView(), ScannerView()
        self.charts, self.news = ChartsView(), NewsView()
        for v in (self.dashboard, self.scanner, self.charts, self.news):
            self.stack.addWidget(v)
        r.addWidget(self.stack, 1)
        root.addWidget(center, 1)

        self.watch = WatchlistPanel()
        root.addWidget(self.watch)

        self.symbol, self.symbol_name = "", ""
        self._engine_ok = False
        self._blink = False
        self._side_manual: Optional[bool] = None
        self._watch_wanted = True
        self._indices: list = []
        self._slide = None
        self._pill_timer = QTimer(self)
        self._pill_timer.setInterval(520)
        self._pill_timer.timeout.connect(self._pulse_pill)
        self._index_timer = QTimer(self)
        self._index_timer.setInterval(180_000)
        self._index_timer.timeout.connect(self._refresh_indices)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

        self.sidebar.navigate.connect(self.navigate)
        self.sidebar.toggled.connect(self._toggle_sidebar)
        self.watch_btn.toggled.connect(self._toggle_watch)
        self.search.picked.connect(self._on_search)
        self.watch.symbol_activated.connect(self._on_watch_pick)
        self.watch.news_requested.connect(lambda s, n: (self.set_symbol(s, n), self.navigate(3)))
        self.watch.list_changed.connect(self.scanner.set_watchlist)
        self.watch.quotes_changed.connect(self._on_quotes)
        self.watch.notify.connect(self.toast)
        self.scanner.notify.connect(self.toast)
        self.scanner.open_chart.connect(lambda s: (self.set_symbol(s), self.navigate(2)))
        self.scanner.scan_state.connect(self._on_scan_state)
        self.scanner.scan_finished.connect(self.dashboard.update_from_scan)
        self.dashboard.open_symbol.connect(lambda s: (self.set_symbol(s), self.navigate(2)))
        self.dashboard.go_scan.connect(lambda: self.navigate(1))
        self.dashboard.go_news.connect(lambda: self.navigate(3))
        self.charts.loaded.connect(self._on_chart_loaded)
        self.charts.add_to_watch.connect(self.watch.add_symbol)
        self.news.open_chart.connect(lambda s: (self.set_symbol(s), self.navigate(2)))
        self.news.items_loaded.connect(self.dashboard.set_headlines)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_search)
        for i in range(4):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda idx=i: self.navigate(idx))
        self.navigate(0, animate=False)

    # --- painting
    def paintEvent(self, e):  # noqa: N802
        if self._backdrop is None or self._backdrop.size() != self.size():
            self._backdrop = paint_backdrop(self.size())
        p = QPainter(self)
        p.drawPixmap(0, 0, self._backdrop)
        p.end()

    def toast(self, text: str, kind: str = "info"):
        if self.isVisible():
            Toast.show(self, text, kind)

    def _focus_search(self):
        self.search.edit.setFocus()
        self.search.edit.selectAll()

    def _tick_clock(self):
        self.clock.setText(datetime.now().strftime("%a %d %b  %H:%M:%S"))
        for lbl, (name, is_open, hours) in zip(self.session_labels, market_sessions()):
            col = Theme.BULL if is_open else Theme.TEXT_FAINT
            lbl.setText(f"<span style='color:{col}'>●</span> {name} "
                        f"<span style='color:{col}; font-weight:800'>{'OPEN' if is_open else 'CLOSED'}</span>")
            lbl.setToolTip(hours)

    # --- engine lifecycle
    def start_engine(self):
        w = WarmWorker()
        w.ready.connect(self._engine_ready)
        w.failed.connect(self._engine_failed)
        launch(w)

    def _engine_ready(self):
        self._engine_ok = True
        self._set_pill("● Live", Theme.BULL)
        self.sidebar.set_engine("Engine ready", Theme.BULL)
        self.watch.load_state()
        self.dashboard.load_history()
        self._refresh_indices()
        self.watch.start_auto()
        self._index_timer.start()
        last = str(ui_state().get("last_symbol") or "")
        symbols = self.watch.symbols()
        first = last or (symbols[0] if symbols else "AAPL")
        self.set_symbol(first, self.watch.name_of(first))

    def _engine_failed(self, message: str):
        self._set_pill("● Offline", Theme.BEAR)
        self.sidebar.set_engine("Engine offline", Theme.BEAR)
        self.status_pill.setToolTip(message)
        self.scanner.status_lbl.setText(message)
        self.charts.chart.set_error(message)
        self.dashboard.load_history()
        self.watch.foot.setText("Engine offline — quotes unavailable.")
        self.toast("Data engine failed to start", "error")

    def _refresh_indices(self):
        w = IndexWorker()
        w.loaded.connect(self._on_indices)
        launch(w)

    def _on_indices(self, quotes: list):
        self._indices = quotes
        self.dashboard.set_indices(quotes)
        self._update_ticker()

    def _on_quotes(self, quotes: list):
        self.dashboard.set_movers(quotes)
        self._update_ticker()

    def _update_ticker(self):
        items = [(name, f"{value:,.2f}", chg) for name, value, chg, _s in self._indices]
        for sym, row in self.watch.rows.items():
            if row.quote and row.change() is not None:
                price = str(row.quote.get("price_label") or row.quote.get("price", ""))
                items.append((sym.split(".")[0], price, row.change()))
        self.ticker.set_items(items)

    def _set_pill(self, text: str, color: str):
        self.status_pill.setText(text)
        self.status_pill.setStyleSheet(f"color: {color};")

    def _on_scan_state(self, state: str):
        if state == "running":
            self._pill_timer.start()
            self._pulse_pill()
            self.sidebar.set_engine("Scanning…", Theme.ACCENT)
        else:
            self._pill_timer.stop()
            ok = self._engine_ok
            self._set_pill("● Live" if ok else "● Offline", Theme.BULL if ok else Theme.BEAR)
            self.sidebar.set_engine("Engine ready" if ok else "Engine offline", Theme.BULL if ok else Theme.BEAR)

    def _pulse_pill(self):
        self._blink = not self._blink
        self._set_pill("● Scanning" if self._blink else "○ Scanning", Theme.ACCENT)

    # --- active symbol
    def set_symbol(self, symbol: str, name: str = ""):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return
        name = name or self.watch.name_of(symbol) or LOCAL_NAMES.get(symbol, "")
        self.symbol, self.symbol_name = symbol, name
        self.watch.set_selected(symbol)
        self.charts.load(symbol, name)
        if self._engine_ok:
            self.news.load(symbol, name)
            ui_state_update(last_symbol=symbol)

    def _on_search(self, symbol: str, name: str):
        if name:
            self.watch.names[symbol] = name
        self.set_symbol(symbol, name)
        if self.stack.currentIndex() in (0, 1):
            self.navigate(2)

    def _on_watch_pick(self, symbol: str, name: str):
        self.set_symbol(symbol, name)
        if self.stack.currentIndex() in (0, 1):
            self.navigate(2)

    def _on_chart_loaded(self, symbol: str, tf: str, candles):
        if symbol.split(".")[0] == self.symbol.split(".")[0] or symbol == self.symbol:
            self.dashboard.set_featured(self.symbol, self.symbol_name, list(candles))

    # --- navigation / layout
    def navigate(self, index: int, animate: bool = True):
        index = max(0, min(index, len(NAV_ITEMS) - 1))
        changed = index != self.stack.currentIndex()
        self.stack.setCurrentIndex(index)
        self.sidebar.set_current(index, animate)
        self.page_title.setText(NAV_ITEMS[index][0])
        self.page_crumb.setText(NAV_ITEMS[index][2])
        self.search.hide_popup()
        if changed and animate and self.isVisible():
            page = self.stack.currentWidget()
            a = QPropertyAnimation(page, b"pos", self)
            a.setDuration(260)
            a.setStartValue(QPoint(0, 26))
            a.setEndValue(QPoint(0, 0))
            a.setEasingCurve(QEasingCurve.Type.OutCubic)
            a.start()
            self._slide = a

    def _toggle_sidebar(self):
        self._side_manual = not self.sidebar.is_collapsed()
        self.sidebar.set_collapsed(self._side_manual)

    def _toggle_watch(self, on: bool):
        self._watch_wanted = on
        self._apply_layout()

    def _apply_layout(self):
        w = self.width()
        collapse = self._side_manual if self._side_manual is not None else w < SIDEBAR_AUTO_COLLAPSE
        self.sidebar.set_collapsed(collapse, animate=self.isVisible())
        self.watch.setVisible(self._watch_wanted and w >= WATCH_AUTO_HIDE)
        compact = w < 1500
        for l in self.session_labels:
            l.setVisible(not compact)
        self.clock.setVisible(w >= 1300)

    def play_intro(self):
        self.start_engine()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_layout()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_layout()
        self.search.hide_popup()

    def closeEvent(self, event):  # noqa: N802
        self.scanner.shutdown()
        for w in list(LIVE_WORKERS):
            try:
                if w.isRunning() and not w.wait(1500):
                    w.terminate()
                    w.wait(400)
            except RuntimeError:
                pass
        super().closeEvent(event)


# ===========================================================================
# Bootstrap
# ===========================================================================
def build_app():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Cortex")
    font = Theme.ui_font()
    app.setFont(make_font(10))
    app.setStyleSheet(stylesheet(font))
    from pathlib import Path
    for name in ("cortex_icon.ico", "cortex_icon.ico.ico"):
        icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break
    return app, MainWindow()


def main():
    app, window = build_app()
    splash = SplashScreen()

    def reveal():
        window.showMaximized()
        window.play_intro()

    splash.finished.connect(reveal)
    splash.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
