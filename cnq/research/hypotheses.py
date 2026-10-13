"""Research Lab — entry hypotheses evaluated with the edge curve, in-sample vs
out-of-sample, against a random baseline. Every rule is causal: at candle i it
sees candles[:i+1]. Fill is explicit per rule (next open, or limit inside candle i).

Each hypothesis returns entries: (fill_index, direction, entry_price, stop_price).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from ..bingx.rest import Candle
from ..features.candles import arrays, atr, relative_volume, resample
from ..features.levels import Level, LevelContext, build_levels, find_swings, nearest_levels

K = (0.5, 1.0, 1.5, 2.0, 3.0)


@dataclass
class Entry:
    index: int          # candle on which the fill happens
    direction: str
    entry: float
    stop: float
    tag: str = ""


# ------------------------------------------------------------------ evaluation
def evaluate(candles: list[Candle], entries: list[Entry], horizon: int = 96,
             min_stop_atr: float = 0.0) -> dict:
    a = arrays(candles)
    h, l = a["h"], a["l"]
    atr_arr = atr(h, l, a["c"])
    n = len(candles)
    hits = {k: [] for k in K}
    resolved = {k: [] for k in K}     # target or stop reached within the horizon
    used = 0
    for e in entries:
        sign = 1 if e.direction == "LONG" else -1
        r = abs(e.entry - e.stop)
        if r <= 0 or e.index >= n or r < min_stop_atr * atr_arr[e.index]:
            continue
        used += 1
        hw = h[e.index: e.index + horizon]; lw = l[e.index: e.index + horizon]
        stop_mask = (lw <= e.stop) if sign == 1 else (hw >= e.stop)
        stop_j = int(np.argmax(stop_mask)) if stop_mask.any() else 10**9
        for k in K:
            tgt = e.entry + sign * k * r
            tm = (hw >= tgt) if sign == 1 else (lw <= tgt)
            tj = int(np.argmax(tm)) if tm.any() else 10**9
            hits[k].append(tj < stop_j)       # same bar as stop counts as stop (conservative)
            resolved[k].append(tj < 10**9 or stop_j < 10**9)
    out = {"n": used}
    for k in K:
        p = float(np.mean(hits[k])) if hits[k] else float("nan")
        res = np.array(resolved[k], dtype=bool)
        pc = float(np.mean(np.array(hits[k])[res])) if res.any() else float("nan")
        out[f"p{k}"] = round(p, 3)
        out[f"pc{k}"] = round(pc, 3)                 # conditional on resolution
        out[f"res{k}"] = round(float(res.mean()), 3) if len(res) else float("nan")
        out[f"ev{k}"] = round(p * k - (1 - p), 3)
    return out


def random_like(candles: list[Candle], entries: list[Entry], seed: int = 1, reps: int = 5) -> list[Entry]:
    rng = np.random.default_rng(seed)
    opens = np.array([c.open for c in candles])
    fake = []
    for _ in range(reps):
        for e in entries:
            dist = abs(e.entry - e.stop)
            j = int(rng.integers(30, len(candles) - 100))
            sign = 1 if e.direction == "LONG" else -1
            fake.append(Entry(j, e.direction, opens[j], opens[j] - sign * dist, "random"))
    return fake


def split(entries: list[Entry], n_candles: int, frac: float = 0.67) -> tuple[list[Entry], list[Entry]]:
    cut = int(n_candles * frac)
    return [e for e in entries if e.index < cut], [e for e in entries if e.index >= cut]


def report(name: str, candles: list[Candle], entries: list[Entry], horizon: int = 96) -> dict:
    ins, oos = split(entries, len(candles))
    rows = {}
    for label, es in (("IS", ins), ("OOS", oos)):
        sig = evaluate(candles, es, horizon)
        rnd = evaluate(candles, random_like(candles, es), horizon)
        rows[label] = {"n": sig["n"], "sig": {k: sig[f"ev{k}"] for k in K}, "rnd": {k: rnd[f"ev{k}"] for k in K},
                       "p1": sig["p1.0"], "p1_rnd": rnd["p1.0"]}
    print(f"== {name}")
    for label, r in rows.items():
        diff = {k: round(r["sig"][k] - r["rnd"][k], 3) for k in K}
        print(f"  {label:3} n={r['n']:5d}  P(1R)={r['p1']:.3f} vs rnd {r['p1_rnd']:.3f}   EV−rnd by target: {diff}")
    return rows


# ------------------------------------------------------------------ hypotheses
def h_limit_at_level(candles: list[Candle], ctx: LevelContext, pad_atr: float = 0.25,
                     min_touches: int = 2, cooldown: int = 8, require_trend: str | None = None,
                     trend_bars: int = 0) -> list[Entry]:
    """Limit order at the zone edge when price first trades into an ACTIVE level zone,
    stop beyond the far edge + pad. Fill assumed at the zone edge on the touching candle."""
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"])
    sma = np.convolve(a["c"], np.ones(80) / 80, mode="full")[:len(a["c"])]
    out, last = [], {}
    for i in range(100, len(candles)):
        c = candles[i]
        for L in ctx.levels_at(i):
            if L.touch_count < min_touches or L.status != "ACTIVE":
                continue
            if i - last.get(L.id, -999) < cooldown:
                continue
            prev_close = a["c"][i - 1]
            pad = pad_atr * atr_arr[i]
            if trend_bars and i - 1 - trend_bars < 0:
                continue
            tr = (prev_close - a["c"][i - 1 - trend_bars]) if trend_bars else 0.0
            if L.kind in ("support", "both") and prev_close > L.hi and c.low <= L.hi:
                if require_trend == "with" and prev_close < sma[i - 1]:
                    continue
                if trend_bars and tr <= 0:
                    continue
                out.append(Entry(i, "LONG", L.hi, L.lo - pad, L.id)); last[L.id] = i
            elif L.kind in ("resistance", "both") and prev_close < L.lo and c.high >= L.lo:
                if require_trend == "with" and prev_close > sma[i - 1]:
                    continue
                if trend_bars and tr >= 0:
                    continue
                out.append(Entry(i, "SHORT", L.lo, L.hi + pad, L.id)); last[L.id] = i
    return out


def h_confirmed_bounce_next_open(candles: list[Candle], ctx: LevelContext, pad_atr: float = 0.25,
                                 min_touches: int = 2) -> list[Entry]:
    """Current production rule shape: touch, then close back on the right side → next open."""
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"])
    out, pending = [], {}
    for i in range(100, len(candles) - 1):
        c = candles[i]
        for L in ctx.levels_at(i):
            if L.touch_count < min_touches or L.status != "ACTIVE":
                continue
            key = L.id
            st = pending.get(key)
            if L.kind in ("support", "both"):
                if c.low <= L.hi:
                    pending[key] = ("LONG", min(c.low, st[1]) if st else c.low, i)
                elif st and st[0] == "LONG" and c.close > L.hi and i - st[2] <= 12:
                    out.append(Entry(i + 1, "LONG", candles[i + 1].open, st[1] - pad_atr * atr_arr[i], key))
                    pending.pop(key, None)
            if L.kind in ("resistance", "both"):
                if c.high >= L.lo:
                    pending[key] = ("SHORT", max(c.high, st[1]) if st and st[0] == "SHORT" else c.high, i)
                elif st and st[0] == "SHORT" and c.close < L.lo and i - st[2] <= 12:
                    out.append(Entry(i + 1, "SHORT", candles[i + 1].open, st[1] + pad_atr * atr_arr[i], key))
                    pending.pop(key, None)
    return out


def h_breakout_momentum(candles: list[Candle], n: int = 48, stop_atr: float = 1.5, rv_min: float = 1.5) -> list[Entry]:
    """Close above the n-candle high with volume → long at next open; stop = entry − k·ATR."""
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"]); rv = relative_volume(a["v"])
    out = []
    for i in range(n + 1, len(candles) - 1):
        hh = a["h"][i - n: i].max(); ll = a["l"][i - n: i].min()
        if a["c"][i] > hh and rv[i] >= rv_min:
            e = candles[i + 1].open; out.append(Entry(i + 1, "LONG", e, e - stop_atr * atr_arr[i], "bo"))
        elif a["c"][i] < ll and rv[i] >= rv_min:
            e = candles[i + 1].open; out.append(Entry(i + 1, "SHORT", e, e + stop_atr * atr_arr[i], "bd"))
    return out


def h_exhaustion_fade(candles: list[Candle], move_atr: float = 3.0, window: int = 8, stop_atr: float = 1.0) -> list[Entry]:
    """Price moved > move_atr·ATR within `window` candles → fade at next open."""
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"])
    out = []
    for i in range(window + 20, len(candles) - 1):
        mv = (a["c"][i] - a["c"][i - window]) / atr_arr[i]
        e = candles[i + 1].open
        if mv > move_atr:
            out.append(Entry(i + 1, "SHORT", e, e + stop_atr * atr_arr[i], "fade"))
        elif mv < -move_atr:
            out.append(Entry(i + 1, "LONG", e, e - stop_atr * atr_arr[i], "fade"))
    return out


def h_prev_day_levels(candles: list[Candle], pad_atr: float = 0.3, interval_ms: int = 900_000) -> list[Entry]:
    """Previous-day high/low as levels (what many BTC level traders actually use).
    Limit at PDH/PDL on first touch of the day, stop beyond by pad."""
    a = arrays(candles)
    atr_arr = atr(a["h"], a["l"], a["c"])
    day = a["t"] // 86_400_000
    out = []
    days = sorted(set(day))
    idx_by_day = {d: np.flatnonzero(day == d) for d in days}
    for k in range(1, len(days)):
        prev = idx_by_day[days[k - 1]]; cur = idx_by_day[days[k]]
        pdh, pdl = a["h"][prev].max(), a["l"][prev].min()
        hit_h = hit_l = False
        for i in cur:
            if not hit_h and a["c"][i - 1] < pdh and a["h"][i] >= pdh:
                out.append(Entry(int(i), "SHORT", pdh, pdh + pad_atr * atr_arr[i], "pdh")); hit_h = True
            if not hit_l and a["c"][i - 1] > pdl and a["l"][i] <= pdl:
                out.append(Entry(int(i), "LONG", pdl, pdl - pad_atr * atr_arr[i], "pdl")); hit_l = True
    return out
