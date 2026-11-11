"""Technical indicator maths and chart-type transforms for Cortex.

Pure python so it can be unit-tested without Qt. Every function takes and
returns plain lists aligned to the input bars; ``None`` marks a warm-up slot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class Candle:
    o: float
    h: float
    l: float
    c: float
    v: float


Series = list[Optional[float]]


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------
def sma(vals: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(vals)
    if period <= 0:
        return out
    acc = 0.0
    for i, v in enumerate(vals):
        acc += v
        if i >= period:
            acc -= vals[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def ema(vals: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(vals)
    if period <= 0 or not vals:
        return out
    k = 2 / (period + 1)
    prev: Optional[float] = None
    for i, v in enumerate(vals):
        prev = v if prev is None else (v - prev) * k + prev
        if i >= period - 1:
            out[i] = prev
    return out


def wma(vals: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(vals)
    if period <= 0:
        return out
    denom = period * (period + 1) / 2
    for i in range(period - 1, len(vals)):
        s = 0.0
        for j in range(period):
            s += vals[i - period + 1 + j] * (j + 1)
        out[i] = s / denom
    return out


def hma(vals: Sequence[float], period: int) -> Series:
    """Hull moving average."""
    if period < 2 or len(vals) < period:
        return [None] * len(vals)
    half = wma(vals, max(1, period // 2))
    full = wma(vals, period)
    raw = [(2 * h - f) if (h is not None and f is not None) else None for h, f in zip(half, full)]
    clean = [x for x in raw if x is not None]
    smoothed = wma(clean, max(1, int(math.sqrt(period))))
    out: Series = [None] * len(vals)
    j = 0
    for i, x in enumerate(raw):
        if x is not None:
            out[i] = smoothed[j]
            j += 1
    return out


def _ma(vals: Sequence[float], period: int, kind: str) -> Series:
    return {"sma": sma, "ema": ema, "wma": wma, "hma": hma}.get(kind, sma)(vals, period)


# ---------------------------------------------------------------------------
# Bands / channels
# ---------------------------------------------------------------------------
def bollinger(vals: Sequence[float], period: int = 20, mult: float = 2.0):
    mid = sma(vals, period)
    up: Series = [None] * len(vals)
    lo: Series = [None] * len(vals)
    for i in range(period - 1, len(vals)):
        m = mid[i]
        if m is None:
            continue
        win = vals[i - period + 1:i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in win) / period)
        up[i], lo[i] = m + mult * sd, m - mult * sd
    return up, mid, lo


def keltner(cs: Sequence[Candle], period: int = 20, mult: float = 2.0):
    closes = [c.c for c in cs]
    mid = ema(closes, period)
    a = atr(cs, period)
    up: Series = [None] * len(cs)
    lo: Series = [None] * len(cs)
    for i in range(len(cs)):
        if mid[i] is not None and a[i] is not None:
            up[i], lo[i] = mid[i] + mult * a[i], mid[i] - mult * a[i]
    return up, mid, lo


def donchian(cs: Sequence[Candle], period: int = 20):
    up: Series = [None] * len(cs)
    lo: Series = [None] * len(cs)
    mid: Series = [None] * len(cs)
    for i in range(period - 1, len(cs)):
        win = cs[i - period + 1:i + 1]
        hh, ll = max(c.h for c in win), min(c.l for c in win)
        up[i], lo[i], mid[i] = hh, ll, (hh + ll) / 2
    return up, mid, lo


def vwap(cs: Sequence[Candle]) -> Series:
    out: Series = []
    cum_v = cum_pv = 0.0
    for c in cs:
        cum_v += c.v
        cum_pv += (c.h + c.l + c.c) / 3 * c.v
        out.append(cum_pv / cum_v if cum_v else None)
    return out


def supertrend(cs: Sequence[Candle], period: int = 10, mult: float = 3.0):
    a = atr(cs, period)
    line: Series = [None] * len(cs)
    direction = [1] * len(cs)
    fub = flb = None
    for i, c in enumerate(cs):
        if a[i] is None:
            continue
        mid = (c.h + c.l) / 2
        ub, lb = mid + mult * a[i], mid - mult * a[i]
        pc = cs[i - 1].c if i else c.c
        fub = ub if (fub is None or ub < fub or pc > fub) else fub
        flb = lb if (flb is None or lb > flb or pc < flb) else flb
        prev = direction[i - 1] if i else 1
        direction[i] = (-1 if c.c < flb else 1) if prev == 1 else (1 if c.c > fub else -1)
        line[i] = flb if direction[i] == 1 else fub
    return line, direction


def psar(cs: Sequence[Candle], step: float = 0.02, cap: float = 0.2):
    """Parabolic SAR. Returns (values, is_rising)."""
    n = len(cs)
    out: Series = [None] * n
    rising = [True] * n
    if n < 3:
        return out, rising
    up = cs[1].c >= cs[0].c
    sar = cs[0].l if up else cs[0].h
    ep = cs[0].h if up else cs[0].l
    af = step
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, cs[i - 1].l, cs[i - 2].l if i >= 2 else cs[i - 1].l)
            if cs[i].l < sar:
                up, sar, ep, af = False, ep, cs[i].l, step
            elif cs[i].h > ep:
                ep, af = cs[i].h, min(cap, af + step)
        else:
            sar = max(sar, cs[i - 1].h, cs[i - 2].h if i >= 2 else cs[i - 1].h)
            if cs[i].h > sar:
                up, sar, ep, af = True, ep, cs[i].h, step
            elif cs[i].l < ep:
                ep, af = cs[i].l, min(cap, af + step)
        out[i], rising[i] = sar, up
    return out, rising


def ichimoku(cs: Sequence[Candle], tenkan_p=9, kijun_p=26, senkou_p=52):
    """Returns (tenkan, kijun, span_b_raw, chikou, shift).

    The senkou spans are NOT pre-shifted: the chart projects them forward by
    ``shift`` bars so the cloud can extend past the last candle.
    """
    n = len(cs)

    def mid(period, i):
        if i < period - 1 or i >= n:
            return None
        win = cs[i - period + 1:i + 1]
        return (max(c.h for c in win) + min(c.l for c in win)) / 2

    tenkan: Series = [mid(tenkan_p, i) for i in range(n)]
    kijun: Series = [mid(kijun_p, i) for i in range(n)]
    span_b_raw: Series = [mid(senkou_p, i) for i in range(n)]
    chikou: Series = [None] * n
    for i in range(n):
        j = i + kijun_p
        if j < n:
            chikou[i] = cs[j].c
    return tenkan, kijun, span_b_raw, chikou, kijun_p


def pivots(cs: Sequence[Candle]):
    """Classic floor-trader pivots derived from the previous bar."""
    keys = ("P", "R1", "R2", "S1", "S2")
    out: dict[str, Series] = {k: [None] * len(cs) for k in keys}
    for i in range(1, len(cs)):
        prev = cs[i - 1]
        p = (prev.h + prev.l + prev.c) / 3
        rng = prev.h - prev.l
        out["P"][i] = p
        out["R1"][i] = 2 * p - prev.l
        out["S1"][i] = 2 * p - prev.h
        out["R2"][i] = p + rng
        out["S2"][i] = p - rng
    return out


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------
def rsi(vals: Sequence[float], period: int = 14) -> Series:
    out: Series = [None] * len(vals)
    if len(vals) <= period:
        return out
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = vals[i] - vals[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    ag, al = gain / period, loss / period
    out[period] = 100 - 100 / (1 + (ag / al if al else 999))
    for i in range(period + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100 - 100 / (1 + (ag / al if al else 999))
    return out


def stoch_rsi(vals: Sequence[float], period: int = 14, smooth: int = 3):
    r = rsi(vals, period)
    k: Series = [None] * len(vals)
    for i in range(len(vals)):
        win = [x for x in r[max(0, i - period + 1):i + 1] if x is not None]
        if len(win) >= period:
            hi, lo = max(win), min(win)
            k[i] = (r[i] - lo) / (hi - lo) * 100 if hi != lo and r[i] is not None else 50.0
    d = _smooth(k, smooth)
    return k, d


def _smooth(series: Series, period: int) -> Series:
    out: Series = [None] * len(series)
    for i in range(len(series)):
        win = [x for x in series[max(0, i - period + 1):i + 1] if x is not None]
        if len(win) == period:
            out[i] = sum(win) / period
    return out


def macd(vals: Sequence[float], fast=12, slow=26, signal=9):
    ef, es = ema(vals, fast), ema(vals, slow)
    line: Series = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    clean = [x for x in line if x is not None]
    sig_clean = ema(clean, signal)
    sig: Series = [None] * len(vals)
    j = 0
    for i, x in enumerate(line):
        if x is not None:
            sig[i] = sig_clean[j]
            j += 1
    hist: Series = [(a - b) if (a is not None and b is not None) else None for a, b in zip(line, sig)]
    return line, sig, hist


def stochastic(cs: Sequence[Candle], period=14, smooth=3):
    k: Series = [None] * len(cs)
    for i in range(period - 1, len(cs)):
        win = cs[i - period + 1:i + 1]
        hh, ll = max(c.h for c in win), min(c.l for c in win)
        k[i] = (cs[i].c - ll) / (hh - ll) * 100 if hh != ll else 50.0
    return k, _smooth(k, smooth)


def atr(cs: Sequence[Candle], period: int = 14) -> Series:
    out: Series = [None] * len(cs)
    trs: list[float] = []
    for i, c in enumerate(cs):
        pc = cs[i - 1].c if i else c.c
        trs.append(max(c.h - c.l, abs(c.h - pc), abs(c.l - pc)))
    prev: Optional[float] = None
    for i, tr in enumerate(trs):
        if i == period - 1:
            prev = sum(trs[:period]) / period
        elif i >= period:
            prev = (prev * (period - 1) + tr) / period
        if prev is not None:
            out[i] = prev
    return out


def adx(cs: Sequence[Candle], period: int = 14):
    n = len(cs)
    plus: Series = [None] * n
    minus: Series = [None] * n
    adx_out: Series = [None] * n
    if n < period * 2:
        return plus, minus, adx_out
    trs, pdm, ndm = [], [], []
    for i, c in enumerate(cs):
        if i == 0:
            trs.append(c.h - c.l)
            pdm.append(0.0)
            ndm.append(0.0)
            continue
        p = cs[i - 1]
        trs.append(max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)))
        up_move, down_move = c.h - p.h, p.l - c.l
        pdm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        ndm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
    tr_s = sum(trs[1:period + 1])
    p_s = sum(pdm[1:period + 1])
    n_s = sum(ndm[1:period + 1])
    dxs: list[float] = []
    for i in range(period, n):
        if i > period:
            tr_s = tr_s - tr_s / period + trs[i]
            p_s = p_s - p_s / period + pdm[i]
            n_s = n_s - n_s / period + ndm[i]
        if tr_s <= 0:
            continue
        pdi, ndi = p_s / tr_s * 100, n_s / tr_s * 100
        plus[i], minus[i] = pdi, ndi
        denom = pdi + ndi
        dxs.append(abs(pdi - ndi) / denom * 100 if denom else 0.0)
        if len(dxs) == period:
            adx_out[i] = sum(dxs) / period
        elif len(dxs) > period and adx_out[i - 1] is not None:
            adx_out[i] = (adx_out[i - 1] * (period - 1) + dxs[-1]) / period
    return plus, minus, adx_out


def cci(cs: Sequence[Candle], period: int = 20) -> Series:
    out: Series = [None] * len(cs)
    tps = [(c.h + c.l + c.c) / 3 for c in cs]
    for i in range(period - 1, len(cs)):
        win = tps[i - period + 1:i + 1]
        m = sum(win) / period
        dev = sum(abs(x - m) for x in win) / period
        out[i] = (tps[i] - m) / (0.015 * dev) if dev else 0.0
    return out


def obv(cs: Sequence[Candle]) -> Series:
    out: Series = []
    total = 0.0
    for i, c in enumerate(cs):
        if i:
            if c.c > cs[i - 1].c:
                total += c.v
            elif c.c < cs[i - 1].c:
                total -= c.v
        out.append(total)
    return out


def mfi(cs: Sequence[Candle], period: int = 14) -> Series:
    out: Series = [None] * len(cs)
    tps = [(c.h + c.l + c.c) / 3 for c in cs]
    for i in range(period, len(cs)):
        pos = neg = 0.0
        for j in range(i - period + 1, i + 1):
            flow = tps[j] * cs[j].v
            if tps[j] > tps[j - 1]:
                pos += flow
            elif tps[j] < tps[j - 1]:
                neg += flow
        out[i] = 100 - 100 / (1 + (pos / neg if neg else 999))
    return out


def williams_r(cs: Sequence[Candle], period: int = 14) -> Series:
    out: Series = [None] * len(cs)
    for i in range(period - 1, len(cs)):
        win = cs[i - period + 1:i + 1]
        hh, ll = max(c.h for c in win), min(c.l for c in win)
        out[i] = (hh - cs[i].c) / (hh - ll) * -100 if hh != ll else -50.0
    return out


def roc(vals: Sequence[float], period: int = 12) -> Series:
    out: Series = [None] * len(vals)
    for i in range(period, len(vals)):
        base = vals[i - period]
        out[i] = (vals[i] - base) / base * 100 if base else 0.0
    return out


def cmf(cs: Sequence[Candle], period: int = 20) -> Series:
    out: Series = [None] * len(cs)
    mfv = []
    for c in cs:
        rng = c.h - c.l
        mult = ((c.c - c.l) - (c.h - c.c)) / rng if rng else 0.0
        mfv.append(mult * c.v)
    for i in range(period - 1, len(cs)):
        vol = sum(c.v for c in cs[i - period + 1:i + 1])
        out[i] = sum(mfv[i - period + 1:i + 1]) / vol if vol else 0.0
    return out


# ---------------------------------------------------------------------------
# Chart-type transforms
# ---------------------------------------------------------------------------
def to_heikin(cs: Sequence[Candle]) -> list[Candle]:
    out: list[Candle] = []
    po = pc = None
    for c in cs:
        hc = (c.o + c.h + c.l + c.c) / 4
        ho = (c.o + c.c) / 2 if po is None else (po + pc) / 2
        out.append(Candle(ho, max(c.h, ho, hc), min(c.l, ho, hc), hc, c.v))
        po, pc = ho, hc
    return out


def to_renko(cs: Sequence[Candle], labels: Sequence[str], box: Optional[float] = None):
    """Build renko bricks. Returns (bricks, labels) aligned to each other."""
    if len(cs) < 3:
        return list(cs), list(labels)
    if box is None:
        a = [x for x in atr(cs, 14) if x is not None]
        box = (sum(a) / len(a)) if a else (cs[-1].c * 0.01)
    box = max(box, cs[-1].c * 0.001) or 1.0
    bricks: list[Candle] = []
    out_labels: list[str] = []
    base = cs[0].c
    for i, c in enumerate(cs):
        if len(bricks) > 4000:
            break
        while c.c - base >= box:
            bricks.append(Candle(base, base + box, base, base + box, c.v))
            out_labels.append(labels[i] if i < len(labels) else "")
            base += box
        while base - c.c >= box:
            bricks.append(Candle(base, base, base - box, base - box, c.v))
            out_labels.append(labels[i] if i < len(labels) else "")
            base -= box
    if not bricks:
        return list(cs), list(labels)
    return bricks, out_labels
