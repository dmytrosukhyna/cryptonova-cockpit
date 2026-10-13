"""FX / metals hypotheses on h1 (and m15) bars. Causal; fills at next bar open unless stated.

W1  weekend gap fade        : Monday open vs Friday close; fade toward Friday close
W2  first-candle-of-week    : break of Monday's first H1/H4 candle in its direction (and the fade)
S1  London open range        : break of 07:00–08:00 UTC range (broker time offset handled by weekday structure)
L1  4h level limit           : our LEVEL_LIMIT family on FX costs
T1  daily trend              : 28d / 20d breakout, hold 5d
R1  RSI(2) mean reversion    : daily close RSI(2) < 10 above 200-SMA → long next open, exit when RSI(2) > 70 / 5 days
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, resample
from ..features.levels import LevelContext
from .hypotheses import h_limit_at_level
from .strategies import Trade


def weekday(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).weekday()


def week_starts(h1: list[Candle]) -> list[int]:
    """Indices of the first bar after a weekend (gap > 40h since previous bar)."""
    return [i for i in range(1, len(h1)) if h1[i].open_time - h1[i - 1].open_time > 40 * 3_600_000]


def w1_weekend_gap_fade(h1: list[Candle], min_gap_atr: float = 0.3, max_gap_atr: float = 3.0, hold_h: int = 24,
                        stop_mult: float = 1.0) -> list[Trade]:
    a = arrays(h1); A = atr(a["h"], a["l"], a["c"], 24 * 10)
    out = []
    for i in week_starts(h1):
        fri = h1[i - 1].close; mon = h1[i].open; gap = mon - fri; g = abs(gap) / A[i - 1]
        if g < min_gap_atr or g > max_gap_atr:
            continue
        dir_ = "SHORT" if gap > 0 else "LONG"           # fade toward Friday close
        stop = mon + (stop_mult * abs(gap)) * (1 if gap > 0 else -1)
        out.append(Trade(i, dir_, mon, stop, fri, hold_h * 60, False, "gap_fade"))
    return out


def w2_first_candle(h1: list[Candle], bars: int = 4, mode: str = "break", hold_h: int = 24, stop_atr: float = 0.0) -> list[Trade]:
    """After the first `bars` hours of the week, trade the break of that range (mode=break: with the break;
    mode=fade: against it, back into the range). Stop = other side of the range."""
    a = arrays(h1); A = atr(a["h"], a["l"], a["c"], 240)
    out = []
    for i in week_starts(h1):
        if i + bars + 24 >= len(h1):
            continue
        hi = max(x.high for x in h1[i:i + bars]); lo = min(x.low for x in h1[i:i + bars])
        for j in range(i + bars, min(i + bars + 12, len(h1) - 1)):   # breakout must happen within 12h
            c = h1[j].close
            if c > hi:
                e = h1[j + 1].open
                if mode == "break":
                    out.append(Trade(j + 1, "LONG", e, lo - stop_atr * A[j], e + (e - lo), hold_h * 60, False, "fc_break"))
                else:
                    out.append(Trade(j + 1, "SHORT", e, hi + (hi - lo) * 0.5, lo, hold_h * 60, False, "fc_fade"))
                break
            if c < lo:
                e = h1[j + 1].open
                if mode == "break":
                    out.append(Trade(j + 1, "SHORT", e, hi + stop_atr * A[j], e - (hi - e), hold_h * 60, False, "fc_break"))
                else:
                    out.append(Trade(j + 1, "LONG", e, lo - (hi - lo) * 0.5, hi, hold_h * 60, False, "fc_fade"))
                break
    return out


def s1_session_range(h1: list[Candle], start_h: int, width: int = 1, hold_h: int = 8, rr: float = 1.5) -> list[Trade]:
    """Opening-range breakout: range of bars [start_h, start_h+width) each weekday (bar hour in data time),
    trade the first close beyond it within 4h; stop = other side; target rr × range."""
    out = []
    n = len(h1)
    i = 0
    while i < n - width - 6:
        t = datetime.fromtimestamp(h1[i].open_time / 1000, tz=timezone.utc)
        if t.hour != start_h or t.weekday() >= 5:
            i += 1; continue
        hi = max(x.high for x in h1[i:i + width]); lo = min(x.low for x in h1[i:i + width]); rng = hi - lo
        if rng <= 0:
            i += 1; continue
        for j in range(i + width, min(i + width + 4, n - 1)):
            if h1[j].close > hi:
                e = h1[j + 1].open; out.append(Trade(j + 1, "LONG", e, lo, e + rr * rng, hold_h * 60, False, f"or{start_h}")); break
            if h1[j].close < lo:
                e = h1[j + 1].open; out.append(Trade(j + 1, "SHORT", e, hi, e - rr * rng, hold_h * 60, False, f"or{start_h}")); break
        i += 24
    return out


def l1_level_limit(h1: list[Candle], target_r: float = 1.5, hold_h: int = 48) -> list[Trade]:
    ctx = LevelContext(h1, 4, every=1, lookback=300, left=3, right=3)      # 4h levels on h1 bars
    out = []
    for e in h_limit_at_level(h1, ctx):
        sign = 1 if e.direction == "LONG" else -1; r = abs(e.entry - e.stop)
        out.append(Trade(e.index, e.direction, e.entry, e.stop, e.entry + sign * target_r * r, hold_h * 60, True, "lvl"))
    return out


def t1_daily_trend(h1: list[Candle], lookback: int = 28, breakout: int = 20, hold_days: int = 5, stop_atr: float = 2.0) -> list[Trade]:
    d = resample(h1, 24)
    # resample by calendar day instead (FX days are not 24 bars): rebuild daily from dates
    days: dict[int, list[Candle]] = {}
    for c in h1:
        days.setdefault(c.open_time // 86_400_000, []).append(c)
    dl = []
    for k in sorted(days):
        g = days[k]
        dl.append(Candle(g[0].open_time, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close, sum(x.volume for x in g), g[-1].close_time, 0, 0, 0, 0))
    a = arrays(dl); A = atr(a["h"], a["l"], a["c"], 14)
    t_h1 = np.array([c.open_time for c in h1])
    out = []; busy = -1
    for k in range(max(lookback, breakout) + 1, len(dl)):
        if k <= busy:
            continue
        c = a["c"][k - 1]; r = c / a["c"][k - 1 - lookback] - 1
        hh = a["h"][k - 1 - breakout:k - 1].max(); ll = a["l"][k - 1 - breakout:k - 1].min()
        j = int(np.searchsorted(t_h1, dl[k].open_time))
        if j >= len(h1):
            break
        if r > 0 and c >= hh:
            out.append(Trade(j, "LONG", h1[j].open, h1[j].open - stop_atr * A[k - 1], None, hold_days * 1440, False, "trend")); busy = k + hold_days - 1
        elif r < 0 and c <= ll:
            out.append(Trade(j, "SHORT", h1[j].open, h1[j].open + stop_atr * A[k - 1], None, hold_days * 1440, False, "trend")); busy = k + hold_days - 1
    return out


def r1_rsi2(h1: list[Candle], lo_th: float = 10.0, hi_th: float = 90.0, hold_days: int = 5, stop_atr: float = 2.0) -> list[Trade]:
    days: dict[int, list[Candle]] = {}
    for c in h1:
        days.setdefault(c.open_time // 86_400_000, []).append(c)
    dl = []
    for k in sorted(days):
        g = days[k]
        dl.append(Candle(g[0].open_time, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close, 0, g[-1].close_time, 0, 0, 0, 0))
    c = np.array([x.close for x in dl]); n = len(c)
    A = atr(np.array([x.high for x in dl]), np.array([x.low for x in dl]), c, 14)
    # RSI(2)
    rsi = np.full(n, 50.0); g = l = 0.0
    for i in range(1, n):
        d = c[i] - c[i - 1]; up, dn = max(d, 0), max(-d, 0)
        g = (g + up) / 2 if i > 2 else up; l = (l + dn) / 2 if i > 2 else dn
        rsi[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    sma = np.array([c[max(0, i - 200):i + 1].mean() for i in range(n)])
    t_h1 = np.array([x.open_time for x in h1])
    out = []; busy = -1
    for k in range(201, n - 1):
        if k <= busy:
            continue
        j = int(np.searchsorted(t_h1, dl[k + 1].open_time))
        if j >= len(h1):
            break
        if rsi[k] < lo_th and c[k] > sma[k]:
            out.append(Trade(j, "LONG", h1[j].open, h1[j].open - stop_atr * A[k], None, hold_days * 1440, False, "rsi2")); busy = k + hold_days
        elif rsi[k] > hi_th and c[k] < sma[k]:
            out.append(Trade(j, "SHORT", h1[j].open, h1[j].open + stop_atr * A[k], None, hold_days * 1440, False, "rsi2")); busy = k + hold_days
    return out
