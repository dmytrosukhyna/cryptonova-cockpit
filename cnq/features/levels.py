"""Market Structure Engine — swing-based horizontal levels as ZONES.

Method (deterministic, causal):
1. A swing high/low is a candle whose high/low is the extreme of `left` candles
   before and `right` candles after. It only becomes KNOWN `right` candles later
   (confirm_index) — replays must respect that.
2. Swings are clustered by price: two swings belong to the same level when their
   prices are within `tol_atr` * ATR of the cluster center.
3. Each level is a zone [lo, hi] with touches, timestamps, volume evidence,
   status (ACTIVE / BROKEN / RECLAIMED) and a score in [0, 100].

Not every touch is independent evidence: touches closer in time than
`min_touch_gap` candles are merged into one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..bingx.rest import Candle
from .candles import arrays, atr, relative_volume, resample


@dataclass
class Swing:
    index: int
    time: int
    price: float
    kind: str              # "high" | "low"
    confirm_index: int     # first index at which this swing is known
    rel_volume: float


@dataclass
class Level:
    id: str
    kind: str              # "resistance" | "support" | "both"
    lo: float
    hi: float
    center: float
    touches: list[Swing] = field(default_factory=list)
    status: str = "ACTIVE"
    broken_index: int | None = None
    score: float = 0.0

    @property
    def touch_count(self) -> int:
        return len(self.touches)

    @property
    def last_touch_index(self) -> int:
        return max(s.index for s in self.touches)

    def contains(self, price: float, pad: float = 0.0) -> bool:
        return self.lo - pad <= price <= self.hi + pad


def find_swings(candles: list[Candle], left: int = 5, right: int = 5,
                upto: int | None = None) -> list[Swing]:
    """Swings confirmed by index `upto` (exclusive). Causal."""
    a = arrays(candles)
    n = len(candles) if upto is None else min(upto, len(candles))
    rv = relative_volume(a["v"])
    out = []
    for i in range(left, n - right):
        hw, lw = a["h"][i - left: i + right + 1], a["l"][i - left: i + right + 1]
        if a["h"][i] == hw.max() and (hw == a["h"][i]).sum() == 1:
            out.append(Swing(i, int(a["t"][i]), float(a["h"][i]), "high", i + right, float(rv[i])))
        if a["l"][i] == lw.min() and (lw == a["l"][i]).sum() == 1:
            out.append(Swing(i, int(a["t"][i]), float(a["l"][i]), "low", i + right, float(rv[i])))
    return out


def build_levels(candles: list[Candle], upto: int | None = None, left: int = 5, right: int = 5,
                 tol_atr: float = 0.6, min_touch_gap: int = 3, break_atr: float = 0.5,
                 break_confirm: int = 2, max_age: int = 600, swings: list[Swing] | None = None,
                 a: dict | None = None, atr_now: float | None = None) -> list[Level]:
    """Levels known at candle index `upto` (exclusive). Default: all candles.
    `swings`/`a`/`atr_now` may be precomputed over the FULL series (causal: a swing is
    only used when its confirm_index <= upto)."""
    n = len(candles) if upto is None else min(upto, len(candles))
    if n < left + right + 5:
        return []
    if a is None or atr_now is None:
        a = arrays(candles[:n])
        atr_now = float(atr(a["h"], a["l"], a["c"])[-1])
    else:
        a = {k: v[:n] for k, v in a.items()}
    if swings is None:
        swings = find_swings(candles, left, right, n)
    swings = [Swing(s.index, s.time, s.price, s.kind, s.confirm_index, s.rel_volume)
              for s in swings if s.confirm_index <= n and s.index >= n - max_age]
    swings.sort(key=lambda s: s.price)
    # ---- cluster by price
    clusters: list[list[Swing]] = []
    for s in swings:
        if clusters and abs(s.price - np.mean([x.price for x in clusters[-1]])) <= tol_atr * atr_now:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    levels: list[Level] = []
    for cl in clusters:
        cl.sort(key=lambda s: s.index)
        merged: list[Swing] = []
        for s in cl:  # merge touches that are too close in time to count twice
            if merged and s.index - merged[-1].index < min_touch_gap:
                continue
            merged.append(s)
        prices = [s.price for s in merged]
        kinds = {s.kind for s in merged}
        kind = "both" if len(kinds) == 2 else ("resistance" if "high" in kinds else "support")
        lvl = Level(id=f"L{int(np.mean(prices))}", kind=kind, lo=min(prices), hi=max(prices),
                    center=float(np.median(prices)), touches=merged)
        # ---- status: broken when `break_confirm` consecutive closes beyond zone by break_atr*ATR
        closes = a["c"]
        start = lvl.touches[0].index
        up = (closes[start:] > lvl.hi + break_atr * atr_now) if kind != "support" else None
        dn = (closes[start:] < lvl.lo - break_atr * atr_now) if kind != "resistance" else None
        bi = None
        for arr in (up, dn):
            if arr is None or len(arr) < break_confirm:
                continue
            run = _consecutive(arr, break_confirm)
            if run is not None:
                bi = start + run if bi is None else max(bi, start + run)  # last break wins
        if bi is not None:
            lvl.status, lvl.broken_index = "BROKEN", bi
            inside = (closes >= lvl.lo) & (closes <= lvl.hi)
            seg = inside[bi + break_confirm:]
            if len(seg) > 1 and (seg[:-1] & seg[1:]).any():
                lvl.status = "RECLAIMED"
        lvl.score = _score(lvl, n, float(closes[-1]), atr_now)
        levels.append(lvl)
    levels.sort(key=lambda L: -L.score)
    return levels


def _consecutive(mask: np.ndarray, k: int) -> int | None:
    """Index of the LAST start of k consecutive True values, or None."""
    if k == 1:
        idx = np.flatnonzero(mask)
        return int(idx[-1]) if len(idx) else None
    m = mask.astype(np.int8)
    conv = np.convolve(m, np.ones(k, dtype=np.int8), mode="valid")
    idx = np.flatnonzero(conv == k)
    return int(idx[-1]) if len(idx) else None


def _score(lvl: Level, n: int, price: float, atr_now: float) -> float:
    touches = min(lvl.touch_count, 6)
    touch_score = 40 * (1 - 0.6 ** touches)           # diminishing returns
    age = n - lvl.last_touch_index
    recency = 25 * max(0.0, 1 - age / 400)
    vol = 20 * min(1.0, np.mean([s.rel_volume for s in lvl.touches]) / 2.0)
    width_pen = 10 * min(1.0, (lvl.hi - lvl.lo) / (2 * atr_now)) if atr_now else 0
    status = {"ACTIVE": 15, "RECLAIMED": 8, "BROKEN": 0}[lvl.status]
    return round(max(0.0, touch_score + recency + vol + status - width_pen), 1)


def nearest_levels(levels: list[Level], price: float, atr_now: float, within_atr: float = 3.0,
                   statuses: tuple[str, ...] = ("ACTIVE", "RECLAIMED")) -> list[Level]:
    out = [L for L in levels if L.status in statuses
           and abs(L.center - price) <= within_atr * atr_now]
    return sorted(out, key=lambda L: abs(L.center - price))


class LevelContext:
    """Levels rebuilt every `every` candles from `tf_factor`-resampled candles
    (e.g. 4 → 1h levels on 15m data). Causal."""

    def __init__(self, candles: list[Candle], tf_factor: int = 1, every: int = 4, lookback: int = 600,
                 left: int = 5, right: int = 5, min_score: float = 50.0):
        self.c = candles
        self.f = tf_factor
        self.every = every
        self.lookback = lookback
        self.min_score = min_score
        self.htf = resample(candles, tf_factor) if tf_factor > 1 else candles
        self.a = arrays(self.htf)
        self.atr_htf = atr(self.a["h"], self.a["l"], self.a["c"])
        self.swings = find_swings(self.htf, left, right)
        self.left, self.right = left, right
        self._cache: tuple[int, list[Level]] = (-1, [])

    def levels_at(self, i: int) -> list[Level]:
        """Levels known at LTF candle i (uses HTF candles fully closed before candle i)."""
        hi = self._htf_index(i)
        key = hi - hi % self.every
        if self._cache[0] != key:
            self._cache = (key, [L for L in build_levels(self.htf, upto=key, max_age=self.lookback,
                                                        swings=self.swings, a=self.a,
                                                        atr_now=float(self.atr_htf[max(0, key - 1)]),
                                                        left=self.left, right=self.right)
                                 if L.score >= self.min_score])
        return self._cache[1]

    def _htf_index(self, i: int) -> int:
        # number of HTF candles fully closed before LTF candle i opens
        t = self.c[i].open_time
        # htf candles are aligned buckets; count those with close_time < t
        return int(np.searchsorted(self.a["t"], t, side="left"))


