"""Candle-derived features. All functions are causal: value at index i uses only
candles[0..i]. This is what makes replay results trustworthy."""
from __future__ import annotations

import numpy as np

from ..bingx.rest import Candle


def arrays(candles: list[Candle]) -> dict[str, np.ndarray]:
    f = lambda attr: np.array([getattr(c, attr) for c in candles], dtype=float)  # noqa: E731
    return {"t": np.array([c.open_time for c in candles], dtype=np.int64),
            "o": f("open"), "h": f("high"), "l": f("low"), "c": f("close"),
            "v": f("volume"), "tb": f("taker_buy_volume")}


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    prev_c = np.concatenate([[c[0]], c[:-1]])
    return np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder ATR, causal. First n values are a simple mean of what is available."""
    tr = true_range(h, l, c)
    out = np.empty_like(tr)
    out[0] = tr[0]
    for i in range(1, len(tr)):
        if i < n:
            out[i] = tr[: i + 1].mean()
        else:
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def delta(v: np.ndarray, tb: np.ndarray) -> np.ndarray:
    """Aggressive buys minus aggressive sells per candle (base asset)."""
    return 2 * tb - v


def cvd(v: np.ndarray, tb: np.ndarray) -> np.ndarray:
    return np.cumsum(delta(v, tb))


def relative_volume(v: np.ndarray, n: int = 20) -> np.ndarray:
    """Volume / trailing mean of the previous n candles (excluding current)."""
    out = np.ones_like(v)
    for i in range(1, len(v)):
        w = v[max(0, i - n): i]
        m = w.mean()
        out[i] = v[i] / m if m > 0 else 1.0
    return out


def rolling_percentile_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Percentile rank of x[i] within the trailing window x[i-n..i]. 0..1, causal."""
    out = np.full_like(x, 0.5)
    for i in range(len(x)):
        w = x[max(0, i - n): i + 1]
        out[i] = (w < x[i]).sum() / len(w) if len(w) > 1 else 0.5
    return out


def resample(candles: list[Candle], factor: int) -> list[Candle]:
    """Aggregate consecutive candles (e.g. 1m → 5m with factor 5), aligned on open_time."""
    if factor <= 1:
        return candles
    out = []
    base_ms = candles[1].open_time - candles[0].open_time if len(candles) > 1 else 60_000
    bucket_ms = base_ms * factor
    group: list[Candle] = []
    cur = None
    for c in candles:
        b = c.open_time // bucket_ms
        if cur is not None and b != cur:
            out.append(_merge(group)); group = []
        cur = b
        group.append(c)
    if group:
        out.append(_merge(group))
    return out


def _merge(g: list[Candle]) -> Candle:
    return Candle(g[0].open_time, g[0].open, max(x.high for x in g), min(x.low for x in g),
                  g[-1].close, sum(x.volume for x in g), g[-1].close_time,
                  sum(x.quote_volume for x in g), sum(x.trades for x in g),
                  sum(x.taker_buy_volume for x in g), sum(x.taker_buy_quote for x in g))
