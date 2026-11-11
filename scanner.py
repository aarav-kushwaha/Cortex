"""
Cortex backend engine, news pipeline, charting, and CLI.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import gc
import io
import json
import math
import os
import tempfile
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from rapidfuzz import fuzz, process as fuzz_process
except ImportError:
    fuzz = None
    fuzz_process = None

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / ".aether_cache"
CACHE_DIR.mkdir(exist_ok=True)
YFINANCE_CACHE_DIR = CACHE_DIR / "yfinance"
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)

_YFINANCE_MODULE: Any = None
_PLOTLY_MODULES: tuple[Any | None, Any | None] | None = None
_FINNHUB_MODULE: Any = None


def _get_yfinance_module() -> Any:
    global _YFINANCE_MODULE
    if _YFINANCE_MODULE is None:
        import yfinance as yf

        try:
            if hasattr(yf, "set_tz_cache_location"):
                yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))
        except Exception:
            pass
        _YFINANCE_MODULE = yf
    return _YFINANCE_MODULE


def _get_plotly_modules() -> tuple[Any | None, Any | None]:
    global _PLOTLY_MODULES
    if _PLOTLY_MODULES is None:
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            _PLOTLY_MODULES = (None, None)
        else:
            _PLOTLY_MODULES = (go, make_subplots)
    return _PLOTLY_MODULES


def _get_finnhub_module() -> Any:
    global _FINNHUB_MODULE
    if _FINNHUB_MODULE is None:
        import finnhub

        _FINNHUB_MODULE = finnhub
    return _FINNHUB_MODULE

USER_AGENT = "Cortex-Scanner/2.0 (personal research tool; github.com/your-username/cortex)"

GLOBAL_EXCHANGE = "GLOBAL"
FULL_UNIVERSE_EXCHANGES = ("NASDAQ", "NYSE", "NSE", "BSE")
MAX_WORKERS = 32
DEFAULT_WORKERS = min(MAX_WORKERS, max(6, (os.cpu_count() or 8)))
DEFAULT_DOWNLOAD_BATCH = 48
DEFAULT_HISTORY_PERIOD = "6mo"
DEFAULT_HISTORY_INTERVAL = "1d"
INTRADAY_PERIOD = "5d"
INTRADAY_INTERVAL = "15m"
MIN_METADATA_CACHE_HOURS = 36
UNIVERSE_CACHE_HOURS = 24
NEWS_CACHE_HOURS = 0.3
QUOTE_CACHE_HOURS = 4
CHART_HISTORY_PERIOD = "1y"
CHART_HISTORY_INTERVAL = "1d"
UI_STATE_CACHE_NAME = "cortex_ui_state.json"
DEFAULT_SIGNAL_SENSITIVITY = 55

EXCHANGE_CONFIG = {
    "NASDAQ": {"suffix": "", "label": "NASDAQ", "full_scan": True},
    "NYSE": {"suffix": "", "label": "NYSE", "full_scan": True},
    "NSE": {"suffix": ".NS", "label": "NSE", "full_scan": True},
    "BSE": {"suffix": ".BO", "label": "BSE", "full_scan": True},
    "LSE": {"suffix": ".L", "label": "LSE", "full_scan": False},
}

MAGNIFICENT_7 = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL"]

NIFTY_50 = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "BHARTIARTL.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "INFY.NS",
    "LICI.NS",
    "ITC.NS",
    "HINDUNILVR.NS",
    "LT.NS",
    "KOTAKBANK.NS",
    "BAJFINANCE.NS",
    "AXISBANK.NS",
    "ASIANPAINT.NS",
    "MARUTI.NS",
    "SUNPHARMA.NS",
    "M&M.NS",
    "TITAN.NS",
    "ULTRACEMCO.NS",
    "NTPC.NS",
    "WIPRO.NS",
    "NESTLEIND.NS",
    "POWERGRID.NS",
    "BAJAJFINSV.NS",
    "TATAMOTORS.NS",
    "HCLTECH.NS",
    "TECHM.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "JSWSTEEL.NS",
    "COALINDIA.NS",
    "INDUSINDBK.NS",
    "TATASTEEL.NS",
    "ONGC.NS",
    "GRASIM.NS",
    "BPCL.NS",
    "EICHERMOT.NS",
    "CIPLA.NS",
    "DRREDDY.NS",
    "APOLLOHOSP.NS",
    "HEROMOTOCO.NS",
    "SHRIRAMFIN.NS",
    "BRITANNIA.NS",
    "BAJAJ-AUTO.NS",
    "DIVISLAB.NS",
    "TATACONSUM.NS",
    "SBILIFE.NS",
    "HINDALCO.NS",
    "UPL.NS",
]

NIFTY_BANK = [
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "KOTAKBANK.NS",
    "AXISBANK.NS",
    "INDUSINDBK.NS",
    "BANKBARODA.NS",
    "PNB.NS",
    "FEDERALBNK.NS",
    "AUBANK.NS",
    "IDFCFIRSTB.NS",
    "BANDHANBNK.NS",
]

SP500_LARGE_CAPS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "GOOG",
    "BRK-B",
    "LLY",
    "AVGO",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "MA",
    "ORCL",
    "COST",
    "HD",
    "PG",
    "JNJ",
    "MRK",
    "ABBV",
    "CVX",
    "BAC",
    "KO",
    "WMT",
    "CRM",
    "NFLX",
    "AMD",
    "ADBE",
    "PEP",
    "TMO",
    "MCD",
    "ACN",
    "CSCO",
    "LIN",
    "INTU",
    "ABT",
    "CMCSA",
    "DIS",
    "VZ",
    "TXN",
    "QCOM",
    "AMAT",
    "DHR",
    "PFE",
    "IBM",
    "INTC",
    "CAT",
    "GE",
]

PHARMA_STOCKS = [
    "LLY",
    "JNJ",
    "MRK",
    "ABBV",
    "PFE",
    "AMGN",
    "GILD",
    "BMY",
    "REGN",
    "VRTX",
    "BIIB",
    "MRNA",
    "DRREDDY.NS",
    "SUNPHARMA.NS",
    "CIPLA.NS",
    "DIVISLAB.NS",
    "LUPIN.NS",
    "TORNTPHARM.NS",
    "AUROPHARMA.NS",
    "ZYDUSLIFE.NS",
]

DEFAULT_FNO_SYMBOLS = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "LT.NS",
    "BHARTIARTL.NS",
    "ITC.NS",
    "AXISBANK.NS",
    "KOTAKBANK.NS",
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "TATAMOTORS.NS",
    "TATASTEEL.NS",
    "JSWSTEEL.NS",
    "BAJFINANCE.NS",
    "BAJAJFINSV.NS",
    "MARUTI.NS",
    "SUNPHARMA.NS",
    "ULTRACEMCO.NS",
    "M&M.NS",
    "HCLTECH.NS",
    "WIPRO.NS",
]
FNO_SYMBOLS = list(DEFAULT_FNO_SYMBOLS)

DEFAULT_WATCHLIST = list(MAGNIFICENT_7)

PRESET_LISTS = {
    "Magnificent 7": MAGNIFICENT_7,
    "NIFTY 50": NIFTY_50,
    "Nifty Bank": NIFTY_BANK,
    "S&P 500 Large Caps": SP500_LARGE_CAPS,
    "Pharma Stocks": PHARMA_STOCKS,
    "Custom": DEFAULT_WATCHLIST,
}

POSITIVE_KEYWORDS = {
    "beat": 2,
    "beats": 2,
    "surge": 2,
    "surges": 2,
    "upgrade": 2,
    "upgrades": 2,
    "outperform": 2,
    "buy": 1,
    "bullish": 2,
    "growth": 1,
    "strong": 1,
    "record": 2,
    "guidance": 1,
    "approval": 2,
    "wins": 1,
    "partnership": 1,
    "expands": 1,
    "demand": 1,
    "contract": 1,
}

NEGATIVE_KEYWORDS = {
    "miss": 2,
    "misses": 2,
    "slump": 2,
    "falls": 2,
    "fall": 2,
    "downgrade": 2,
    "downgrades": 2,
    "probe": 2,
    "investigation": 2,
    "lawsuit": 2,
    "fraud": 3,
    "warning": 2,
    "weak": 1,
    "delay": 1,
    "recall": 2,
    "bearish": 2,
    "sell": 1,
    "cuts": 2,
}

NSE_FALLBACK = [
    ("RELIANCE.NS", "Reliance Industries"),
    ("TCS.NS", "Tata Consultancy Services"),
    ("INFY.NS", "Infosys"),
    ("HDFCBANK.NS", "HDFC Bank"),
    ("ICICIBANK.NS", "ICICI Bank"),
    ("SBIN.NS", "State Bank of India"),
    ("LT.NS", "Larsen & Toubro"),
]

NASDAQ_FALLBACK = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("NVDA", "NVIDIA Corporation"),
    ("AMZN", "Amazon.com, Inc."),
]

NYSE_FALLBACK = [
    ("JPM", "JPMorgan Chase & Co."),
    ("WMT", "Walmart Inc."),
    ("KO", "The Coca-Cola Company"),
    ("BA", "The Boeing Company"),
]

BSE_FALLBACK = [
    ("RELIANCE.BO", "Reliance Industries"),
    ("TCS.BO", "Tata Consultancy Services"),
    ("INFY.BO", "Infosys"),
    ("HDFCBANK.BO", "HDFC Bank"),
]

FNO_INDEX_SYMBOLS = {
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "NIFTY",
    "NIFTYNXT50",
}

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class IndicatorSettings:
    bb_length: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_period: int = 14
    volume_multiplier: float = 1.25
    macd_weight: float = 1.0
    rsi_weight: float = 1.0
    vol_weight: float = 1.0
    confluence_percent: int = DEFAULT_SIGNAL_SENSITIVITY
    coil_days: int = 10
    coil_range_percent: float = 5.0
    squeeze_percentile: float = 25.0
    resistance_lookback: int = 30
    min_history_rows: int = 90
    ema_stop_window: int = 20

    def required_matches(self, criteria_count: int = 5) -> int:
        raw_required = math.ceil(criteria_count * (self.confluence_percent / 100.0))
        return max(1, min(criteria_count, raw_required))

    def total_weight(self) -> float:
        total = float(self.macd_weight) + float(self.rsi_weight) + float(self.vol_weight)
        return total if total > 0 else 1.0

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FilterSettings:
    market_cap_min: Optional[float] = None
    market_cap_max: Optional[float] = None
    sector: str = ""
    industry: str = ""
    min_rvol: Optional[float] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    within_52w_high_pct: Optional[float] = None
    gap_up_min_pct: Optional[float] = None
    min_volume: Optional[float] = None
    min_float_millions: Optional[float] = None
    max_float_millions: Optional[float] = None
    rsi_min: Optional[float] = None
    rsi_max: Optional[float] = None
    above_ema20: str = "Any"
    above_sma50: str = "Any"
    macd_signal: str = "Any"
    candle_pattern: str = "Any"
    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    min_score: Optional[float] = None
    trend_direction: str = "Any"


@dataclass(slots=True)
class ScanRequest:
    tickers: list[str] = field(default_factory=list)
    exchange: str = GLOBAL_EXCHANGE
    global_mode: bool = False
    full_exchange: bool = False
    turbo_mode: bool = True
    retain_non_matches: bool = False
    enrich_metadata: bool = True
    workers: int = DEFAULT_WORKERS
    debug_mode: bool = False


@dataclass(slots=True)
class NewsRequest:
    ticker: str
    company_name: str = ""
    exchange: str = GLOBAL_EXCHANGE
    sector: str = ""
    industry: str = ""
    limit: int = 12

    def build_terms(self) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for raw in (self.ticker, self.company_name, self.sector, self.industry, "stock"):
            clean = str(raw or "").strip()
            key = clean.lower()
            if not clean or key in seen:
                continue
            seen.add(key)
            terms.append(clean)
        return terms

    def query(self) -> str:
        return " ".join(self.build_terms())


@dataclass(slots=True)
class UniverseRecord:
    symbol: str
    company_name: str
    exchange: str
    sector: str = ""
    industry: str = ""
    market_cap: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ScanTelemetry:
    total_symbols: int
    scanned_symbols: int = 0
    passed_symbols: int = 0
    start_monotonic: float = field(default_factory=time.perf_counter)

    def snapshot(self) -> dict[str, Any]:
        elapsed = max(time.perf_counter() - self.start_monotonic, 0.0)
        rate = self.scanned_symbols / elapsed if elapsed > 0 else 0.0
        remaining = max(self.total_symbols - self.scanned_symbols, 0)
        eta = remaining / rate if rate > 0 else None
        pass_rate = (self.passed_symbols / self.scanned_symbols * 100.0) if self.scanned_symbols else 0.0
        return {
            "total": self.total_symbols,
            "scanned": self.scanned_symbols,
            "passed": self.passed_symbols,
            "elapsed_seconds": round(elapsed, 2),
            "elapsed_label": format_seconds(elapsed),
            "eta_seconds": round(eta, 2) if eta is not None else None,
            "eta_label": format_seconds(eta) if eta is not None else "--:--",
            "rate_per_second": round(rate, 2),
            "rate_per_minute": round(rate * 60.0, 2),
            "pass_rate": round(pass_rate, 2),
        }


@dataclass(slots=True)
class ScanSummary:
    total_symbols: int
    scanned_symbols: int
    matched_symbols: int
    error_symbols: int
    elapsed_seconds: float
    turbo_mode: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["elapsed_label"] = format_seconds(self.elapsed_seconds)
        return payload


def format_seconds(seconds: Optional[float]) -> str:
    if seconds is None or math.isinf(seconds) or math.isnan(seconds):
        return "--:--"
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def currency_symbol_for_exchange(exchange: str) -> str:
    return "INR" if _normalise_exchange(exchange) == "NSE" else "USD"


def format_currency(value: Optional[float], exchange: str, decimals: int = 2) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    if currency_symbol_for_exchange(exchange) == "INR":
        return f"INR {value:,.{decimals}f}"
    return f"${value:,.{decimals}f}"


def format_market_cap(value: Optional[float], exchange: str = "") -> str:
    if value is None or not math.isfinite(value):
        return "Unknown"
    absolute = abs(value)
    if _normalise_exchange(exchange) == "NSE":
        crores = value / 10_000_000
        if absolute >= 1_000_000_000_000:
            return f"INR {crores:,.0f}Cr"
        if absolute >= 1_000_000_000:
            return f"INR {crores:,.0f}Cr"
        if absolute >= 10_000_000:
            return f"INR {crores:,.1f}Cr"
        return f"INR {value:,.0f}"
    if absolute >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if absolute >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def format_volume(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def format_float_millions(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    return f"{value / 1_000_000:.1f}M"


def parse_ticker_text(raw_text: str) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for token in raw_text.replace(",", "\n").replace(";", "\n").split():
        clean = token.strip().upper()
        if not clean or clean in seen:
            continue
        tickers.append(clean)
        seen.add(clean)
    return tickers


def parse_market_cap_text(raw_text: str) -> Optional[float]:
    text = str(raw_text or "").strip().upper().replace(",", "")
    if not text:
        return None
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
    suffix = text[-1]
    try:
        if suffix in multipliers:
            return float(text[:-1]) * multipliers[suffix]
        return float(text)
    except ValueError:
        return None


def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _read_json_cache(name: str, max_age_hours: float) -> Optional[Any]:
    path = _cache_path(name)
    if not path.exists():
        return None
    age_seconds = time.time() - path.stat().st_mtime
    if age_seconds > max_age_hours * 3600:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json_cache(name: str, payload: Any) -> None:
    path = _cache_path(name)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _parse_cache_timestamp(raw_value: Any) -> Optional[datetime]:
    text = str(raw_value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_timestamped_cache(name: str, max_age_hours: float) -> tuple[Optional[Any], bool]:
    path = _cache_path(name)
    if not path.exists():
        return None, False
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, False
    payload = raw_payload
    timestamp = None
    if isinstance(raw_payload, dict) and "timestamp" in raw_payload and "payload" in raw_payload:
        payload = raw_payload.get("payload")
        timestamp = _parse_cache_timestamp(raw_payload.get("timestamp"))
    if timestamp is None:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_hours = max((datetime.now(timezone.utc) - timestamp).total_seconds() / 3600.0, 0.0)
    return payload, age_hours <= max_age_hours


def _write_timestamped_cache(name: str, payload: Any) -> None:
    _write_json_cache(
        name,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        },
    )


def _replace_fno_symbols(symbols: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        clean = str(raw or "").strip().upper()
        if clean and clean not in seen:
            unique.append(clean)
            seen.add(clean)
    FNO_SYMBOLS[:] = unique or list(DEFAULT_FNO_SYMBOLS)
    return list(FNO_SYMBOLS)


def load_ui_state() -> dict[str, Any]:
    cached = _read_json_cache(UI_STATE_CACHE_NAME, 24 * 365 * 5)
    return dict(cached or {})


def save_ui_state(payload: dict[str, Any]) -> None:
    _write_json_cache(UI_STATE_CACHE_NAME, payload)


def _chunked(items: Sequence[str], chunk_size: int) -> Iterator[list[str]]:
    size = max(int(chunk_size), 1)
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _coerce_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


def calculate_weighted_score(
    settings: "IndicatorSettings",
    *,
    macd_strength: float,
    rsi_strength: float,
    volume_strength: float,
) -> dict[str, Any]:
    component_strengths = {
        "macd": round(_clamp(macd_strength), 4),
        "rsi": round(_clamp(rsi_strength), 4),
        "volume": round(_clamp(volume_strength), 4),
    }
    weighted_score = (
        float(settings.macd_weight) * component_strengths["macd"]
        + float(settings.rsi_weight) * component_strengths["rsi"]
        + float(settings.vol_weight) * component_strengths["volume"]
    )
    component_contributions = {
        "macd": round(float(settings.macd_weight) * component_strengths["macd"], 4),
        "rsi": round(float(settings.rsi_weight) * component_strengths["rsi"], 4),
        "volume": round(float(settings.vol_weight) * component_strengths["volume"], 4),
    }
    max_score = settings.total_weight()
    required_score = round(max_score * (settings.confluence_percent / 100.0), 2)
    confluence_percent = (weighted_score / max_score * 100.0) if max_score else 0.0
    return {
        "score": round(weighted_score, 2),
        "max_score": round(max_score, 2),
        "required_score": required_score,
        "confluence_percent": round(confluence_percent, 1),
        "passes_threshold": confluence_percent >= settings.confluence_percent,
        "signal_strengths": component_strengths,
        "component_contributions": component_contributions,
        "score_formula": "W_macd*S_macd + W_rsi*S_rsi + W_vol*S_vol",
        "score_breakdown": (
            f"{float(settings.macd_weight):.2f}*{component_strengths['macd']:.2f} + "
            f"{float(settings.rsi_weight):.2f}*{component_strengths['rsi']:.2f} + "
            f"{float(settings.vol_weight):.2f}*{component_strengths['volume']:.2f}"
        ),
        "weights": {
            "macd": round(float(settings.macd_weight), 2),
            "rsi": round(float(settings.rsi_weight), 2),
            "volume": round(float(settings.vol_weight), 2),
        },
    }


def _choose_first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] not in (None, "", "N/A"):
            return source[key]
    return None


def _normalise_exchange(exchange: str) -> str:
    clean = (exchange or "").upper().strip()
    return clean if clean in EXCHANGE_CONFIG or clean == GLOBAL_EXCHANGE else GLOBAL_EXCHANGE


def get_exchange_options() -> list[str]:
    return list(EXCHANGE_CONFIG.keys())


def load_custom_watchlist_presets() -> dict[str, list[str]]:
    payload = load_ui_state()
    presets = payload.get("custom_watchlist_presets", {})
    cleaned: dict[str, list[str]] = {}
    for raw_name, raw_symbols in dict(presets or {}).items():
        name = str(raw_name or "").strip()
        symbols = parse_ticker_text(" ".join(str(item) for item in list(raw_symbols or [])))
        if name and symbols:
            cleaned[name] = symbols
    return cleaned


def save_custom_watchlist_preset(name: str, symbols: Sequence[str]) -> None:
    payload = load_ui_state()
    presets = dict(payload.get("custom_watchlist_presets", {}) or {})
    clean_name = str(name or "").strip()
    clean_symbols = parse_ticker_text(" ".join(str(item) for item in list(symbols or [])))
    if clean_name and clean_symbols:
        presets[clean_name] = clean_symbols
    payload["custom_watchlist_presets"] = presets
    save_ui_state(payload)


def get_preset_names() -> list[str]:
    names = list(PRESET_LISTS.keys())
    for custom_name in load_custom_watchlist_presets():
        if custom_name not in names:
            names.append(custom_name)
    return names


def load_preset_list(name: str) -> list[str]:
    if name == "Custom":
        payload = load_ui_state()
        persisted = parse_ticker_text(" ".join(str(item) for item in list(payload.get("watchlist_symbols", []) or [])))
        return persisted or list(DEFAULT_WATCHLIST)
    custom_presets = load_custom_watchlist_presets()
    if name in custom_presets:
        return list(custom_presets[name])
    return list(PRESET_LISTS.get(name, DEFAULT_WATCHLIST))


class HttpClient:
    """Plain HTTP client: identifies itself honestly, retries with backoff, no proxies."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def build_session(self) -> requests.Session:
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=24, pool_maxsize=24)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        return session

    def get_text(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
        rotate_on_error: bool = True,  # kept for call compatibility; ignored (no proxies)
    ) -> str:
        last_error: Optional[Exception] = None
        attempts = 3
        for attempt in range(1, attempts + 1):
            session = self.build_session()
            if headers:
                session.headers.update(headers)
            try:
                response = session.get(url, params=params, timeout=timeout or self.timeout)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(0.35 * (2 ** (attempt - 1)), 3.0))
        raise RuntimeError(f"HTTP request failed for {url}: {last_error}") from last_error


    def get_json(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
        rotate_on_error: bool = True,
    ) -> Any:
        text = self.get_text(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            rotate_on_error=rotate_on_error,
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc


class FinnhubClient:
    COUNTRY_BY_EXCHANGE = {
        "NASDAQ": "US",
        "NYSE": "US",
        "NSE": "IN",
        "BSE": "IN",
    }

    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http = http_client or HttpClient()
        self.api_key = str(os.environ.get("FINNHUB_API_KEY", "")).strip()
        self._client: Any = None
        self._catalog_cache: dict[str, list[dict[str, Any]]] = {}
        self._catalog_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _client_instance(self) -> Any:
        if not self.enabled:
            raise RuntimeError("FINNHUB_API_KEY is not configured.")
        if self._client is None:
            finnhub = _get_finnhub_module()
            self._client = finnhub.Client(api_key=self.api_key)
        return self._client

    def _catalog_for_exchange(self, exchange: str) -> list[dict[str, Any]]:
        clean_exchange = infer_exchange_from_symbol(exchange) if "." in exchange else str(exchange or "").upper().strip()
        country = self.COUNTRY_BY_EXCHANGE.get(clean_exchange, "US")
        with self._catalog_lock:
            cached = self._catalog_cache.get(country)
            if cached is not None:
                return cached
        try:
            catalog = list(self._client_instance().stock_symbols(country) or [])
        except Exception:
            catalog = []
        with self._catalog_lock:
            self._catalog_cache[country] = catalog
        return catalog

    def _candidate_symbols(self, symbol: str) -> list[str]:
        clean_symbol = str(symbol or "").strip().upper()
        if not clean_symbol:
            return []
        exchange = infer_exchange_from_symbol(clean_symbol)
        base_symbol = clean_symbol.replace(".NS", "").replace(".BO", "")
        candidates: list[str] = []

        for item in self._catalog_for_exchange(exchange):
            display_symbol = str(item.get("displaySymbol") or "").strip().upper()
            provider_symbol = str(item.get("symbol") or display_symbol).strip().upper()
            if display_symbol in {clean_symbol, base_symbol} or provider_symbol in {clean_symbol, base_symbol}:
                candidates.append(provider_symbol)

        if exchange in {"NSE", "BSE"}:
            candidates.extend([base_symbol, f"{exchange}:{base_symbol}", clean_symbol])
        else:
            candidates.extend([clean_symbol, base_symbol])

        unique: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            candidate = str(raw or "").strip().upper()
            if candidate and candidate not in seen:
                unique.append(candidate)
                seen.add(candidate)
        return unique

    def fetch_candles(
        self,
        symbol: str,
        resolution: str,
        from_timestamp: int,
        to_timestamp: int,
    ) -> pd.DataFrame:
        if not self.enabled:
            return pd.DataFrame()
        last_error: Optional[Exception] = None
        client = self._client_instance()
        for candidate in self._candidate_symbols(symbol):
            try:
                payload = client.stock_candles(candidate, resolution, int(from_timestamp), int(to_timestamp))
            except Exception as exc:
                last_error = exc
                continue
            if not isinstance(payload, dict) or str(payload.get("s", "")).lower() != "ok":
                continue
            frame = pd.DataFrame(
                {
                    "Date": pd.to_datetime(payload.get("t", []), unit="s", utc=True, errors="coerce"),
                    "Open": payload.get("o", []),
                    "High": payload.get("h", []),
                    "Low": payload.get("l", []),
                    "Close": payload.get("c", []),
                    "Volume": payload.get("v", []),
                }
            )
            if frame.empty:
                continue
            frame = frame.dropna(subset=["Date"]).set_index("Date")
            cleaned = _clean_history_frame(frame)
            if not cleaned.empty:
                return cleaned
        if last_error is not None:
            raise RuntimeError(f"Finnhub candle download failed for {symbol}: {last_error}") from last_error
        return pd.DataFrame()


class ExchangeUniverseLoader:
    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http = http_client or HttpClient()
        self._universe_cache: dict[str, list[UniverseRecord]] = {}
        self._universe_cache_stale: dict[str, bool] = {}
        self._fno_cache: list[str] = []
        self._fno_cache_stale = False
        self._search_cache: dict[str, list[tuple[str, UniverseRecord]]] = {}
        self._lock = threading.Lock()

    def _deserialize_records(self, payload: list[dict[str, Any]]) -> list[UniverseRecord]:
        return [UniverseRecord(**item) for item in payload]

    def _serialize_records(self, records: list[UniverseRecord]) -> list[dict[str, Any]]:
        return [record.to_dict() for record in records]

    def _cache_name(self, exchange: str) -> str:
        mapping = {
            "NASDAQ": "nasdaq_symbols.json",
            "NYSE": "nyse_symbols.json",
            "NSE": "nse_symbols.json",
            "BSE": "bse_symbols.json",
            "LSE": "lse_symbols.json",
        }
        return mapping.get(_normalise_exchange(exchange), f"universe_{exchange.lower()}.json")

    def _fno_cache_name(self) -> str:
        return "fno_symbols.json"

    def _set_exchange_cache(self, exchange: str, records: list[UniverseRecord], *, stale: bool) -> None:
        with self._lock:
            self._universe_cache[exchange] = records
            self._universe_cache_stale[exchange] = stale
            self._search_cache.pop(exchange, None)

    def _set_fno_cache(self, symbols: Sequence[str], *, stale: bool) -> list[str]:
        updated = _replace_fno_symbols(symbols)
        with self._lock:
            self._fno_cache = list(updated)
            self._fno_cache_stale = stale
        return updated

    def _cached_records(self, exchange: str) -> tuple[list[UniverseRecord], bool]:
        cache_name = self._cache_name(exchange)
        payload, is_fresh = _read_timestamped_cache(cache_name, UNIVERSE_CACHE_HOURS)
        legacy_name = f"universe_{_normalise_exchange(exchange).lower()}.json"
        if not isinstance(payload, list) and legacy_name != cache_name:
            payload, is_fresh = _read_timestamped_cache(legacy_name, UNIVERSE_CACHE_HOURS)
        if not isinstance(payload, list):
            return [], False
        return self._deserialize_records(payload), is_fresh

    def _cached_symbols(self) -> tuple[list[str], bool]:
        payload, is_fresh = _read_timestamped_cache(self._fno_cache_name(), UNIVERSE_CACHE_HOURS)
        if not isinstance(payload, list):
            return [], False
        return _replace_fno_symbols(payload), is_fresh

    def load_exchange_universe_state(
        self,
        exchange: str,
        *,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> tuple[list[UniverseRecord], bool]:
        clean_exchange = _normalise_exchange(exchange)
        if clean_exchange == GLOBAL_EXCHANGE:
            return self.load_global_universe(force_refresh=force_refresh), False
        with self._lock:
            if not force_refresh and clean_exchange in self._universe_cache:
                return list(self._universe_cache[clean_exchange]), self._universe_cache_stale.get(clean_exchange, False)

        cached_records, cached_fresh = self._cached_records(clean_exchange)
        if cached_records and not force_refresh and allow_stale:
            is_stale = not cached_fresh
            self._set_exchange_cache(clean_exchange, cached_records, stale=is_stale)
            return list(cached_records), is_stale

        fetchers = {
            "NASDAQ": self._fetch_nasdaq_universe,
            "NYSE": self._fetch_nyse_universe,
            "NSE": self._fetch_nse_universe,
            "BSE": self._fetch_bse_universe,
            "LSE": self._fetch_lse_universe,
        }

        try:
            records = fetchers[clean_exchange]()
        except Exception:
            if cached_records:
                is_stale = not cached_fresh
                self._set_exchange_cache(clean_exchange, cached_records, stale=is_stale)
                return list(cached_records), is_stale
            records = self._fallback_universe(clean_exchange)
            self._set_exchange_cache(clean_exchange, records, stale=False)
            return list(records), False

        if records:
            _write_timestamped_cache(self._cache_name(clean_exchange), self._serialize_records(records))
        self._set_exchange_cache(clean_exchange, records, stale=False)
        return list(records), False

    def load_exchange_universe(self, exchange: str, force_refresh: bool = False) -> list[UniverseRecord]:
        records, _is_stale = self.load_exchange_universe_state(exchange, force_refresh=force_refresh, allow_stale=True)
        return records

    def load_fno_symbols_state(
        self,
        *,
        force_refresh: bool = False,
        allow_stale: bool = True,
    ) -> tuple[list[str], bool]:
        with self._lock:
            if not force_refresh and self._fno_cache:
                return list(self._fno_cache), self._fno_cache_stale

        cached_symbols, cached_fresh = self._cached_symbols()
        if cached_symbols and not force_refresh and allow_stale:
            is_stale = not cached_fresh
            self._set_fno_cache(cached_symbols, stale=is_stale)
            return list(cached_symbols), is_stale

        try:
            symbols = self._fetch_fno_symbols()
        except Exception:
            if cached_symbols:
                is_stale = not cached_fresh
                self._set_fno_cache(cached_symbols, stale=is_stale)
                return list(cached_symbols), is_stale
            fallback = self._set_fno_cache(DEFAULT_FNO_SYMBOLS, stale=False)
            return list(fallback), False

        if symbols:
            _write_timestamped_cache(self._fno_cache_name(), list(symbols))
        updated = self._set_fno_cache(symbols, stale=False)
        return list(updated), False

    def load_fno_symbols(self, force_refresh: bool = False) -> list[str]:
        symbols, _is_stale = self.load_fno_symbols_state(force_refresh=force_refresh, allow_stale=True)
        return symbols

    def warm_start(self, force_refresh: bool = False) -> dict[str, int]:
        counts: dict[str, int] = {}
        for exchange in FULL_UNIVERSE_EXCHANGES:
            counts[exchange] = len(self.load_exchange_universe(exchange, force_refresh=force_refresh))
        counts["F&O"] = len(self.load_fno_symbols(force_refresh=force_refresh))
        return counts

    def load_global_universe(self, force_refresh: bool = False) -> list[UniverseRecord]:
        combined: list[UniverseRecord] = []
        seen: set[str] = set()
        for exchange in FULL_UNIVERSE_EXCHANGES:
            for record in self.load_exchange_universe(exchange, force_refresh=force_refresh):
                if record.symbol in seen:
                    continue
                combined.append(record)
                seen.add(record.symbol)
        with self._lock:
            self._universe_cache[GLOBAL_EXCHANGE] = combined
            self._universe_cache_stale[GLOBAL_EXCHANGE] = False
            self._search_cache.pop(GLOBAL_EXCHANGE, None)
        return combined

    def find_record(self, symbol: str, exchange_hint: str = GLOBAL_EXCHANGE) -> Optional[UniverseRecord]:
        clean_symbol = symbol.strip().upper()
        if not clean_symbol:
            return None
        search_exchanges = [exchange_hint] if exchange_hint in EXCHANGE_CONFIG else []
        search_exchanges.extend([GLOBAL_EXCHANGE, "NASDAQ", "NYSE", "NSE", "BSE", "LSE"])
        seen_exchanges: set[str] = set()
        for exchange in search_exchanges:
            if exchange in seen_exchanges:
                continue
            seen_exchanges.add(exchange)
            universe = self.load_exchange_universe(exchange) if exchange != GLOBAL_EXCHANGE else self.load_global_universe()
            for record in universe:
                if record.symbol == clean_symbol:
                    return record
        return None

    def search(self, query: str, exchange: str = GLOBAL_EXCHANGE, limit: int = 8) -> list[dict[str, Any]]:
        clean_query = query.strip()
        if not clean_query:
            return []
        clean_exchange = _normalise_exchange(exchange)
        universe = self.load_global_universe() if clean_exchange == GLOBAL_EXCHANGE else self.load_exchange_universe(clean_exchange)
        if not universe:
            return []

        direct_hits: list[dict[str, Any]] = []
        query_upper = clean_query.upper()
        for record in universe:
            haystack = f"{record.symbol} {record.company_name}".upper()
            if query_upper in haystack:
                direct_hits.append(
                    {
                        "symbol": record.symbol,
                        "company_name": record.company_name,
                        "exchange": record.exchange,
                        "score": 100,
                    }
                )
            if len(direct_hits) >= limit:
                return direct_hits[:limit]

        choices = self._search_choices(clean_exchange, universe)
        results: list[dict[str, Any]] = []
        seen_symbols: set[str] = set()

        if fuzz_process and fuzz:
            extracted = fuzz_process.extract(
                clean_query,
                {label: record for label, record in choices},
                scorer=fuzz.WRatio,
                limit=max(limit * 2, 10),
            )
            for _label, score, record in extracted:
                if score < 50 or record.symbol in seen_symbols:
                    continue
                results.append(
                    {
                        "symbol": record.symbol,
                        "company_name": record.company_name,
                        "exchange": record.exchange,
                        "score": int(score),
                    }
                )
                seen_symbols.add(record.symbol)
                if len(results) >= limit:
                    break
        else:
            ranked = sorted(
                choices,
                key=lambda item: _sequence_similarity(clean_query.upper(), item[0]),
                reverse=True,
            )
            for label, record in ranked[: limit * 2]:
                score = int(_sequence_similarity(clean_query.upper(), label) * 100)
                if score < 45 or record.symbol in seen_symbols:
                    continue
                results.append(
                    {
                        "symbol": record.symbol,
                        "company_name": record.company_name,
                        "exchange": record.exchange,
                        "score": score,
                    }
                )
                seen_symbols.add(record.symbol)
                if len(results) >= limit:
                    break

        return direct_hits + results[: max(limit - len(direct_hits), 0)]

    def _search_choices(self, exchange: str, universe: list[UniverseRecord]) -> list[tuple[str, UniverseRecord]]:
        with self._lock:
            cached = self._search_cache.get(exchange)
            if cached:
                return cached
        choices = [
            (f"{record.symbol} {record.company_name} {record.exchange}".upper(), record)
            for record in universe
        ]
        with self._lock:
            self._search_cache[exchange] = choices
        return choices

    def _looks_like_common_equity(self, company_name: str) -> bool:
        label = str(company_name or "").upper()
        include_terms = ("COMMON STOCK", "COMMON SHARES", "ORDINARY SHARES", "ADS", "ADR")
        exclude_terms = (
            "PREFERRED",
            "DEPOSITARY SHARES",
            "UNIT",
            "UNITS",
            "RIGHTS",
            "WARRANT",
            "TRUST",
            "FUND",
            "NOTE",
            "NOTES",
            "BOND",
        )
        return any(term in label for term in include_terms) and not any(term in label for term in exclude_terms)

    def _fetch_nasdaq_universe(self) -> list[UniverseRecord]:
        url = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
        text = self.http.get_text(url)
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        records: list[UniverseRecord] = []
        for row in reader:
            symbol = (row.get("Symbol") or "").strip().upper()
            if not symbol or symbol == "FILE CREATION TIME":
                continue
            if (row.get("Test Issue") or "").strip().upper() == "Y":
                continue
            if (row.get("ETF") or "").strip().upper() == "Y":
                continue
            company_name = (row.get("Security Name") or symbol).strip()
            if not self._looks_like_common_equity(company_name):
                continue
            records.append(
                UniverseRecord(
                    symbol=symbol,
                    company_name=company_name,
                    exchange="NASDAQ",
                )
            )
        return records

    def _fetch_nyse_universe(self) -> list[UniverseRecord]:
        url = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
        text = self.http.get_text(url)
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        records: list[UniverseRecord] = []
        for row in reader:
            exchange_code = (row.get("Exchange") or "").strip().upper()
            if exchange_code != "N":
                continue
            if (row.get("Test Issue") or "").strip().upper() == "Y":
                continue
            if (row.get("ETF") or "").strip().upper() == "Y":
                continue
            symbol = ((row.get("ACT Symbol") or row.get("CQS Symbol") or "")).strip().upper()
            if not symbol or symbol == "FILE CREATION TIME":
                continue
            company_name = (row.get("Security Name") or symbol).strip()
            if not self._looks_like_common_equity(company_name):
                continue
            records.append(
                UniverseRecord(
                    symbol=symbol,
                    company_name=company_name,
                    exchange="NYSE",
                )
            )
        return records

    def _fetch_nse_universe(self) -> list[UniverseRecord]:
        url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {"Referer": "https://www.nseindia.com/"}
        text = self.http.get_text(url, headers=headers)
        reader = csv.DictReader(io.StringIO(text))
        records: list[UniverseRecord] = []
        for row in reader:
            series = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
            symbol = ((row.get("SYMBOL") or "")).strip().upper()
            if not symbol or series not in {"EQ", "BE"}:
                continue
            company_name = (row.get("NAME OF COMPANY") or symbol).strip()
            records.append(
                UniverseRecord(
                    symbol=f"{symbol}.NS",
                    company_name=company_name,
                    exchange="NSE",
                )
            )
        return records

    def _fetch_bse_universe(self) -> list[UniverseRecord]:
        url = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Host": "api.bseindia.com",
            "Referer": "https://www.bseindia.com/corporates/ann.html",
        }
        payload = self.http.get_json(
            url,
            params={"segment": "Equity", "status": "Active"},
            headers=headers,
            rotate_on_error=False,
        )
        raw_items = payload
        if isinstance(payload, dict):
            for key in ("Table", "data", "Data", "results"):
                if isinstance(payload.get(key), list):
                    raw_items = payload.get(key)
                    break
        if not isinstance(raw_items, list):
            raise RuntimeError("BSE scrip list returned an unexpected payload.")

        records: list[UniverseRecord] = []
        seen: set[str] = set()
        for row in raw_items:
            if not isinstance(row, dict):
                continue
            symbol = str(
                row.get("scrip_id")
                or row.get("Symbol")
                or row.get("symbol")
                or row.get("Security Id")
                or row.get("SecurityID")
                or ""
            ).strip().upper()
            if not symbol:
                continue
            final_symbol = f"{symbol}.BO"
            if final_symbol in seen:
                continue
            company_name = str(
                row.get("Scrip_Name")
                or row.get("Security Name")
                or row.get("CompanyName")
                or row.get("name")
                or symbol
            ).strip()
            records.append(
                UniverseRecord(
                    symbol=final_symbol,
                    company_name=company_name or symbol,
                    exchange="BSE",
                )
            )
            seen.add(final_symbol)
        return records

    def _fetch_lse_universe(self) -> list[UniverseRecord]:
        return self._fallback_universe("LSE")

    def _fetch_fno_symbols(self) -> list[str]:
        url = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
        headers = {"Referer": "https://www.nseindia.com/"}
        text = self.http.get_text(url, headers=headers)
        reader = csv.reader(io.StringIO(text))
        header = next(reader, [])
        normalized_header = [str(item or "").strip().upper() for item in header]
        try:
            symbol_index = normalized_header.index("SYMBOL")
        except ValueError as exc:
            raise RuntimeError("NSE F&O lot-size file did not expose a SYMBOL column.") from exc
        symbols: list[str] = []
        seen: set[str] = set()
        for row in reader:
            raw_symbol = str(row[symbol_index] if symbol_index < len(row) else "").strip().upper()
            if not raw_symbol or raw_symbol == "SYMBOL" or raw_symbol in FNO_INDEX_SYMBOLS:
                continue
            final_symbol = f"{raw_symbol}.NS"
            if final_symbol in seen:
                continue
            symbols.append(final_symbol)
            seen.add(final_symbol)
        return symbols

    def _fallback_universe(self, exchange: str) -> list[UniverseRecord]:
        fallback_names = {
            "NASDAQ": NASDAQ_FALLBACK,
            "NYSE": NYSE_FALLBACK,
            "NSE": NSE_FALLBACK,
            "BSE": BSE_FALLBACK,
            "LSE": [("VOD.L", "Vodafone Group"), ("HSBA.L", "HSBC Holdings")],
        }
        values = fallback_names.get(exchange, [])
        return [UniverseRecord(symbol=symbol, company_name=name, exchange=exchange) for symbol, name in values]


class NewsFetcher:
    def __init__(self, http_client: Optional[HttpClient] = None) -> None:
        self.http = http_client or HttpClient()

    def build_request(
        self,
        ticker: str,
        *,
        company_name: str = "",
        exchange: str = GLOBAL_EXCHANGE,
        sector: str = "",
        industry: str = "",
        limit: int = 12,
    ) -> NewsRequest:
        return NewsRequest(
            ticker=ticker.strip().upper(),
            company_name=company_name,
            exchange=exchange,
            sector=sector,
            industry=industry,
            limit=max(int(limit), 1),
        )

    def fetch_ticker_news(
        self,
        ticker: str,
        company_name: str = "",
        *,
        exchange: str = GLOBAL_EXCHANGE,
        sector: str = "",
        industry: str = "",
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        request = self.build_request(
            ticker,
            company_name=company_name,
            exchange=exchange,
            sector=sector,
            industry=industry,
            limit=limit,
        )
        return self.fetch_for_request(request)

    def fetch_for_request(self, request: NewsRequest) -> list[dict[str, Any]]:
        clean_ticker = request.ticker.strip().upper()
        if not clean_ticker:
            return []

        cache_key = f"news_{clean_ticker.replace('.', '_')}.json"
        cached = _read_json_cache(cache_key, NEWS_CACHE_HOURS)
        if cached:
            return list(cached)[: request.limit]

        query = request.query()
        sources = [
            ("Yahoo Finance", f"https://finance.yahoo.com/rss/headline?s={quote_plus(clean_ticker)}"),
            (
                "Google News",
                f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en",
            ),
        ]

        merged: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for source_name, url in sources:
            try:
                text = self.http.get_text(url)
            except Exception:
                continue
            for item in self._parse_rss(text, default_source=source_name):
                title_key = item["title"].strip().lower()
                if title_key in seen_titles:
                    continue
                merged.append(
                    item
                    | {
                        "query": query,
                        "endpoint": url,
                        "ticker": clean_ticker,
                    }
                )
                seen_titles.add(title_key)

        merged.sort(key=lambda item: item.get("published_at") or "", reverse=True)
        final_items = merged[: request.limit]
        _write_json_cache(cache_key, final_items)
        return final_items

    def _parse_rss(self, xml_text: str, default_source: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        items: list[dict[str, Any]] = []
        for element in root.findall(".//item"):
            title = (element.findtext("title") or "").strip()
            link = (element.findtext("link") or "").strip()
            pub_date = (element.findtext("pubDate") or "").strip()
            source = (element.findtext("source") or default_source).strip()
            if not title:
                continue
            published = self._parse_date(pub_date)
            tag, score = classify_headline_sentiment(title)
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": source or default_source,
                    "published_at": published.isoformat() if published else "",
                    "published_label": published.astimezone().strftime("%d %b %Y %H:%M") if published else "Unknown",
                    "sentiment_tag": tag,
                    "sentiment_score": score,
                }
            )
        return items

    def _parse_date(self, raw_value: str) -> Optional[datetime]:
        if not raw_value:
            return None
        patterns = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
        ]
        for pattern in patterns:
            try:
                parsed = datetime.strptime(raw_value, pattern)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except ValueError:
                continue
        return None


def classify_headline_sentiment(headline: str) -> tuple[str, int]:
    title = headline.lower()
    score = 0
    for keyword, weight in POSITIVE_KEYWORDS.items():
        if keyword in title:
            score += weight
    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in title:
            score -= weight
    if score > 0:
        return "Bullish", score
    if score < 0:
        return "Bearish", score
    return "Neutral", score


def _sequence_similarity(left: str, right: str) -> float:
    left_set = set(left.split())
    right_set = set(right.split())
    if not left_set or not right_set:
        return 0.0
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    if union == 0:
        return 0.0
    return intersection / union


def _serialise_frame(df: pd.DataFrame) -> dict[str, list[Any]]:
    payload = df.reset_index().rename(columns={df.reset_index().columns[0]: "Date"})
    payload["Date"] = payload["Date"].astype(str)
    return payload.to_dict(orient="list")


def _deserialise_frame(payload: dict[str, list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(payload)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["Date"]).set_index("Date")
    return frame


def _clean_history_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(part[-1] if isinstance(part, tuple) else part) for part in frame.columns]

    normalized_columns = {str(column).lower().replace(" ", "_"): column for column in frame.columns}
    rename_map = {}
    for canonical in ("open", "high", "low", "close", "volume"):
        original = normalized_columns.get(canonical)
        if original is not None:
            rename_map[original] = canonical.capitalize()
    frame = frame.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(column in frame.columns for column in required):
        return pd.DataFrame()

    frame = frame[required]
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame = frame.astype(
        {
            "Open": "float32",
            "High": "float32",
            "Low": "float32",
            "Close": "float32",
            "Volume": "float32",
        },
        copy=False,
    )
    frame = frame.dropna(subset=["Close", "High", "Low"])
    if frame.empty:
        return pd.DataFrame()

    frame.index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    frame = frame[~frame.index.isna()]
    frame = frame.sort_index()
    return frame


def _extract_symbol_frame(downloaded: pd.DataFrame, symbol: str, batch_size: int) -> pd.DataFrame:
    if downloaded is None or downloaded.empty:
        return pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        columns = downloaded.columns
        if symbol in columns.get_level_values(0):
            frame = downloaded.xs(symbol, axis=1, level=0, drop_level=True)
        elif symbol in columns.get_level_values(-1):
            frame = downloaded.xs(symbol, axis=1, level=-1, drop_level=True)
        else:
            return pd.DataFrame()
    else:
        frame = downloaded.copy() if batch_size == 1 else pd.DataFrame()
    return _clean_history_frame(frame)


def _series_payload(series: pd.Series, digits: int = 4) -> list[Optional[float]]:
    values: list[Optional[float]] = []
    for raw in series.tolist():
        if pd.isna(raw):
            values.append(None)
        else:
            values.append(round(float(raw), digits))
    return values


def _compact_debug_frame(frame: pd.DataFrame, rows: int = 4) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    debug_rows: list[dict[str, Any]] = []
    window = _clean_history_frame(frame).tail(rows)
    for index, row in window.iterrows():
        debug_rows.append(
            {
                "date": index.isoformat() if hasattr(index, "isoformat") else str(index),
                "open": round(float(row.get("Open", 0.0)), 4) if pd.notna(row.get("Open")) else None,
                "high": round(float(row.get("High", 0.0)), 4) if pd.notna(row.get("High")) else None,
                "low": round(float(row.get("Low", 0.0)), 4) if pd.notna(row.get("Low")) else None,
                "close": round(float(row.get("Close", 0.0)), 4) if pd.notna(row.get("Close")) else None,
                "volume": round(float(row.get("Volume", 0.0)), 2) if pd.notna(row.get("Volume")) else None,
            }
        )
    return debug_rows


def _detect_candle_pattern(df: pd.DataFrame) -> str:
    if df is None or len(df) < 2:
        return "Any"
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    latest_open = float(latest["Open"])
    latest_close = float(latest["Close"])
    latest_high = float(latest["High"])
    latest_low = float(latest["Low"])
    previous_open = float(previous["Open"])
    previous_close = float(previous["Close"])

    body = abs(latest_close - latest_open)
    candle_range = max(latest_high - latest_low, 1e-9)
    upper_wick = latest_high - max(latest_open, latest_close)
    lower_wick = min(latest_open, latest_close) - latest_low

    if body <= candle_range * 0.1:
        return "Doji"
    if lower_wick >= body * 2.0 and upper_wick <= body:
        return "Hammer"
    bullish_engulfing = (
        latest_close > latest_open
        and previous_close < previous_open
        and latest_close >= previous_open
        and latest_open <= previous_close
    )
    bearish_engulfing = (
        latest_close < latest_open
        and previous_close > previous_open
        and latest_open >= previous_close
        and latest_close <= previous_open
    )
    if bullish_engulfing or bearish_engulfing:
        return "Engulfing"
    return "Any"


def _trend_label(close: pd.Series, ema20: pd.Series, sma50: pd.Series) -> str:
    latest_close = float(close.iloc[-1])
    latest_ema20 = float(ema20.iloc[-1]) if pd.notna(ema20.iloc[-1]) else latest_close
    latest_sma50 = float(sma50.iloc[-1]) if pd.notna(sma50.iloc[-1]) else latest_close
    prior_ema20 = float(ema20.iloc[-5]) if len(ema20) >= 5 and pd.notna(ema20.iloc[-5]) else latest_ema20
    if latest_close >= latest_ema20 >= latest_sma50 and latest_ema20 >= prior_ema20:
        return "Uptrend"
    if latest_close <= latest_ema20 <= latest_sma50 and latest_ema20 <= prior_ema20:
        return "Downtrend"
    return "Sideways"


def _metric_line(label: str, value: str, passed: bool) -> str:
    marker = "PASS" if passed else "FAIL"
    return f"{marker} {label}: {value}"


def _build_symbol_diagnostics(
    settings: IndicatorSettings,
    *,
    latest_close: float,
    latest_volume: float,
    effective_rvol: float,
    raw_rvol: Optional[float],
    latest_rsi: float,
    latest_macd: float,
    latest_macd_signal: float,
    trend: str,
    signal_score: float,
    breakout_level: float,
    above_ema20: bool,
    above_sma50: bool,
    candle_pattern: str,
) -> dict[str, Any]:
    pass_reasons: list[str] = []
    fail_reasons: list[str] = []
    diagnostic_lines: list[str] = []

    score_pass = signal_score >= settings.confluence_percent
    score_line = _metric_line(
        "Score",
        f"{signal_score:.1f} vs sensitivity {settings.confluence_percent}",
        score_pass,
    )
    diagnostic_lines.append(score_line)
    (pass_reasons if score_pass else fail_reasons).append(score_line)

    rvol_threshold = max(float(settings.volume_multiplier), 1.0)
    rvol_pass = effective_rvol >= rvol_threshold
    rvol_source = "neutralized to 1.00x" if raw_rvol is None else f"{effective_rvol:.2f}x"
    rvol_line = _metric_line(
        "RVOL",
        f"{rvol_source} vs threshold {rvol_threshold:.2f}x",
        rvol_pass,
    )
    diagnostic_lines.append(rvol_line)
    (pass_reasons if rvol_pass else fail_reasons).append(rvol_line)

    rsi_pass = 40.0 <= latest_rsi <= 75.0
    rsi_line = _metric_line("RSI", f"{latest_rsi:.2f} vs target band 40.00-75.00", rsi_pass)
    diagnostic_lines.append(rsi_line)
    (pass_reasons if rsi_pass else fail_reasons).append(rsi_line)

    macd_pass = latest_macd >= latest_macd_signal
    macd_line = _metric_line("MACD", f"{latest_macd:.4f} vs signal {latest_macd_signal:.4f}", macd_pass)
    diagnostic_lines.append(macd_line)
    (pass_reasons if macd_pass else fail_reasons).append(macd_line)

    trend_pass = trend == "Uptrend"
    trend_line = _metric_line("Trend", trend, trend_pass)
    diagnostic_lines.append(trend_line)
    (pass_reasons if trend_pass else fail_reasons).append(trend_line)

    breakout_pass = latest_close >= breakout_level
    breakout_line = _metric_line("Breakout", f"{latest_close:.2f} vs {breakout_level:.2f}", breakout_pass)
    diagnostic_lines.append(breakout_line)
    (pass_reasons if breakout_pass else fail_reasons).append(breakout_line)

    ema20_line = _metric_line("20 EMA", "Price above 20 EMA" if above_ema20 else "Price below 20 EMA", above_ema20)
    sma50_line = _metric_line("50 SMA", "Price above 50 SMA" if above_sma50 else "Price below 50 SMA", above_sma50)
    diagnostic_lines.extend([ema20_line, sma50_line])
    (pass_reasons if above_ema20 else fail_reasons).append(ema20_line)
    (pass_reasons if above_sma50 else fail_reasons).append(sma50_line)

    candle_line = _metric_line("Candle", candle_pattern, candle_pattern in {"Hammer", "Engulfing", "Doji"})
    diagnostic_lines.append(candle_line)
    (pass_reasons if candle_pattern in {"Hammer", "Engulfing", "Doji"} else fail_reasons).append(candle_line)

    return {
        "pass_reasons": pass_reasons,
        "fail_reasons": fail_reasons,
        "diagnostic_lines": diagnostic_lines,
        "primary_failure": fail_reasons[0] if fail_reasons else "",
        "signal_reason": " | ".join(pass_reasons[:5]) if pass_reasons else "No qualifying reasons recorded.",
        "elimination_reason": fail_reasons[0] if fail_reasons else "",
        "metrics_snapshot": {
            "price": round(latest_close, 2),
            "volume": round(latest_volume, 2),
            "rvol": round(effective_rvol, 2),
            "raw_rvol": round(raw_rvol, 2) if raw_rvol is not None else None,
            "rsi": round(latest_rsi, 2),
            "macd": round(latest_macd, 4),
            "macd_signal": round(latest_macd_signal, 4),
            "trend": trend,
            "signal_score": round(signal_score, 2),
        },
    }


def evaluate_serialized_symbol(task: tuple[str, dict[str, list[Any]], dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    symbol, payload, record_payload, settings_payload = task
    record = UniverseRecord(**record_payload) if record_payload else UniverseRecord(symbol=symbol, company_name=symbol, exchange="")
    settings = IndicatorSettings(**settings_payload)
    frame = _deserialise_frame(payload)
    try:
        return evaluate_history(symbol, frame, record, settings)
    finally:
        del frame
        gc.collect()


def evaluate_history(
    symbol: str,
    frame: pd.DataFrame,
    record: UniverseRecord,
    settings: IndicatorSettings,
) -> dict[str, Any]:
    df = _clean_history_frame(frame)
    if df.empty or len(df) < settings.min_history_rows:
        return {"ticker": symbol, "error": f"Insufficient history for {symbol}"}

    close = df["Close"]
    open_price = df["Open"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].replace(0, np.nan)

    bb_basis = close.rolling(settings.bb_length).mean()
    bb_std = close.rolling(settings.bb_length).std(ddof=0)
    bb_upper = bb_basis + settings.bb_std * bb_std
    bb_lower = bb_basis - settings.bb_std * bb_std
    bb_width = ((bb_upper - bb_lower) / bb_basis.replace(0, np.nan) * 100.0).replace([np.inf, -np.inf], np.nan)

    ema20 = close.ewm(span=settings.ema_stop_window, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    sma50 = close.rolling(50).mean()

    macd_fast = close.ewm(span=settings.macd_fast, adjust=False).mean()
    macd_slow = close.ewm(span=settings.macd_slow, adjust=False).mean()
    macd_line = macd_fast - macd_slow
    macd_signal = macd_line.ewm(span=settings.macd_signal, adjust=False).mean()
    macd_hist = macd_line - macd_signal

    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta.clip(upper=0.0)).replace(0, np.nan)
    avg_gain = gains.ewm(alpha=1 / max(settings.rsi_period, 2), adjust=False, min_periods=settings.rsi_period).mean()
    avg_loss = losses.ewm(alpha=1 / max(settings.rsi_period, 2), adjust=False, min_periods=settings.rsi_period).mean()
    relative_strength = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    rsi = rsi.fillna(50.0)

    latest_close = float(close.iloc[-1])
    latest_open = float(open_price.iloc[-1]) if pd.notna(open_price.iloc[-1]) else latest_close
    previous_close = float(close.iloc[-2])
    latest_volume = float(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else 0.0
    avg_volume_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
    raw_vol_ratio = latest_volume / avg_volume_20 if avg_volume_20 else np.nan
    effective_vol_ratio = float(raw_vol_ratio) if pd.notna(raw_vol_ratio) else 1.0

    coil_slice = close.tail(settings.coil_days)
    coil_range_pct = ((coil_slice.max() - coil_slice.min()) / max(coil_slice.min(), 1e-9) * 100.0) if len(coil_slice) else np.nan
    is_coiling = bool(pd.notna(coil_range_pct) and coil_range_pct <= settings.coil_range_percent)

    squeeze_window = bb_width.dropna().tail(120)
    squeeze_cutoff = np.nanpercentile(squeeze_window, settings.squeeze_percentile) if len(squeeze_window) >= 12 else np.nan
    latest_bb_width = float(bb_width.iloc[-1]) if pd.notna(bb_width.iloc[-1]) else np.nan
    is_squeeze = bool(pd.notna(latest_bb_width) and pd.notna(squeeze_cutoff) and latest_bb_width <= squeeze_cutoff)

    resistance_slice = high.iloc[-settings.resistance_lookback - 1 : -1]
    breakout_level = float(resistance_slice.max()) if len(resistance_slice) else float(high.iloc[:-1].max())
    is_breakout = bool(latest_close > breakout_level and previous_close <= breakout_level)

    is_volume_surge = bool(effective_vol_ratio >= settings.volume_multiplier)
    is_macd_cross = bool(
        len(macd_line) >= 2
        and len(macd_signal) >= 2
        and macd_line.iloc[-1] > macd_signal.iloc[-1]
        and macd_line.iloc[-2] <= macd_signal.iloc[-2]
    )
    latest_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0
    previous_rsi = float(rsi.iloc[-2]) if len(rsi) >= 2 and pd.notna(rsi.iloc[-2]) else latest_rsi
    is_rsi_constructive = bool(50.0 <= latest_rsi <= 72.0 and latest_rsi >= previous_rsi)

    signals = {
        "coiling": is_coiling,
        "bb_squeeze": is_squeeze,
        "price_breakout": is_breakout,
        "volume_surge": is_volume_surge,
        "macd_crossover": is_macd_cross,
        "rsi_constructive": is_rsi_constructive,
    }

    latest_macd = float(macd_line.iloc[-1]) if pd.notna(macd_line.iloc[-1]) else 0.0
    latest_macd_signal = float(macd_signal.iloc[-1]) if pd.notna(macd_signal.iloc[-1]) else 0.0
    latest_macd_hist = float(macd_hist.iloc[-1]) if pd.notna(macd_hist.iloc[-1]) else 0.0
    hist_reference = float(macd_hist.tail(30).abs().median()) if len(macd_hist) else 0.0
    if not math.isfinite(hist_reference) or hist_reference <= 0:
        hist_reference = max(abs(latest_macd_hist), 1e-6)
    macd_hist_strength = _clamp(latest_macd_hist / (hist_reference * 2.0))
    macd_strength = _clamp(
        (0.45 if latest_macd > latest_macd_signal else 0.0)
        + (0.35 if is_macd_cross else 0.0)
        + (0.20 * macd_hist_strength)
    )

    if latest_rsi < 40.0:
        rsi_strength = _clamp((latest_rsi / 40.0) * 0.45)
    elif latest_rsi <= 60.0:
        rsi_strength = _clamp(0.55 + ((latest_rsi - 40.0) / 20.0) * 0.35)
    elif latest_rsi <= 72.0:
        rsi_strength = _clamp(0.90 - ((latest_rsi - 60.0) / 12.0) * 0.15)
    else:
        rsi_strength = _clamp(0.75 - ((latest_rsi - 72.0) / 18.0) * 0.55)
    if latest_rsi >= previous_rsi:
        rsi_strength = _clamp(rsi_strength + 0.10)
    if 50.0 <= latest_rsi <= 68.0:
        rsi_strength = _clamp(rsi_strength + 0.08)

    volume_strength = _clamp((effective_vol_ratio / max(settings.volume_multiplier, 1.0)) / 1.35)
    if is_volume_surge:
        volume_strength = _clamp(volume_strength + 0.18)

    weighted_result = calculate_weighted_score(
        settings,
        macd_strength=macd_strength,
        rsi_strength=rsi_strength,
        volume_strength=volume_strength,
    )

    breakout_distance_pct = ((latest_close / breakout_level) - 1.0) * 100.0 if breakout_level else np.nan
    stop_loss = min(float(ema20.iloc[-1]), float(coil_slice.min())) if len(coil_slice) else float(ema20.iloc[-1])
    profit_target = latest_close + max(latest_close - stop_loss, 0.0) * 2.0
    change_pct = ((latest_close / previous_close) - 1.0) * 100.0 if previous_close else 0.0
    gap_up_pct = ((latest_open / previous_close) - 1.0) * 100.0 if previous_close else 0.0
    high_52w = float(high.tail(252).max()) if len(high) >= 2 else float(high.max())
    distance_from_52w_high_pct = ((high_52w - latest_close) / high_52w * 100.0) if high_52w else np.nan
    support_level = float(low.tail(20).min()) if len(low) else latest_close
    resistance_level = float(high.tail(20).max()) if len(high) else latest_close
    above_ema20 = bool(pd.notna(ema20.iloc[-1]) and latest_close >= float(ema20.iloc[-1]))
    above_sma50 = bool(pd.notna(sma50.iloc[-1]) and latest_close >= float(sma50.iloc[-1]))
    macd_signal_state = "Bullish crossover" if is_macd_cross else ("Bullish" if latest_macd >= latest_macd_signal else "Bearish")
    candle_pattern = _detect_candle_pattern(df)
    trend_label = _trend_label(close, ema20, sma50.fillna(ema50))
    signal_score = float(weighted_result["confluence_percent"])
    diagnostics = _build_symbol_diagnostics(
        settings,
        latest_close=latest_close,
        latest_volume=latest_volume,
        effective_rvol=effective_vol_ratio,
        raw_rvol=float(raw_vol_ratio) if pd.notna(raw_vol_ratio) else None,
        latest_rsi=latest_rsi,
        latest_macd=latest_macd,
        latest_macd_signal=latest_macd_signal,
        trend=trend_label,
        signal_score=signal_score,
        breakout_level=breakout_level,
        above_ema20=above_ema20,
        above_sma50=above_sma50,
        candle_pattern=candle_pattern,
    )
    latest_stamp = df.index[-1]
    last_updated = latest_stamp.strftime("%H:%M:%S") if hasattr(latest_stamp, "strftime") else str(latest_stamp)
    sparkline = _series_payload(close.tail(5), digits=2)

    result = {
        "ticker": symbol,
        "company_name": record.company_name or symbol,
        "exchange": record.exchange or infer_exchange_from_symbol(symbol),
        "currency": currency_symbol_for_exchange(record.exchange or infer_exchange_from_symbol(symbol)),
        "price": round(latest_close, 2),
        "price_label": format_currency(latest_close, record.exchange or infer_exchange_from_symbol(symbol)),
        "change_pct": round(change_pct, 2),
        "change_label": f"{change_pct:+.2f}%",
        "score": weighted_result["score"],
        "signal_score": round(signal_score, 1),
        "max_score": weighted_result["max_score"],
        "required_score": weighted_result["required_score"],
        "confluence_percent": weighted_result["confluence_percent"],
        "threshold_percent": settings.confluence_percent,
        "passes_threshold": weighted_result["passes_threshold"],
        "perfect": weighted_result["passes_threshold"] and float(weighted_result["confluence_percent"]) >= 98.0,
        "signals": signals,
        "signal_strengths": weighted_result["signal_strengths"],
        "component_contributions": weighted_result["component_contributions"],
        "score_formula": weighted_result["score_formula"],
        "score_breakdown": weighted_result["score_breakdown"],
        "weights": weighted_result["weights"],
        "rvol": round(effective_vol_ratio, 2),
        "raw_rvol": round(float(raw_vol_ratio), 2) if pd.notna(raw_vol_ratio) else None,
        "rvol_neutralized": not pd.notna(raw_vol_ratio),
        "vol_ratio": round(effective_vol_ratio, 2),
        "volume": round(latest_volume, 2),
        "volume_label": format_volume(latest_volume),
        "avg_volume_20": round(avg_volume_20, 2) if math.isfinite(avg_volume_20) else None,
        "bb_width": round(float(latest_bb_width), 2) if pd.notna(latest_bb_width) else None,
        "breakout_level": round(breakout_level, 2) if math.isfinite(breakout_level) else None,
        "breakout_distance_pct": round(float(breakout_distance_pct), 2) if pd.notna(breakout_distance_pct) else None,
        "gap_up_pct": round(float(gap_up_pct), 2) if pd.notna(gap_up_pct) else None,
        "high_52w": round(high_52w, 2) if math.isfinite(high_52w) else None,
        "distance_from_52w_high_pct": round(float(distance_from_52w_high_pct), 2) if pd.notna(distance_from_52w_high_pct) else None,
        "macd": round(latest_macd, 4) if pd.notna(macd_line.iloc[-1]) else None,
        "macd_signal": round(latest_macd_signal, 4) if pd.notna(macd_signal.iloc[-1]) else None,
        "macd_hist": round(latest_macd_hist, 4) if pd.notna(macd_hist.iloc[-1]) else None,
        "macd_signal_state": macd_signal_state,
        "rsi": round(latest_rsi, 2),
        "ema20": round(float(ema20.iloc[-1]), 2) if pd.notna(ema20.iloc[-1]) else None,
        "ema50": round(float(ema50.iloc[-1]), 2) if pd.notna(ema50.iloc[-1]) else None,
        "sma50": round(float(sma50.iloc[-1]), 2) if pd.notna(sma50.iloc[-1]) else None,
        "above_ema20": above_ema20,
        "above_sma50": above_sma50,
        "coil_range_pct": round(float(coil_range_pct), 2) if pd.notna(coil_range_pct) else None,
        "stop_loss": round(stop_loss, 2) if math.isfinite(stop_loss) else None,
        "profit_target": round(profit_target, 2) if math.isfinite(profit_target) else None,
        "support": round(support_level, 2),
        "resistance": round(resistance_level, 2),
        "candle_pattern": candle_pattern,
        "trend": trend_label,
        "sector": record.sector,
        "industry": record.industry,
        "market_cap": record.market_cap,
        "market_cap_label": format_market_cap(record.market_cap, record.exchange or infer_exchange_from_symbol(symbol)),
        "float_shares": None,
        "float_label": "--",
        "pe_ratio": None,
        "last_updated": last_updated,
        "as_of_label": last_updated,
        "sparkline": sparkline,
        "diagnostics": diagnostics["diagnostic_lines"],
        "pass_reasons": diagnostics["pass_reasons"],
        "fail_reasons": diagnostics["fail_reasons"],
        "primary_failure": diagnostics["primary_failure"] if not weighted_result["passes_threshold"] else "",
        "signal_reason": diagnostics["signal_reason"],
        "elimination_reason": diagnostics["elimination_reason"] if not weighted_result["passes_threshold"] else "",
        "metrics_snapshot": diagnostics["metrics_snapshot"],
        "api_debug": {
            "symbol": symbol,
            "exchange": record.exchange or infer_exchange_from_symbol(symbol),
            "history_preview": _compact_debug_frame(df),
        },
        "history_rows": int(len(df)),
        "as_of": df.index[-1].isoformat(),
    }
    return result


class ScannerEngine:
    def __init__(
        self,
        settings: Optional[IndicatorSettings] = None,
        *,
        loader: Optional[ExchangeUniverseLoader] = None,
        news_fetcher: Optional[NewsFetcher] = None,
        max_workers: int = DEFAULT_WORKERS,
        download_batch_size: int = DEFAULT_DOWNLOAD_BATCH,
    ) -> None:
        self.settings = settings or IndicatorSettings()
        self.http = HttpClient()
        self.loader = loader or ExchangeUniverseLoader(self.http)
        self.news_fetcher = news_fetcher or NewsFetcher(self.http)
        self.finnhub = FinnhubClient(self.http)
        self.download_batch_size = max(8, int(download_batch_size))
        self.available_cpu_workers = max(1, (os.cpu_count() or 4) - 1)
        self.max_workers = DEFAULT_WORKERS
        self.compute_workers = 1
        self.metadata_workers = 2
        self.configure_runtime(max_workers)

    def update_settings(self, settings: IndicatorSettings) -> None:
        self.settings = settings

    def configure_runtime(self, max_workers: int) -> None:
        self.max_workers = max(1, min(int(max_workers), MAX_WORKERS))
        self.compute_workers = max(1, min(self.max_workers, self.available_cpu_workers))
        self.metadata_workers = max(2, min(self.max_workers, 12))

    def build_scan_request(
        self,
        *,
        tickers: Optional[Sequence[str]] = None,
        exchange: str = GLOBAL_EXCHANGE,
        global_mode: bool = False,
        full_exchange: bool = False,
        turbo_mode: bool = True,
        retain_non_matches: bool = False,
        enrich_metadata: bool = True,
        workers: Optional[int] = None,
        debug_mode: bool = False,
    ) -> ScanRequest:
        return ScanRequest(
            tickers=list(tickers or []),
            exchange=_normalise_exchange(exchange),
            global_mode=bool(global_mode),
            full_exchange=bool(full_exchange),
            turbo_mode=bool(turbo_mode),
            retain_non_matches=bool(retain_non_matches),
            enrich_metadata=bool(enrich_metadata),
            workers=int(workers or self.max_workers),
            debug_mode=bool(debug_mode),
        )

    def warm_start_universes(self, force_refresh: bool = False) -> dict[str, int]:
        return self.loader.warm_start(force_refresh=force_refresh)

    def warm_start_payload(self, force_refresh: bool = False) -> dict[str, Any]:
        counts: dict[str, int] = {}
        combined_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        refresh_required = False

        for exchange in FULL_UNIVERSE_EXCHANGES:
            exchange_records, is_stale = self.loader.load_exchange_universe_state(exchange, force_refresh=force_refresh, allow_stale=True)
            counts[exchange] = len(exchange_records)
            refresh_required = refresh_required or is_stale
            for record in exchange_records:
                if record.symbol in seen:
                    continue
                combined_records.append(record.to_dict())
                seen.add(record.symbol)

        fno_symbols, fno_stale = self.loader.load_fno_symbols_state(force_refresh=force_refresh, allow_stale=True)
        counts["F&O"] = len(fno_symbols)
        refresh_required = refresh_required or fno_stale
        return {
            "counts": counts,
            "records": combined_records,
            "refresh_required": refresh_required and not force_refresh,
        }

    def normalize_symbol(self, symbol: str, exchange: str = GLOBAL_EXCHANGE) -> str:
        clean = symbol.strip().upper()
        if not clean:
            return ""
        clean_exchange = _normalise_exchange(exchange)
        if "." in clean or clean_exchange == GLOBAL_EXCHANGE:
            return clean
        suffix = EXCHANGE_CONFIG.get(clean_exchange, {}).get("suffix", "")
        return f"{clean}{suffix}" if suffix else clean

    def resolve_tickers(self, tickers: Iterable[str], exchange: str = GLOBAL_EXCHANGE, global_mode: bool = False) -> list[str]:
        resolved: list[str] = []
        seen: set[str] = set()
        for ticker in tickers:
            raw = ticker.strip().upper()
            if not raw:
                continue
            normalized = raw if global_mode else self.normalize_symbol(raw, exchange)
            if normalized not in seen:
                resolved.append(normalized)
                seen.add(normalized)
        return resolved

    def build_full_exchange_list(self, exchange: str, global_mode: bool = False) -> list[str]:
        raw_exchange = str(exchange or "").upper().strip()
        clean_exchange = _normalise_exchange(exchange)
        if raw_exchange == "F&O":
            return self.loader.load_fno_symbols()
        if global_mode or clean_exchange == GLOBAL_EXCHANGE:
            records = self.loader.load_global_universe()
        else:
            records = self.loader.load_exchange_universe(clean_exchange)
        return [record.symbol for record in records]

    def _quote_cache_name(self, symbol: str) -> str:
        return f"quote_{symbol.replace('.', '_')}.json"

    def get_cached_quote(self, symbol: str) -> dict[str, Any]:
        cached = _read_json_cache(self._quote_cache_name(symbol), QUOTE_CACHE_HOURS)
        return dict(cached or {})

    def _store_quote(self, payload: dict[str, Any]) -> None:
        symbol = str(payload.get("symbol", "")).strip().upper()
        if not symbol:
            return
        _write_json_cache(self._quote_cache_name(symbol), payload)

    def fetch_quote_snapshots(self, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        quotes: dict[str, dict[str, Any]] = {}
        normalized = self.resolve_tickers(symbols, exchange=GLOBAL_EXCHANGE, global_mode=True)
        pending: list[str] = []

        for symbol in normalized:
            cached = self.get_cached_quote(symbol)
            if cached:
                quotes[symbol] = cached
            else:
                pending.append(symbol)

        for batch in _chunked(pending, 24):
            try:
                downloaded = self._download_history(batch, period="5d", interval="1d")
            except Exception:
                continue
            for symbol in batch:
                frame = _extract_symbol_frame(downloaded, symbol, len(batch))
                if frame.empty:
                    continue
                df = _clean_history_frame(frame)
                if df.empty:
                    continue
                close = df["Close"]
                latest_close = float(close.iloc[-1])
                previous_close = float(close.iloc[-2]) if len(close) >= 2 else latest_close
                change_pct = ((latest_close / previous_close) - 1.0) * 100.0 if previous_close else 0.0
                exchange = infer_exchange_from_symbol(symbol)
                payload = {
                    "symbol": symbol,
                    "exchange": exchange,
                    "price": round(latest_close, 2),
                    "change_pct": round(change_pct, 2),
                    "price_label": format_currency(latest_close, exchange),
                    "change_label": f"{change_pct:+.2f}%",
                    "last_updated": df.index[-1].strftime("%H:%M:%S") if hasattr(df.index[-1], "strftime") else str(df.index[-1]),
                }
                quotes[symbol] = payload
                self._store_quote(payload)
            del downloaded
            gc.collect()
        return quotes

    def search_tickers(self, query: str, exchange: str = GLOBAL_EXCHANGE, limit: int = 8) -> list[dict[str, Any]]:
        target_exchange = GLOBAL_EXCHANGE if exchange == GLOBAL_EXCHANGE else _normalise_exchange(exchange)
        return self.loader.search(query, exchange=target_exchange, limit=limit)

    def build_news_request(self, ticker: str, context: Optional[dict[str, Any]] = None, *, limit: int = 12) -> NewsRequest:
        payload = context or {}
        return self.news_fetcher.build_request(
            ticker or str(payload.get("ticker", "")),
            company_name=str(payload.get("company_name", "")),
            exchange=str(payload.get("exchange", GLOBAL_EXCHANGE)),
            sector=str(payload.get("sector", "")),
            industry=str(payload.get("industry", "")),
            limit=limit,
        )

    def fetch_news_for_result(self, result: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
        request = self.build_news_request(str(result.get("ticker", "")), context=result, limit=limit)
        return self.news_fetcher.fetch_for_request(request)

    def get_ticker_context(self, ticker: str, exchange: str = GLOBAL_EXCHANGE) -> dict[str, Any]:
        symbol = self.normalize_symbol(ticker, exchange=exchange)
        record = self.loader.find_record(symbol, exchange_hint=_normalise_exchange(exchange)) or UniverseRecord(
            symbol=symbol,
            company_name=symbol,
            exchange=infer_exchange_from_symbol(symbol),
        )
        metadata = self._fetch_ticker_metadata(symbol)
        return {
            "ticker": symbol,
            "company_name": metadata.get("company_name", record.company_name or symbol),
            "exchange": record.exchange or infer_exchange_from_symbol(symbol),
            "sector": metadata.get("sector", record.sector),
            "industry": metadata.get("industry", record.industry),
            "market_cap": metadata.get("market_cap", record.market_cap),
            "market_cap_label": format_market_cap(_coerce_float(metadata.get("market_cap", record.market_cap)), record.exchange or infer_exchange_from_symbol(symbol)),
            "float_shares": metadata.get("float_shares"),
            "float_label": format_float_millions(_coerce_float(metadata.get("float_shares"))),
            "pe_ratio": metadata.get("pe_ratio"),
        }

    def apply_filters(self, results: list[dict[str, Any]], filters: FilterSettings) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        sector_filter = filters.sector.strip().lower()
        industry_filter = filters.industry.strip().lower()
        for item in results:
            market_cap = _coerce_float(item.get("market_cap"))
            rvol = _coerce_float(item.get("rvol")) or 1.0
            price = _coerce_float(item.get("price"))
            volume = _coerce_float(item.get("volume"))
            float_shares = _coerce_float(item.get("float_shares"))
            rsi = _coerce_float(item.get("rsi"))
            gap_up_pct = _coerce_float(item.get("gap_up_pct"))
            distance_from_52w_high_pct = _coerce_float(item.get("distance_from_52w_high_pct"))
            pe_ratio = _coerce_float(item.get("pe_ratio"))
            signal_score = _coerce_float(item.get("signal_score"))
            trend = str(item.get("trend", "Any"))
            macd_signal_state = str(item.get("macd_signal_state", "Any"))
            candle_pattern = str(item.get("candle_pattern", "Any"))

            if filters.market_cap_min is not None:
                if market_cap is None or market_cap < filters.market_cap_min:
                    continue
            if filters.market_cap_max is not None:
                if market_cap is None or market_cap > filters.market_cap_max:
                    continue
            if filters.min_rvol is not None:
                if rvol is None or rvol < filters.min_rvol:
                    continue
            if filters.min_price is not None:
                if price is None or price < filters.min_price:
                    continue
            if filters.max_price is not None:
                if price is None or price > filters.max_price:
                    continue
            if filters.within_52w_high_pct is not None:
                if distance_from_52w_high_pct is None or distance_from_52w_high_pct > filters.within_52w_high_pct:
                    continue
            if filters.gap_up_min_pct is not None:
                if gap_up_pct is None or gap_up_pct < filters.gap_up_min_pct:
                    continue
            if filters.min_volume is not None:
                if volume is None or volume < filters.min_volume:
                    continue
            if filters.min_float_millions is not None:
                if float_shares is None or float_shares < filters.min_float_millions * 1_000_000:
                    continue
            if filters.max_float_millions is not None:
                if float_shares is None or float_shares > filters.max_float_millions * 1_000_000:
                    continue
            if filters.rsi_min is not None:
                if rsi is None or rsi < filters.rsi_min:
                    continue
            if filters.rsi_max is not None:
                if rsi is None or rsi > filters.rsi_max:
                    continue
            if filters.above_ema20 == "Above" and not bool(item.get("above_ema20")):
                continue
            if filters.above_ema20 == "Below" and bool(item.get("above_ema20")):
                continue
            if filters.above_sma50 == "Above" and not bool(item.get("above_sma50")):
                continue
            if filters.above_sma50 == "Below" and bool(item.get("above_sma50")):
                continue
            if filters.macd_signal != "Any" and filters.macd_signal.lower() not in macd_signal_state.lower():
                continue
            if filters.candle_pattern != "Any" and filters.candle_pattern.lower() not in candle_pattern.lower():
                continue
            if filters.pe_min is not None:
                if pe_ratio is None or pe_ratio < filters.pe_min:
                    continue
            if filters.pe_max is not None:
                if pe_ratio is None or pe_ratio > filters.pe_max:
                    continue
            if filters.min_score is not None:
                if signal_score is None or signal_score < filters.min_score:
                    continue
            if filters.trend_direction != "Any" and filters.trend_direction.lower() != trend.lower():
                continue
            if sector_filter and sector_filter not in str(item.get("sector", "")).lower():
                continue
            if industry_filter and industry_filter not in str(item.get("industry", "")).lower():
                continue
            filtered.append(item)
        return filtered

    def scan_market(
        self,
        tickers: Sequence[str],
        *,
        exchange: str = GLOBAL_EXCHANGE,
        global_mode: bool = False,
        turbo_mode: bool = True,
        retain_non_matches: bool = False,
        enrich_metadata: bool = True,
        debug_mode: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        resolved = self.resolve_tickers(tickers, exchange=exchange, global_mode=global_mode)
        total = len(resolved)
        telemetry = ScanTelemetry(total_symbols=total)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        record_map = self._record_map_for_symbols(resolved, exchange, global_mode)
        start = time.perf_counter()

        if progress_callback:
            progress_callback({"type": "status", "message": f"Queued {total:,} symbols for the live scan."})
            progress_callback({"type": "telemetry", "telemetry": telemetry.snapshot()})

        process_pool: Optional[futures.ProcessPoolExecutor] = None
        if turbo_mode and self.compute_workers > 1 and total >= self.download_batch_size:
            process_pool = futures.ProcessPoolExecutor(max_workers=self.compute_workers)

        try:
            for batch_number, batch in enumerate(_chunked(resolved, self.download_batch_size), start=1):
                if stop_event and stop_event.is_set():
                    break
                if progress_callback:
                    progress_callback(
                        {
                            "type": "status",
                            "message": f"Batch {batch_number}: downloading {len(batch):,} symbols with rate-limit aware batching...",
                        }
                    )

                batch_frames, batch_errors = self._download_batch_histories(batch)
                if batch_errors:
                    errors.extend(batch_errors)
                    if progress_callback:
                        for item in batch_errors:
                            progress_callback(
                                {
                                    "type": "symbol_error",
                                    "ticker": item.get("ticker", ""),
                                    "error": item.get("error", "Unknown download failure."),
                                    "telemetry": telemetry.snapshot(),
                                }
                            )

                tasks: list[tuple[str, dict[str, list[Any]], dict[str, Any], dict[str, Any]]] = []
                for symbol in batch:
                    frame = batch_frames.get(symbol)
                    if frame is None or frame.empty:
                        if not any(item["ticker"] == symbol for item in batch_errors):
                            error_item = {"ticker": symbol, "error": "No usable OHLCV data returned."}
                            errors.append(error_item)
                            if progress_callback:
                                progress_callback(
                                    {
                                        "type": "symbol_error",
                                        "ticker": symbol,
                                        "error": error_item["error"],
                                        "telemetry": telemetry.snapshot(),
                                    }
                                )
                        telemetry.scanned_symbols += 1
                        if progress_callback:
                            progress_callback({"type": "telemetry", "telemetry": telemetry.snapshot(), "ticker": symbol})
                        continue
                    record = record_map.get(symbol) or UniverseRecord(symbol=symbol, company_name=symbol, exchange=infer_exchange_from_symbol(symbol))
                    tasks.append((symbol, _serialise_frame(frame), record.to_dict(), self.settings.as_payload()))

                if process_pool and tasks:
                    future_map = {process_pool.submit(evaluate_serialized_symbol, task): task[0] for task in tasks}
                    for future in futures.as_completed(future_map):
                        symbol = future_map[future]
                        if stop_event and stop_event.is_set():
                            break
                        try:
                            result = future.result()
                        except Exception as exc:
                            error_item = {"ticker": symbol, "error": str(exc)}
                            errors.append(error_item)
                            if progress_callback:
                                progress_callback(
                                    {
                                        "type": "symbol_error",
                                        "ticker": symbol,
                                        "error": str(exc),
                                        "telemetry": telemetry.snapshot(),
                                    }
                                )
                            result = None
                        self._handle_scored_result(
                            result,
                            telemetry,
                            results,
                            progress_callback,
                            retain_non_matches=retain_non_matches,
                            debug_mode=debug_mode,
                        )
                else:
                    for task in tasks:
                        if stop_event and stop_event.is_set():
                            break
                        result = evaluate_serialized_symbol(task)
                        self._handle_scored_result(
                            result,
                            telemetry,
                            results,
                            progress_callback,
                            retain_non_matches=retain_non_matches,
                            debug_mode=debug_mode,
                        )
                batch_frames.clear()
                tasks.clear()
                gc.collect()

            if enrich_metadata and results and not (stop_event and stop_event.is_set()):
                self.enrich_results_metadata(results, progress_callback=progress_callback, stop_event=stop_event)
        finally:
            if process_pool:
                process_pool.shutdown(wait=False, cancel_futures=True)

        elapsed = time.perf_counter() - start
        ranked = self.rank_results(results)
        summary = ScanSummary(
            total_symbols=total,
            scanned_symbols=telemetry.scanned_symbols,
            matched_symbols=len([item for item in ranked if item.get("passes_threshold")]),
            error_symbols=len(errors),
            elapsed_seconds=elapsed,
            turbo_mode=turbo_mode,
        ).to_dict()

        if progress_callback:
            completion_message = f"Scan complete in {summary['elapsed_label']}."
            if summary["matched_symbols"] == 0:
                completion_message += " 0 signals qualified. Try lowering Signal Sensitivity or expanding your watchlist."
            progress_callback({"type": "status", "message": completion_message})
            progress_callback({"type": "telemetry", "telemetry": telemetry.snapshot()})
        return ranked, errors, summary

    def execute_scan(
        self,
        request: ScanRequest,
        *,
        progress_callback: Optional[ProgressCallback] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        self.configure_runtime(request.workers)
        if request.full_exchange:
            tickers = self.build_full_exchange_list(request.exchange, global_mode=request.global_mode)
        else:
            tickers = self.resolve_tickers(request.tickers, exchange=request.exchange, global_mode=request.global_mode)
        return self.scan_market(
            tickers,
            exchange=request.exchange,
            global_mode=request.global_mode,
            turbo_mode=request.turbo_mode,
            retain_non_matches=request.retain_non_matches,
            enrich_metadata=request.enrich_metadata,
            debug_mode=request.debug_mode,
            progress_callback=progress_callback,
            stop_event=stop_event,
        )

    def enrich_results_metadata(
        self,
        results: list[dict[str, Any]],
        *,
        progress_callback: Optional[ProgressCallback] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        pending = [item for item in results if not item.get("sector") or not item.get("market_cap")]
        if not pending:
            return

        total = len(pending)
        completed = 0
        if progress_callback:
            progress_callback({"type": "status", "message": f"Enriching {total:,} matches with sector and market-cap metadata..."})

        with futures.ThreadPoolExecutor(max_workers=min(self.metadata_workers, total)) as pool:
            future_map = {pool.submit(self._fetch_ticker_metadata, item["ticker"]): item for item in pending}
            for future in futures.as_completed(future_map):
                if stop_event and stop_event.is_set():
                    break
                item = future_map[future]
                completed += 1
                try:
                    metadata = future.result()
                except Exception:
                    metadata = {}
                if metadata:
                    item.update(metadata)
                    exchange = str(item.get("exchange", infer_exchange_from_symbol(item["ticker"])))
                    item["market_cap_label"] = format_market_cap(_coerce_float(item.get("market_cap")), exchange)
                    item["float_label"] = format_float_millions(_coerce_float(item.get("float_shares")))
                if progress_callback:
                    progress_callback(
                        {
                            "type": "metadata",
                            "completed": completed,
                            "total": total,
                            "ticker": item["ticker"],
                            "result": dict(item),
                        }
                    )

    def open_chart(self, ticker: str, *, exchange: str = GLOBAL_EXCHANGE, settings: Optional[IndicatorSettings] = None) -> tuple[bool, str]:
        success, location = self.render_chart_html(ticker, exchange=exchange, settings=settings)
        if success:
            webbrowser.open(Path(location).as_uri())
        return success, location

    def build_chart_snapshot(
        self,
        ticker: str,
        *,
        exchange: str = GLOBAL_EXCHANGE,
        settings: Optional[IndicatorSettings] = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        active_settings = settings or self.settings
        symbol = self.normalize_symbol(ticker, exchange=exchange)
        frame = self._download_single_history(symbol, period=CHART_HISTORY_PERIOD, interval=CHART_HISTORY_INTERVAL)
        if frame.empty:
            return False, f"No chart data returned for {symbol}."

        df = _clean_history_frame(frame)
        if df.empty:
            return False, f"Unable to normalize chart data for {symbol}."

        close = df["Close"]
        volume = df["Volume"]
        bb_basis = close.rolling(active_settings.bb_length).mean()
        bb_std = close.rolling(active_settings.bb_length).std(ddof=0)
        bb_upper = bb_basis + active_settings.bb_std * bb_std
        bb_lower = bb_basis - active_settings.bb_std * bb_std
        macd_fast = close.ewm(span=active_settings.macd_fast, adjust=False).mean()
        macd_slow = close.ewm(span=active_settings.macd_slow, adjust=False).mean()
        macd_line = macd_fast - macd_slow
        macd_signal = macd_line.ewm(span=active_settings.macd_signal, adjust=False).mean()
        macd_hist = macd_line - macd_signal

        result = evaluate_history(
            symbol,
            df,
            UniverseRecord(symbol=symbol, company_name=symbol, exchange=infer_exchange_from_symbol(symbol)),
            active_settings,
        )
        if result.get("error"):
            return False, str(result["error"])

        return True, {
            "ticker": symbol,
            "dates": [stamp.strftime("%Y-%m-%d") for stamp in df.index],
            "close": _series_payload(close, digits=4),
            "volume": _series_payload(volume, digits=2),
            "bb_upper": _series_payload(bb_upper, digits=4),
            "bb_basis": _series_payload(bb_basis, digits=4),
            "bb_lower": _series_payload(bb_lower, digits=4),
            "macd": _series_payload(macd_line, digits=4),
            "macd_signal": _series_payload(macd_signal, digits=4),
            "macd_hist": _series_payload(macd_hist, digits=4),
            "result": result,
        }

    def render_chart_html(
        self,
        ticker: str,
        *,
        exchange: str = GLOBAL_EXCHANGE,
        settings: Optional[IndicatorSettings] = None,
    ) -> tuple[bool, str]:
        go, make_subplots = _get_plotly_modules()
        if go is None or make_subplots is None:
            return False, "Plotly is not installed."
        active_settings = settings or self.settings
        symbol = self.normalize_symbol(ticker, exchange=exchange)
        frame = self._download_single_history(symbol, period=CHART_HISTORY_PERIOD, interval=CHART_HISTORY_INTERVAL)
        if frame.empty:
            return False, f"No chart data returned for {symbol}."

        df = _clean_history_frame(frame)
        if df.empty:
            return False, f"Unable to normalize chart data for {symbol}."

        close = df["Close"]
        volume = df["Volume"]
        bb_basis = close.rolling(active_settings.bb_length).mean()
        bb_std = close.rolling(active_settings.bb_length).std(ddof=0)
        bb_upper = bb_basis + active_settings.bb_std * bb_std
        bb_lower = bb_basis - active_settings.bb_std * bb_std
        macd_fast = close.ewm(span=active_settings.macd_fast, adjust=False).mean()
        macd_slow = close.ewm(span=active_settings.macd_slow, adjust=False).mean()
        macd_line = macd_fast - macd_slow
        macd_signal = macd_line.ewm(span=active_settings.macd_signal, adjust=False).mean()
        macd_hist = macd_line - macd_signal

        result = evaluate_history(symbol, df, UniverseRecord(symbol=symbol, company_name=symbol, exchange=infer_exchange_from_symbol(symbol)), active_settings)

        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.58, 0.18, 0.24],
            vertical_spacing=0.04,
            subplot_titles=(f"{symbol} OHLC + Bollinger Bands", "Volume", "MACD"),
        )
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name="OHLC",
                increasing_line_color="#34d399",
                decreasing_line_color="#fb7185",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(go.Scatter(x=df.index, y=bb_upper, name="BB Upper", line=dict(color="#22d3ee", width=1.2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=bb_basis, name="BB Basis", line=dict(color="#94a3b8", width=1.0)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=bb_lower, name="BB Lower", line=dict(color="#22d3ee", width=1.2)), row=1, col=1)
        fig.add_trace(go.Bar(x=df.index, y=volume, name="Volume", marker_color="#0ea5e9", opacity=0.7), row=2, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=macd_line, name="MACD", line=dict(color="#34d399", width=1.6)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=macd_signal, name="Signal", line=dict(color="#f59e0b", width=1.4)), row=3, col=1)
        fig.add_trace(go.Bar(x=df.index, y=macd_hist, name="Histogram", marker_color="#38bdf8", opacity=0.65), row=3, col=1)

        breakout_level = result.get("breakout_level")
        if breakout_level:
            fig.add_hline(y=breakout_level, line_dash="dot", line_color="#22d3ee", row=1, col=1)
        if result.get("stop_loss"):
            fig.add_hline(y=result["stop_loss"], line_dash="dash", line_color="#fb7185", row=1, col=1)
        if result.get("profit_target"):
            fig.add_hline(y=result["profit_target"], line_dash="dash", line_color="#34d399", row=1, col=1)

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#030712",
            plot_bgcolor="#07111d",
            font=dict(family="Inter, Segoe UI, sans-serif", color="#e2e8f0"),
            title=(
                f"{symbol} | Score {result.get('score', 0):.2f}/{result.get('max_score', active_settings.total_weight()):.2f} | "
                f"Threshold {result.get('threshold_percent', active_settings.confluence_percent)}% | "
                f"RVOL {result.get('rvol', '-')}"
            ),
            xaxis=dict(
                rangeselector=dict(
                    buttons=[
                        dict(count=1, label="1M", step="month", stepmode="backward"),
                        dict(count=3, label="3M", step="month", stepmode="backward"),
                        dict(count=6, label="6M", step="month", stepmode="backward"),
                        dict(count=1, label="YTD", step="year", stepmode="todate"),
                        dict(count=1, label="1Y", step="year", stepmode="backward"),
                        dict(step="all", label="All"),
                    ]
                ),
                rangeslider=dict(visible=True),
                type="date",
            ),
            margin=dict(l=30, r=24, t=60, b=24),
            height=980,
            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0.0),
        )
        fig.update_yaxes(gridcolor="#1e293b")
        fig.update_xaxes(gridcolor="#0f172a")

        chart_path = Path(tempfile.gettempdir()) / f"cortex_chart_{symbol.replace('.', '_')}.html"
        fig.write_html(chart_path, include_plotlyjs="cdn", auto_open=False)
        return True, str(chart_path)

    def export_results_csv(self, results: list[dict[str, Any]], path: str) -> None:
        frame = pd.DataFrame(results)
        if not frame.empty and "signals" in frame.columns:
            signals = frame.pop("signals").apply(lambda item: item if isinstance(item, dict) else {})
            frame["signal_coiling"] = signals.apply(lambda item: item.get("coiling"))
            frame["signal_squeeze"] = signals.apply(lambda item: item.get("bb_squeeze"))
            frame["signal_breakout"] = signals.apply(lambda item: item.get("price_breakout"))
            frame["signal_volume"] = signals.apply(lambda item: item.get("volume_surge"))
            frame["signal_macd"] = signals.apply(lambda item: item.get("macd_crossover"))
            frame["signal_rsi"] = signals.apply(lambda item: item.get("rsi_constructive"))
        if not frame.empty and "signal_strengths" in frame.columns:
            strengths = frame.pop("signal_strengths").apply(lambda item: item if isinstance(item, dict) else {})
            frame["strength_macd"] = strengths.apply(lambda item: item.get("macd"))
            frame["strength_rsi"] = strengths.apply(lambda item: item.get("rsi"))
            frame["strength_volume"] = strengths.apply(lambda item: item.get("volume"))
        if not frame.empty and "weights" in frame.columns:
            weights = frame.pop("weights").apply(lambda item: item if isinstance(item, dict) else {})
            frame["weight_macd"] = weights.apply(lambda item: item.get("macd"))
            frame["weight_rsi"] = weights.apply(lambda item: item.get("rsi"))
            frame["weight_volume"] = weights.apply(lambda item: item.get("volume"))
        frame.to_csv(path, index=False)

    def rank_results(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            results,
            key=lambda item: (
                int(bool(item.get("passes_threshold"))),
                float(item.get("signal_score") or item.get("confluence_percent") or 0.0),
                float(item.get("rvol") or 0.0),
                float(item.get("change_pct") or 0.0),
            ),
            reverse=True,
        )

    def fetch_news(
        self,
        ticker: str,
        company_name: str = "",
        limit: int = 12,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        if context:
            return self.fetch_news_for_result({"ticker": ticker, "company_name": company_name} | dict(context), limit=limit)
        return self.news_fetcher.fetch_ticker_news(ticker, company_name=company_name, limit=limit)

    def _record_map_for_symbols(self, symbols: Sequence[str], exchange: str, global_mode: bool) -> dict[str, UniverseRecord]:
        record_map: dict[str, UniverseRecord] = {}
        universe: list[UniverseRecord] = []
        if global_mode or exchange == GLOBAL_EXCHANGE:
            for exchange_name in FULL_UNIVERSE_EXCHANGES:
                universe.extend(self.loader.load_exchange_universe(exchange_name))
        else:
            universe = self.loader.load_exchange_universe(exchange)
        if universe:
            indexed = {record.symbol: record for record in universe}
            for symbol in symbols:
                if symbol in indexed:
                    record_map[symbol] = indexed[symbol]
        for symbol in symbols:
            record_map.setdefault(symbol, UniverseRecord(symbol=symbol, company_name=symbol, exchange=infer_exchange_from_symbol(symbol)))
        return record_map

    def _handle_scored_result(
        self,
        result: Optional[dict[str, Any]],
        telemetry: ScanTelemetry,
        results: list[dict[str, Any]],
        progress_callback: Optional[ProgressCallback],
        *,
        retain_non_matches: bool,
        debug_mode: bool,
    ) -> None:
        telemetry.scanned_symbols += 1
        if result and not result.get("error"):
            passed = bool(result.get("passes_threshold"))
            self._store_quote(
                {
                    "symbol": str(result.get("ticker", "")),
                    "exchange": str(result.get("exchange", infer_exchange_from_symbol(str(result.get("ticker", ""))))),
                    "price": result.get("price"),
                    "change_pct": result.get("change_pct"),
                    "price_label": result.get("price_label"),
                    "change_label": result.get("change_label"),
                    "last_updated": result.get("last_updated"),
                }
            )
            if passed:
                telemetry.passed_symbols += 1
            if passed or retain_non_matches:
                results.append(result)
            if progress_callback:
                progress_callback({"type": "symbol_result", "result": dict(result), "telemetry": telemetry.snapshot()})
                if passed:
                    progress_callback({"type": "match", "result": dict(result), "telemetry": telemetry.snapshot()})
                if debug_mode:
                    progress_callback(
                        {
                            "type": "debug",
                            "ticker": result.get("ticker", ""),
                            "debug": dict(result.get("api_debug", {}) or {}),
                            "telemetry": telemetry.snapshot(),
                        }
                    )
        if progress_callback:
            progress_callback(
                {
                    "type": "telemetry",
                    "telemetry": telemetry.snapshot(),
                    "ticker": result.get("ticker") if isinstance(result, dict) else "",
                }
            )

    def _download_batch_histories(self, symbols: Sequence[str]) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
        results: dict[str, pd.DataFrame] = {}
        errors: list[dict[str, Any]] = []
        queue: list[list[str]] = [list(symbols)]

        while queue:
            current = queue.pop(0)
            if not current:
                continue
            try:
                downloaded = self._download_history(current, period=DEFAULT_HISTORY_PERIOD, interval=DEFAULT_HISTORY_INTERVAL)
            except Exception as exc:
                if len(current) == 1:
                    errors.append({"ticker": current[0], "error": str(exc)})
                else:
                    midpoint = max(1, len(current) // 2)
                    queue.insert(0, current[midpoint:])
                    queue.insert(0, current[:midpoint])
                continue

            for symbol in current:
                frame = _extract_symbol_frame(downloaded, symbol, len(current))
                if frame.empty:
                    if len(current) == 1:
                        errors.append({"ticker": symbol, "error": "Empty OHLCV response."})
                    else:
                        queue.insert(0, [symbol])
                    continue
                results[symbol] = frame
            del downloaded
            gc.collect()

        return results, errors

    def _history_window(self, period: str) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        period_map = {
            "1d": timedelta(days=1),
            "5d": timedelta(days=5),
            "1mo": timedelta(days=31),
            "3mo": timedelta(days=92),
            "6mo": timedelta(days=183),
            "1y": timedelta(days=366),
            "2y": timedelta(days=731),
            "5y": timedelta(days=3653),
        }
        delta = period_map.get(str(period or "").lower(), timedelta(days=366))
        start = now - delta
        return int(start.timestamp()), int(now.timestamp())

    def _finnhub_resolution(self, interval: str) -> str:
        mapping = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "60m": "60",
            "90m": "60",
            "1h": "60",
            "1d": "D",
            "1wk": "W",
            "1mo": "M",
        }
        return mapping.get(str(interval or "").lower(), "D")

    def _download_finnhub_history(self, symbol: str, *, period: str, interval: str) -> pd.DataFrame:
        if not self.finnhub.enabled:
            return pd.DataFrame()
        from_timestamp, to_timestamp = self._history_window(period)
        return self.finnhub.fetch_candles(
            symbol,
            self._finnhub_resolution(interval),
            from_timestamp,
            to_timestamp,
        )

    def _download_single_history(self, symbol: str, *, period: str, interval: str) -> pd.DataFrame:
        last_error: Optional[Exception] = None
        try:
            downloaded = self._download_history([symbol], period=period, interval=interval)
            frame = _extract_symbol_frame(downloaded, symbol, 1)
            del downloaded
            if not frame.empty:
                return frame
            last_error = RuntimeError(f"Yahoo Finance returned an empty frame for {symbol}.")
        except Exception as exc:
            last_error = exc

        if self.finnhub.enabled:
            print(f"yfinance failed for {symbol}, trying Finnhub fallback")
            try:
                frame = self._download_finnhub_history(symbol, period=period, interval=interval)
                if not frame.empty:
                    return frame
            except Exception:
                pass

        if last_error is not None:
            return pd.DataFrame()
        return pd.DataFrame()

    def _download_history(self, symbols: Sequence[str], *, period: str, interval: str) -> pd.DataFrame:
        tickers = list(symbols)
        if not tickers:
            return pd.DataFrame()

        last_error: Optional[Exception] = None
        kwargs: dict[str, Any] = {
            "tickers": tickers if len(tickers) > 1 else tickers[0],
            "period": period,
            "interval": interval,
            "group_by": "ticker",
            "auto_adjust": False,
            "actions": False,
            "progress": False,
            "threads": True,
            "repair": True,
            "timeout": 25,
        }

        for attempt in range(1, 5):
            try:
                frame = self._invoke_yfinance_download(**kwargs)
                if isinstance(frame, pd.DataFrame) and not frame.empty:
                    return frame
                raise RuntimeError("Yahoo Finance returned an empty batch.")
            except Exception as exc:
                last_error = exc
                time.sleep(min(0.45 * (2 ** (attempt - 1)), 3.5))
        raise RuntimeError(f"History download failed for {len(tickers)} symbols: {last_error}") from last_error

    def _invoke_yfinance_download(self, **kwargs: Any) -> pd.DataFrame:
        yf = _get_yfinance_module()
        variants: list[dict[str, Any]] = [
            kwargs,
            {key: value for key, value in kwargs.items() if key not in {"repair", "timeout"}},
        ]
        last_error: Optional[Exception] = None
        for variant in variants:
            try:
                return yf.download(**variant)
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(f"yfinance.download failed: {last_error}") from last_error

    def _fetch_ticker_metadata(self, ticker: str) -> dict[str, Any]:
        cache_key = f"meta_{ticker.replace('.', '_')}.json"
        cached = _read_json_cache(cache_key, MIN_METADATA_CACHE_HOURS)
        if cached:
            return dict(cached)

        ticker_obj = _get_yfinance_module().Ticker(ticker)

        metadata: dict[str, Any] = {}
        info: dict[str, Any] = {}
        fast_info: dict[str, Any] = {}

        try:
            fast_info = dict(getattr(ticker_obj, "fast_info", {}) or {})
        except Exception:
            fast_info = {}
        try:
            info = dict(getattr(ticker_obj, "info", {}) or {})
        except Exception:
            info = {}

        company_name = _choose_first(info, "longName", "shortName", "displayName")
        sector = _choose_first(info, "sector", "sectorDisp") or ""
        industry = _choose_first(info, "industry", "industryDisp") or ""
        market_cap = _coerce_float(_choose_first(info, "marketCap", "market_cap") or _choose_first(fast_info, "marketCap", "market_cap"))
        float_shares = _coerce_float(_choose_first(info, "floatShares", "sharesFloat", "float_shares"))
        pe_ratio = _coerce_float(_choose_first(info, "trailingPE", "forwardPE", "pe", "trailingPe"))

        if company_name:
            metadata["company_name"] = str(company_name)
        if sector:
            metadata["sector"] = str(sector)
        if industry:
            metadata["industry"] = str(industry)
        if market_cap is not None:
            metadata["market_cap"] = market_cap
        if float_shares is not None:
            metadata["float_shares"] = float_shares
        if pe_ratio is not None:
            metadata["pe_ratio"] = pe_ratio

        _write_json_cache(cache_key, metadata)
        return metadata


def infer_exchange_from_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.endswith(".NS"):
        return "NSE"
    if clean.endswith(".BO"):
        return "BSE"
    if clean.endswith(".L"):
        return "LSE"
    return "NASDAQ"


def cli_main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Cortex CLI")
    parser.add_argument("--tickers", nargs="*", default=[], help="Space-separated ticker list.")
    parser.add_argument("--exchange", default="NASDAQ", help="Exchange context for suffix normalization.")
    parser.add_argument("--global-mode", action="store_true", help="Preserve mixed-market symbols.")
    parser.add_argument("--full-exchange", action="store_true", help="Scan the full universe for the exchange.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent worker count.")
    parser.add_argument("--bb-length", type=int, default=20)
    parser.add_argument("--bb-std", type=float, default=2.0)
    parser.add_argument("--macd-fast", type=int, default=12)
    parser.add_argument("--macd-slow", type=int, default=26)
    parser.add_argument("--macd-signal", type=int, default=9)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--volume-multiplier", type=float, default=1.25)
    parser.add_argument("--macd-weight", type=float, default=1.0)
    parser.add_argument("--rsi-weight", type=float, default=1.0)
    parser.add_argument("--vol-weight", type=float, default=1.0)
    parser.add_argument("--confluence", type=int, default=DEFAULT_SIGNAL_SENSITIVITY, help="Match threshold percentage.")
    parser.add_argument("--retain-all", action="store_true", help="Keep non-matching symbols in output.")
    parser.add_argument("--export", default="", help="Optional CSV export path.")
    parser.add_argument("--news", default="", help="Fetch news for a ticker and exit.")
    parser.add_argument("--chart", default="", help="Render and open chart for a ticker and exit.")
    args = parser.parse_args(argv)

    settings = IndicatorSettings(
        bb_length=args.bb_length,
        bb_std=args.bb_std,
        macd_fast=args.macd_fast,
        macd_slow=args.macd_slow,
        macd_signal=args.macd_signal,
        rsi_period=args.rsi_period,
        volume_multiplier=args.volume_multiplier,
        macd_weight=args.macd_weight,
        rsi_weight=args.rsi_weight,
        vol_weight=args.vol_weight,
        confluence_percent=args.confluence,
    )
    engine = ScannerEngine(settings=settings, max_workers=args.workers)

    if args.news:
        for item in engine.fetch_news(args.news):
            print(f"[{item['sentiment_tag']:<7}] {item['published_label']} | {item['title']}")
            if item.get("link"):
                print(f"  {item['link']}")
        return 0

    if args.chart:
        success, message = engine.open_chart(args.chart, exchange=args.exchange)
        print(message)
        return 0 if success else 1

    if args.full_exchange:
        tickers = engine.build_full_exchange_list(args.exchange, global_mode=args.global_mode)
    else:
        tickers = args.tickers

    if not tickers:
        parser.error("Provide --tickers or use --full-exchange.")

    def on_progress(event: dict[str, Any]) -> None:
        if event.get("type") == "status":
            print(event["message"])
        elif event.get("type") == "telemetry":
            telemetry = event["telemetry"]
            print(
                f"Scanned {telemetry['scanned']:,}/{telemetry['total']:,} | "
                f"Passed {telemetry['passed']:,} | "
                f"ETA {telemetry['eta_label']} | "
                f"Rate {telemetry['rate_per_minute']:.1f}/min"
            )
        elif event.get("type") == "match":
            result = event["result"]
            print(
                f"PASS {result['ticker']:<12} Score {result['score']:.2f}/{result.get('max_score', settings.total_weight()):.2f} | "
                f"Confluence {result['confluence_percent']:.0f}% | RVOL {result.get('rvol', '-')}"
            )

    results, errors, summary = engine.scan_market(
        tickers,
        exchange=args.exchange,
        global_mode=args.global_mode,
        turbo_mode=True,
        retain_non_matches=args.retain_all,
        progress_callback=on_progress,
    )

    print("")
    print(f"Completed {summary['scanned_symbols']:,}/{summary['total_symbols']:,} in {summary['elapsed_label']}")
    print(f"Matches: {summary['matched_symbols']:,} | Errors: {summary['error_symbols']:,}")

    for result in results[:25]:
        print(
            f"{result['ticker']:<12} {result['score']:.2f}/{result.get('max_score', settings.total_weight()):.2f}  "
            f"RVOL {result.get('rvol', '-')}  "
            f"Sector {result.get('sector') or 'Unknown'}  "
            f"Market Cap {result.get('market_cap_label', 'Unknown')}"
        )

    if args.export:
        engine.export_results_csv(results, args.export)
        print(f"Exported CSV to {args.export}")

    if errors:
        print("")
        print("Sample errors:")
        for item in errors[:10]:
            print(f"  {item['ticker']}: {item['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
