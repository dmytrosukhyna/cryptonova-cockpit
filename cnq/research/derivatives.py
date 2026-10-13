"""Derivatives-data hypotheses on the Binance 15m research dataset (funding, OI,
liquidations, long/short ratio, taker ratio). Bars with is_imputed_metrics=1 are
never used as signal bars. Causal: signal at bar i uses columns at bars ≤ i;
fill at open of bar i+1."""
from __future__ import annotations

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr
from .strategies import Trade


def _pct_rank_trailing(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=float)
    for i in range(n, len(x)):
        w = x[i - n: i]
        w = w[~np.isnan(w)]
        if len(w) > n // 2 and not np.isnan(x[i]):
            out[i] = (w < x[i]).mean()
    return out


def trailing_quantile(x: np.ndarray, n: int, q: float, step: int = 96) -> np.ndarray:
    """q-quantile of the trailing n values, recomputed every `step` bars and held (causal, fast)."""
    out = np.full_like(x, np.nan, dtype=float)
    last = np.nan
    for i in range(n, len(x)):
        if (i - n) % step == 0:
            w = x[i - n: i]; w = w[~np.isnan(w)]
            last = float(np.quantile(w, q)) if len(w) > n // 2 else np.nan
        out[i] = last
    return out


_QCACHE: dict = {}


def cached_quantile(x: np.ndarray, n: int, q: float) -> np.ndarray:
    key = (len(x), float(np.nansum(x[:5000])), float(np.nansum(x[-5000:])), n, q)
    if key not in _QCACHE:
        _QCACHE[key] = trailing_quantile(x, n, q)
    return _QCACHE[key]


def d1_funding_extreme_fade(cs: list[Candle], m: dict, pct: float = 0.95, lookback_bars: int = 96 * 90,
                            ret24_min: float = 0.04, stop_atr: float = 1.0, hold_bars: int = 192,
                            target_r: float = 2.0) -> list[Trade]:
    """Idea 3B: funding ≥ pct-percentile of trailing 90d AND 24h return ≥ ret24_min AND the bar
    closes below EMA20 → SHORT next open. Mirror for very negative funding. Stop = cycle high + ATR."""
    a = arrays(cs); atr15 = atr(a["h"], a["l"], a["c"], 96)
    f = m["funding_rate_pct"]; imp = m["is_imputed_metrics"]
    rank = _pct_rank_trailing(f, lookback_bars)
    ema = np.zeros_like(a["c"]); ema[0] = a["c"][0]
    for i in range(1, len(ema)):
        ema[i] = ema[i - 1] + (a["c"][i] - ema[i - 1]) * 2 / 21
    out, last = [], -10**9
    for i in range(lookback_bars, len(cs) - 1):
        if imp[i] or np.isnan(rank[i]) or i - last < hold_bars:
            continue
        r24 = a["c"][i] / a["c"][i - 96] - 1
        if rank[i] >= pct and r24 >= ret24_min and a["c"][i] < ema[i]:
            e = cs[i + 1].open; hi = a["h"][i - 96: i + 1].max()
            stop = hi + stop_atr * atr15[i]
            out.append(Trade(i + 1, "SHORT", e, stop, e - target_r * (stop - e), hold_bars * 15, False, "fund_hi")); last = i
        elif rank[i] <= 1 - pct and r24 <= -ret24_min and a["c"][i] > ema[i]:
            e = cs[i + 1].open; lo = a["l"][i - 96: i + 1].min()
            stop = lo - stop_atr * atr15[i]
            out.append(Trade(i + 1, "LONG", e, stop, e + target_r * (e - stop), hold_bars * 15, False, "fund_lo")); last = i
    return out


def d2_liquidation_cascade_fade(cs: list[Candle], m: dict, sigma_mult: float = 3.0, liq_pct: float = 0.95,
                                lookback_bars: int = 96 * 90, wick_frac: float = 0.5, hold_bars: int = 16,
                                stop_atr: float = 0.25, target_retrace: float = 0.5, side: str = "BOTH",
                                target_r: float | None = None, vol_mult: float = 0.0) -> list[Trade]:
    """Idea 6: 15m |return| ≥ sigma_mult·σ(30d) AND same-side liquidations ≥ liq_pct percentile (90d)
    AND the bar's wick against the move ≥ wick_frac of its range → fade at next open.
    Stop beyond the cascade extreme − stop_atr·ATR; target = retrace of the bar's range."""
    a = arrays(cs); atr15 = atr(a["h"], a["l"], a["c"], 96)
    ret = np.concatenate([[0.0], np.diff(a["c"]) / a["c"][:-1]])
    ll, sl, imp = np.abs(m["long_liq_usd"]), np.abs(m["short_liq_usd"]), m["is_imputed_metrics"]
    if "_absll" not in m:
        m["_absll"], m["_abssl"] = ll, sl
    thr_l = cached_quantile(m["_absll"], lookback_bars, liq_pct); thr_s = cached_quantile(m["_abssl"], lookback_bars, liq_pct)
    if "_sig30" not in m:
        m["_sig30"] = np.array([ret[max(0, i - 96 * 30): i].std() if i >= 96 * 30 else np.nan for i in range(0, len(ret), 96)])
    out, last = [], -10**9
    for i in range(lookback_bars, len(cs) - 1):
        if imp[i] or i - last < hold_bars:
            continue
        sig = m["_sig30"][i // 96]
        if not sig or np.isnan(sig):
            continue
        rng = a["h"][i] - a["l"][i]
        if rng <= 0:
            continue
        if vol_mult and a["v"][i] < vol_mult * a["v"][i - 96: i].mean():
            continue
        if side != "SHORT" and ret[i] <= -sigma_mult * sig and not np.isnan(thr_l[i]) and ll[i] >= thr_l[i]:   # long liquidations, price crashed
            lower_wick = min(a["o"][i], a["c"][i]) - a["l"][i]
            if lower_wick / rng < wick_frac:
                continue
            e = cs[i + 1].open; stop = a["l"][i] - stop_atr * atr15[i]
            tgt = max(e + 1.0 * (e - stop), a["l"][i] + target_retrace * rng) if target_r is None else e + target_r * (e - stop)
            out.append(Trade(i + 1, "LONG", e, stop, tgt, hold_bars * 15, False, "liq_long")); last = i
        elif side != "LONG" and ret[i] >= sigma_mult * sig and not np.isnan(thr_s[i]) and sl[i] >= thr_s[i]:
            upper_wick = a["h"][i] - max(a["o"][i], a["c"][i])
            if upper_wick / rng < wick_frac:
                continue
            e = cs[i + 1].open; stop = a["h"][i] + stop_atr * atr15[i]
            tgt = min(e - 1.0 * (stop - e), a["h"][i] - target_retrace * rng) if target_r is None else e - target_r * (stop - e)
            out.append(Trade(i + 1, "SHORT", e, stop, tgt, hold_bars * 15, False, "liq_short")); last = i
    return out


def d3_oi_spike_continuation(cs: list[Candle], m: dict, oi_pct: float = 0.98, lookback_bars: int = 96 * 30,
                             hold_bars: int = 32, stop_atr: float = 1.5) -> list[Trade]:
    """OI jump (oi_change_pct ≥ 98th pct of 30d) together with a directional bar → follow the
    direction (new money entering). Zero prior; included because the data is there."""
    a = arrays(cs); atr15 = atr(a["h"], a["l"], a["c"], 96)
    oi = m["oi_change_pct"]; imp = m["is_imputed_metrics"]
    rank = _pct_rank_trailing(oi, lookback_bars)
    out, last = [], -10**9
    for i in range(lookback_bars, len(cs) - 1):
        if imp[i] or np.isnan(rank[i]) or rank[i] < oi_pct or i - last < hold_bars:
            continue
        body = a["c"][i] - a["o"][i]
        if abs(body) < 0.5 * atr15[i]:
            continue
        e = cs[i + 1].open
        if body > 0:
            out.append(Trade(i + 1, "LONG", e, e - stop_atr * atr15[i], None, hold_bars * 15, False, "oi_up"))
        else:
            out.append(Trade(i + 1, "SHORT", e, e + stop_atr * atr15[i], None, hold_bars * 15, False, "oi_dn"))
        last = i
    return out


def d4_taker_ratio_extreme(cs: list[Candle], m: dict, pct: float = 0.97, lookback_bars: int = 96 * 30,
                           hold_bars: int = 8, stop_atr: float = 1.0) -> list[Trade]:
    """Aggressive taker buy/sell ratio at an extreme with price NOT making a new 24h extreme
    (absorption) → fade. Volume-absorption family, tested on real taker data."""
    a = arrays(cs); atr15 = atr(a["h"], a["l"], a["c"], 96)
    tb, ts = m["taker_buy_vol_btc"], m["taker_sell_vol_btc"]
    ratio = np.where((tb + ts) > 0, (tb - ts) / (tb + ts), np.nan)
    rank = _pct_rank_trailing(ratio, lookback_bars)
    out, last = [], -10**9
    for i in range(lookback_bars, len(cs) - 1):
        if np.isnan(rank[i]) or i - last < hold_bars:
            continue
        hi24 = a["h"][i - 96: i].max(); lo24 = a["l"][i - 96: i].min()
        e = cs[i + 1].open
        if rank[i] >= pct and a["h"][i] < hi24:         # buyers aggressive, price not breaking out → absorbed
            out.append(Trade(i + 1, "SHORT", e, a["h"][i] + stop_atr * atr15[i], None, hold_bars * 15, False, "absorb_S")); last = i
        elif rank[i] <= 1 - pct and a["l"][i] > lo24:
            out.append(Trade(i + 1, "LONG", e, a["l"][i] - stop_atr * atr15[i], None, hold_bars * 15, False, "absorb_L")); last = i
    return out
