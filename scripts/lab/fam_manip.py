#!/usr/bin/env python3
"""Strategy lab — family "manip": liquidity grabs, stop hunts and traps.

Owner's thesis (18.10): classic chart patterns no longer work as they used to, because market makers know where the
crowd's stops and liquidation levels are. They push price just beyond the obvious level to take that liquidity, then
reverse. This family tests that behaviour on 15m bars (long and short separately, harness.py for entries/exits/stats).

Ideas
  1. sweep   — sweep & reclaim of an obvious level: previous UTC-day high/low (pd), previous week high/low (pw),
               3-day range = highest high / lowest low of the previous 288 bars (r3), Asian-session range 00–07 UTC traded
               during London 07–13 UTC (asia). Bar j trades beyond the (still untouched) level by >= kmin x ATR (and not
               more than kmax x ATR — beyond that it is a breakout, not a grab), then a close back inside within R bars
               (R counts the sweep bar itself) -> trade the reversal. Base: kmin 0.1, kmax 2, R 4 on each level; filters
               on the union of the 4 levels: wick (the same bar closes back inside, wick >= 50 % of the bar), vol (sweep
               bar volume >= 2x the previous 24 h mean), deep (kmin 0.5, kmax 3).
  2. eqhl    — equal highs / equal lows: two swing highs (fractal 4+4 bars, >= 3 h apart) within x % of each other inside
               the last N bars, nothing in between above them; the first bar that trades above the pool (0.05–2 ATR) and
               a close back below within 4 bars -> short (mirror for longs). N = 96 / 288, x = 0.1 / 0.3 %.
  3. liq     — liquidation-backed sweep (18-coin set only, 2023+ real data): idea 1 on the union of levels, and on the
               sweep bar the liquidations of the swept side (short liqs above highs, long liqs below lows) are >= the
               coin's own 30-day q-percentile and >= 2x its 30-day median (alt liquidation series have a constant floor,
               so only spikes relative to each coin's own history count). q = 0.95 / 0.99.
  4. crowd   — crowded positioning flush: open interest IN COINS (OI_usd / close) +x % over 24 h while price is flat
               (|24 h return| <= 0.5 sigma of the coin's 24 h returns over 30 days) during the previous 2 h; then a close
               beyond the 24 h range -> follow the break. Crowd filter: the break goes against the paying side
               (funding >= 0.01 % and in the coin's top 20 % of 30 days -> longs crowded -> only shorts; funding < 0.005 %
               and bottom 20 % -> only longs). The wide set has no funding -> the 1 h basis premium is used instead
               (> 0 and z >= 1 over 30 days = longs crowded; < 0 and z <= -1 = shorts crowded). x = 5 / 10 %.
  5. trap    — failed classic breakout: the first close above the N-bar high (below the low), N = 96 / 288, that closes
               back inside within W = 4 / 16 bars -> trade the opposite way; and a Bollinger-squeeze breakout (width in the
               lowest 20 % of 5 days during the previous 4 h, first close outside the band AND outside the 12 h box) that
               closes back inside the box within 8 bars.
  6. round   — round-number sweep: the round level just above the last 4 h high (below the 4 h low); step = 10^(digits-2)
               of the price (BTC ~60 000 -> every 1000, ETH ~3000 -> 100, SOL ~150 -> 10, DOGE ~0.15 -> 0.01) or half
               of it; sweep 0.1–2 ATR beyond and close back within 4 bars -> reverse.
  7. night   — low-liquidity move faded at a session open: on weekdays the 00:00–04:00 UTC move >= k sigma (sigma = the
               coin's 4 h move std over 30 days), still >= half intact at the open -> enter against it at the Europe open
               (07:00 UTC) or the New York open (09:30 New York time, DST-aware). Weekend: Saturday 00:00 -> Sunday 24:00
               move >= 1.5 sigma (sigma = 24 h std x sqrt 2) faded at Monday's Europe / New York open.
  8. oisweep — (added: liquidations proxy that also exists in the wide set) idea 1 on the union of levels, and on the
               sweep bar open interest in coins drops sharply (z <= -1.5 vs its 30-day std: stops / liquidations were
               filled) or jumps (z >= +1.5: breakout traders opened positions and are trapped).
Exits: harness grid_exits(96) for everything; sweep-type ideas (1, 2, 3, 5, 6, 8) also get two structural exits — stop
beyond the sweep extreme + 0.25 ATR (kept between 0.5 and 4 ATR), 1 target 2R or 3 targets 1/2/3R with break-even;
ideas 4 and 7 (no sweep wick) get "wide_time" (8 ATR disaster stop, time exit at 24 h) instead.

No look-ahead: every feature at bar i uses bars <= i (levels from bars before the sweep bar, pivots used only after their
confirmation bar, rolling windows end at i); the harness enters at the next bar's open. Checked by rescaling every bar after
a cut and comparing all masks up to the cut (scratchpad lookahead_manip.py).
Derivative data (liquidations, OI, funding, basis) on the 18-coin set is real only from 2023 -> ideas 3, 4, 8 there start
2023-01 (train 2023–24); price-only ideas train on 2021–24. Wide set: train 2023–24 for everything.
Choice of signal variant AND exit per idea/side on TRAIN only: train n >= NMIN, then prefer variants with most train years
positive, 2024 positive and the 5-slot account positive on train; highest train avg per trade among them.

Run: python3 fam_manip.py                         -> results/manip.json
     CNQ_LAB_SET=wide CNQ_LAB_TOPN=50 python3 fam_manip.py -> results/manip_wide50.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import resource
import sys
import time
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as HN  # noqa: E402
from harness import D, entries_from_mask, run, stats, grid_exits, report, passes  # noqa: E402

T0 = time.time()
WIDE = bool(HN.WIDE)
NS, NT = D.NS, D.T
SYMS = D.syms
TIMES = D.times
BOD = ((TIMES // 900_000) % 96).astype(np.int64)              # 15m bar of the UTC day (0 = 00:00–00:15)
DAYN = (TIMES // 86_400_000).astype(np.int64)
OFF_D = int(BOD[0])                                            # day segments: bar index + OFF_D is a multiple of 96 at 00:00
OFF_W = int((DAYN[0] + 3) % 7) * 96 + OFF_D                    # week segments start Monday 00:00 UTC
SIDX_D = (np.arange(NT) + OFF_D) // 96
SIDX_W = (np.arange(NT) + OFF_W) // 672
START23 = int(np.searchsorted(TIMES, 1672531200000))           # 2023-01-01 00:00 UTC
TRAIN_YEARS = tuple(range(int(D.year[np.searchsorted(TIMES, HN.TRAIN_START)]), 2025))   # 2021–24; wide set: 2023–24
MB = 96                                                        # time exit / max hold: 24 h
LEVELS_ALL = ("pd", "pw", "r3", "asia")
DERIV_IDEAS = ("liq", "crowd", "oisweep")                      # need real derivative data (18-coin set: 2023+)
IDEAS = ("sweep", "eqhl", "liq", "crowd", "trap", "round", "night", "oisweep")
SWEEP_IDEAS = ("sweep", "eqhl", "liq", "trap", "round", "oisweep")   # ideas with a sweep extreme (structural exits)
assert np.all(np.diff(TIMES) == 900_000), "the harness data must be a contiguous 15m grid"


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


# ---------------------------------------------------------------- causal helpers
def shift(x, k=1):
    """Value of bar i-k at bar i."""
    out = np.empty_like(x)
    out[:, :k] = False if x.dtype == bool else (-1 if np.issubdtype(x.dtype, np.integer) else np.nan)
    out[:, k:] = x[:, :-k]
    return out


def roll(x, n, fn="max", minp=None, q=None):
    """Rolling statistic over the last n bars INCLUDING bar i (NaN until min_periods real values)."""
    r = pd.DataFrame(np.asarray(x, dtype=np.float64).T).rolling(n, min_periods=minp or n)
    if fn == "std":
        v = r.std(ddof=0)
    elif fn == "quantile":
        v = r.quantile(q)
    else:
        v = getattr(r, fn)()
    return np.ascontiguousarray(v.to_numpy(dtype=np.float64).T, dtype=DT)


def seg_reshape(x, seglen, off, fill):
    """[coin, bar] -> [coin, segment, bar-in-segment] (calendar days / weeks), padded with `fill`."""
    nseg = (off + NT + seglen - 1) // seglen
    buf = np.full((x.shape[0], nseg * seglen), fill, dtype=x.dtype)
    buf[:, off:off + NT] = x
    return buf.reshape(x.shape[0], nseg, seglen)


def seg_unshape(v, off):
    return np.ascontiguousarray(v.reshape(v.shape[0], -1)[:, off:off + NT])


def atr14(h, l, c):
    pc = np.concatenate([c[:, :1], c[:, :-1]], axis=1)
    tr = np.nan_to_num(np.nanmax(np.stack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0))
    return roll(tr, 14, "mean")


# ---------------------------------------------------------------- arrays (module globals so the look-ahead test can swap them)
C = H = L = O = V = ATR = A1 = OIc = REAL = None
FUND = BAS = LLQ = SLQ = None
HAS_LIQ = HAS_FUND = HAS_OI = False
DT = np.float64
LEVELS: dict = {}
CACHE: dict = {}


def set_arrays(A: dict | None = None):
    """Load (or replace, for the look-ahead test) every input series and the derived ones."""
    global C, H, L, O, V, ATR, A1, OIc, REAL, FUND, BAS, LLQ, SLQ, HAS_LIQ, HAS_FUND, HAS_OI, DT
    test = A is not None
    if A is None:
        A = dict(C=D.C, H=D.H, L=D.L, O=D.O, V=D.V, OI=D.OI, LLIQ=D.LLIQ, SLIQ=D.SLIQ, FUND=D.FUND, BASIS=D.BASIS, IMP=D.IMP)
    C, H, L, O, V = (np.asarray(A[k]) for k in ("C", "H", "L", "O", "V"))
    DT = C.dtype
    ATR = atr14(H, L, C) if test else np.asarray(D.ATR, dtype=DT)
    A1 = shift(ATR)                                            # ATR known before the sweep bar
    imp = np.asarray(A["IMP"])
    REAL = (imp == 0) & np.isfinite(C)
    if not WIDE:
        REAL[:, :START23] = False                              # 18-coin derivatives are imputed before 2023
    smp = (slice(None), slice(None, None, 97))

    def has(x):
        return bool(np.isfinite(np.asarray(x)[smp]).any())
    HAS_OI, HAS_LIQ, HAS_FUND = has(A["OI"]), has(A["SLIQ"]), has(A["FUND"])
    if HAS_OI:
        oi = np.asarray(A["OI"], dtype=DT)
        with np.errstate(invalid="ignore", divide="ignore"):
            OIc = np.where(REAL & (oi > 0), oi / C, np.nan).astype(DT)        # open interest in COINS
    else:
        OIc = None
    FUND = np.where(REAL, np.asarray(A["FUND"], dtype=DT), np.nan) if HAS_FUND else None
    BAS = np.where(REAL, np.asarray(A["BASIS"], dtype=DT), np.nan) if has(A["BASIS"]) else None
    if HAS_LIQ:
        LLQ = np.where(REAL, np.abs(np.asarray(A["LLIQ"], dtype=DT)), np.nan)
        SLQ = np.where(REAL, np.abs(np.asarray(A["SLIQ"], dtype=DT)), np.nan)
    else:
        LLQ = SLQ = None
    LEVELS.clear()
    CACHE.clear()


def croll(x_name, n, fn):
    """Cached rolling of a base price series over the PREVIOUS n bars (shifted: bars i-n .. i-1)."""
    key = (x_name, n, fn)
    if key not in CACHE:
        CACHE[key] = shift(roll({"H": H, "L": L, "V": V}[x_name], n, fn))
    return CACHE[key]


# ---------------------------------------------------------------- obvious levels (known before the sweep bar)
def period_levels(seglen, off, sidx):
    """Previous calendar period's high / low per bar, and the running high / low of the CURRENT period before bar i."""
    out = []
    for X, fill, acc in ((H, -np.inf, np.maximum), (L, np.inf, np.minimum)):
        Xn = np.where(np.isfinite(X), X, fill).astype(DT)
        v = seg_reshape(Xn, seglen, off, fill)
        del Xn
        acc.accumulate(v, axis=2, out=v)
        seg_ext = v[:, :, -1].copy()
        pre = np.concatenate([np.full(v.shape[:2] + (1,), fill, DT), v[:, :, :-1]], axis=2)
        del v
        pre = seg_unshape(pre, off)                            # extreme of this period over bars before i
        prev = seg_ext[:, np.maximum(sidx - 1, 0)]
        prev[:, sidx == 0] = np.nan
        prev[~np.isfinite(prev)] = np.nan
        out += [prev, pre]
    return out                                                 # prev_hi, pre_hi, prev_lo, pre_lo


def build_levels():
    """LEVELS[name] = (level above, level below): NaN where the level is not valid or was already traded through
    earlier in the current period ("untouched" levels only)."""
    if LEVELS:
        return
    pdh, dpre_hi, pdl, dpre_lo = period_levels(96, OFF_D, SIDX_D)
    LEVELS["pd"] = (np.where(dpre_hi <= pdh, pdh, np.nan), np.where(dpre_lo >= pdl, pdl, np.nan))
    del pdh, pdl
    pwh, wpre_hi, pwl, wpre_lo = period_levels(672, OFF_W, SIDX_W)
    LEVELS["pw"] = (np.where(wpre_hi <= pwh, pwh, np.nan), np.where(wpre_lo >= pwl, pwl, np.nan))
    del pwh, wpre_hi, pwl, wpre_lo
    LEVELS["r3"] = (croll("H", 288, "max"), croll("L", 288, "min"))   # rolling: untouched by construction
    asia = (BOD < 28)[None, :]
    win = ((BOD >= 28) & (BOD < 52))[None, :]                  # traded during London 07:00–13:00 UTC
    lv = []
    for X, fill, red, pre in ((H, -np.inf, np.max, dpre_hi), (L, np.inf, np.min, dpre_lo)):
        Xa = np.where(np.isfinite(X) & asia, X, fill).astype(DT)
        v = seg_reshape(Xa, 96, OFF_D, fill)
        ext = red(v[:, :, :28], axis=2)
        del Xa, v
        lev = ext[:, SIDX_D]
        lev[~np.isfinite(lev)] = np.nan
        ok = win & ((pre <= lev) if fill < 0 else (pre >= lev))
        lv.append(np.where(ok, lev, np.nan))
    LEVELS["asia"] = tuple(lv)
    del dpre_hi, dpre_lo


# ---------------------------------------------------------------- sweep -> reclaim machinery
def sweep_masks(lev_hi, lev_lo, kmin, kmax, f_hi=None, f_lo=None):
    """Sweep bar j: the high trades above the level by kmin..kmax x ATR (the low below the low level)."""
    with np.errstate(invalid="ignore"):
        up = (H > lev_hi + kmin * A1) & (H <= lev_hi + kmax * A1)
        dn = (L < lev_lo - kmin * A1) & (L >= lev_lo - kmax * A1)
    if f_hi is not None:
        up &= f_hi
        dn &= f_lo
    return up, dn


def reclaim(sw, lev, R, side, kmax=None):
    """Trigger at the FIRST bar i in [j, j+R-1] that closes back inside the level of the latest sweep j <= i.
    side = -1: the sweep went ABOVE `lev` -> short when the close is back below it; +1: mirror (long).
    Returns (trigger mask, extreme of the sweep from j to i: max high for shorts / min low for longs)."""
    ar = np.arange(NT, dtype=np.int32)[None, :]
    last = np.where(sw, ar, np.int32(-1))
    np.maximum.accumulate(last, axis=1, out=last)
    has = last >= 0
    lc = np.where(has, last, 0)
    age = np.where(has, ar - last, np.int32(1 << 30))
    Lj = np.take_along_axis(lev, lc, axis=1)
    with np.errstate(invalid="ignore"):
        inside = (C < Lj) if side < 0 else (C > Lj)
    cond = has & (age < R) & inside
    prev_same = shift(cond) & (shift(last) == last)
    trig = cond & ~prev_same
    del cond, prev_same, inside
    X = H if side < 0 else L
    acc = np.fmax if side < 0 else np.fmin
    ext = np.where(age == 0, X, np.nan).astype(DT)
    run_ = X.astype(DT, copy=True)
    for a in range(1, R):
        run_ = acc(run_, shift(X, a))
        m = age == a
        ext[m] = run_[m]
    del run_
    if kmax is not None:                                       # the whole grab stays within kmax x ATR of the level
        Aj = np.take_along_axis(A1, lc, axis=1)
        with np.errstate(invalid="ignore"):
            trig &= ((ext - Lj) if side < 0 else (Lj - ext)) <= kmax * Aj
    trig &= np.isfinite(ext)
    ext = np.where(trig, ext, np.nan).astype(DT)
    return trig, ext


def union(parts, side):
    """OR of several (trigger, extreme) pairs; if two fire on the same bar keep the farther extreme."""
    trig, ext = None, None
    for t, e in parts:
        if trig is None:
            trig, ext = t.copy(), e.copy()
            continue
        both = trig & t
        ext = np.where(both, (np.fmax if side < 0 else np.fmin)(ext, e), np.where(trig, ext, e))
        trig |= t
    return trig, ext


def sweep_signal(names, kmin=0.1, kmax=2.0, R=4, f_hi=None, f_lo=None):
    """Sweep & reclaim on the union of level sets -> (short trig, short ext, long trig, long ext)."""
    build_levels()
    ps, pl = [], []
    for nm in names:
        lh, ll = LEVELS[nm]
        up, dn = sweep_masks(lh, ll, kmin, kmax, f_hi, f_lo)
        ps.append(reclaim(up, lh, R, -1, kmax))
        pl.append(reclaim(dn, ll, R, +1, kmax))
        del up, dn
    ts, es = union(ps, -1)
    tl, el = union(pl, +1)
    return ts, es, tl, el


# ---------------------------------------------------------------- exits
def exits(mb, struct):
    ex = dict(grid_exits(mb))
    if struct:
        sr = dict(sl_rule="sweep_extreme", buf_atr=0.25, min_atr=0.5, max_atr=4.0)
        ex["struct_2r"] = dict(sr, tps=[(2.0, 1.0)], max_bars=mb)
        ex["struct_3tp"] = dict(sr, tps=[(1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)], be_after_tp1=True, max_bars=mb)
    else:
        ex["wide_time"] = dict(sl_atr=8.0, tps=[], max_bars=mb)
    return ex


def run_struct(E, ext, ex):
    """Structural stop: beyond the sweep extreme + buf x ATR (clipped to min..max ATR), targets in R of that stop.
    The stop is placed after the entry fill, so using the entry price (next bar's open) is legitimate."""
    base = {k: v for k, v in ex.items() if k not in ("sl_rule", "buf_atr", "min_atr", "max_atr")}
    Oa, Aa = D.O, D.ATR
    out = []
    for (k, i, sg), x in zip(E, ext):
        e = i + 1
        if e >= NT:
            continue
        px, a = float(Oa[k, e]), float(Aa[k, i])
        if not (np.isfinite(px) and np.isfinite(a) and a > 0 and np.isfinite(x)):
            continue
        stop = x - ex["buf_atr"] * a if sg > 0 else x + ex["buf_atr"] * a
        sd = (px - stop) if sg > 0 else (stop - px)
        sd = min(max(sd, ex["min_atr"] * a), ex["max_atr"] * a)
        out += run([(k, i, sg)], dict(base, sl_pct=sd / px))
    return out


EXIT_SHORT_RU = {"1tp_1r": "одна цель 1R", "1tp_2r": "одна цель 2R", "3tp_be": "3 цели с безубытком",
                 "5tp_be": "5 целей с безубытком", "tp1_trail": "половина на 1R + трейлинг", "time": "только время (стоп 4 ATR)",
                 "wide_time": "только время (стоп 8 ATR)", "struct_2r": "стоп за экстремумом выноса, цель 2R",
                 "struct_3tp": "стоп за экстремумом выноса, 3 цели"}


def exit_ru(name, mb=MB):
    h = f"~{mb / 4:g} ч" if mb < 96 else f"~{mb / 96:g} сут"
    h2 = f"~{mb * 2 / 96:g} сут"
    st = "стоп за экстремумом выноса + 0,25 ATR (не ближе 0,5 и не дальше 4 ATR)"
    return {
        "1tp_1r": f"стоп 2 ATR(15м); одна цель 1R (= 2 ATR); иначе выход по времени через {h}",
        "1tp_2r": f"стоп 2 ATR(15м); одна цель 2R (= 4 ATR); иначе выход по времени через {h}",
        "3tp_be": f"стоп 2 ATR(15м); 3 цели 1R/2R/3R по 1/3, после первой цели стоп в безубыток; максимум {h}",
        "5tp_be": f"стоп 2 ATR(15м); 5 целей 0,5–2,5R по 20 %, после первой цели стоп в безубыток; максимум {h}",
        "tp1_trail": f"стоп 2 ATR(15м); половина позиции на 1R, затем стоп в безубыток и трейлинг 2 ATR от лучшего "
                     f"закрытия; максимум {h2}",
        "time": f"стоп 4 ATR(15м), без целей; выход по времени через {h}",
        "wide_time": f"аварийный стоп 8 ATR(15м), без целей; выход по времени через {h}",
        "struct_2r": f"{st}; одна цель 2R; иначе выход по времени через {h}",
        "struct_3tp": f"{st}; 3 цели 1R/2R/3R по 1/3, после первой цели стоп в безубыток; максимум {h}",
    }[name]


# ---------------------------------------------------------------- evaluation bookkeeping
TRIED = 0
REC: list[dict] = []
ENTRIES: dict = {}


def pkey(idea, side, params):
    return (idea, side, json.dumps(params, sort_keys=True))


def evaluate(idea, side, params, mask, gap=16, start=None, ext=None, mb=MB):
    """One signal (idea, side, params) x every exit of its type. Every exit run counts in `tried`."""
    global TRIED
    E = entries_from_mask(mask, side, gap, start)
    exa = np.array([ext[k, i] for k, i, _ in E], dtype=np.float64) if ext is not None else None
    ENTRIES[pkey(idea, side, params)] = (E, exa, gap, start)
    exs = exits(mb, ext is not None)
    first = len(REC)
    for en, ex in exs.items():
        T = run_struct(E, exa, ex) if "sl_rule" in ex else run(E, ex)
        st = stats(T)
        TRIED += 1
        REC.append(dict(idea=idea, side=side, params=params, exit=en, ex=ex, gap=gap, mb=mb, start=start, stats=st))
    best = max(REC[first:], key=lambda r: (r["stats"].get("train") or {}).get("avg_pct", -9))
    tr, te = best["stats"].get("train") or {}, best["stats"].get("test") or {}
    log(f"{idea:8s} {side:+d} {json.dumps(params):52s} n_sig={len(E):6d}  train-best exit {best['exit']:10s} "
        f"train {tr.get('avg_pct')} (n {tr.get('n')})  test {te.get('avg_pct')} (n {te.get('n')})")


def top_test_days(r, k=3):
    """The chosen variant re-run (same entries, same exit — not a new variant): its k best TEST days by summed return."""
    E, exa, gap, start = ENTRIES[pkey(r["idea"], r["side"], r["params"])]
    T = run_struct(E, exa, r["ex"]) if "sl_rule" in r["ex"] else run(E, r["ex"])
    by = {}
    for t in T:
        tt = int(D.times[t["e"]])
        if tt < HN.TRAIN_END:
            continue
        d = dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        n_, s_ = by.get(d, (0, 0.0))
        by[d] = (n_ + 1, s_ + t["ret"] * 100)
    tot = sum(v[1] for v in by.values())
    top = sorted(by.items(), key=lambda kv: -kv[1][1])[:k]
    return {"test_sum_pct": round(tot, 1), "days": [{"day": d, "trades": n_, "sum_pct": round(s_, 1)} for d, (n_, s_) in top]}


def train_pos_years(st):
    ys = st.get("years", {})
    return sum(1 for y in TRAIN_YEARS if ys.get(y, {}).get("n", 0) >= 10 and ys[y]["avg_pct"] > 0)


def nmin(idea):
    return 60 if (WIDE or idea in DERIV_IDEAS) else 100


def choose(idea, side):
    """Best variant of an idea/side by TRAIN only (TEST and per-year 2025/2026 figures are never looked at here)."""
    cand = [r for r in REC if r["idea"] == idea and r["side"] == side
            and (r["stats"].get("train") or {}).get("n", 0) >= nmin(idea)]
    if not cand:
        return None
    nyrs = len(TRAIN_YEARS) if (WIDE or idea not in DERIV_IDEAS) else 2
    need = nyrs - 1 if nyrs >= 4 else nyrs                     # 3 of 4 train years (2021–24), else every train year

    def ok_years(r):
        st = r["stats"]
        if idea in DERIV_IDEAS and not WIDE:
            ys = st.get("years", {})
            npos = sum(1 for y in (2023, 2024) if ys.get(y, {}).get("n", 0) >= 10 and ys[y]["avg_pct"] > 0)
        else:
            npos = train_pos_years(st)
        y24 = (st.get("years", {}).get(2024) or {}).get("avg_pct", -1)
        return npos >= need and y24 > 0

    def ok_pf5(r):
        return ((r["stats"].get("pf5") or {}).get("train_pct") or -1) > 0

    for pool in ([r for r in cand if ok_years(r) and ok_pf5(r)], [r for r in cand if ok_pf5(r)], cand):
        if pool:
            return max(pool, key=lambda r: r["stats"]["train"]["avg_pct"])
    return None


# ================================================================= 1. sweep & reclaim of obvious levels
def idea_sweep():
    build_levels()
    for nm in LEVELS_ALL:
        ts, es, tl, el = sweep_signal([nm])
        p = dict(level=nm, filt="base", kmin=0.1, kmax=2.0, R=4)
        evaluate("sweep", -1, p, ts, ext=es)
        evaluate("sweep", +1, p, tl, ext=el)
        del ts, es, tl, el
    rng = H - L
    with np.errstate(invalid="ignore", divide="ignore"):
        wu = (H - np.maximum(O, C)) >= 0.5 * rng
        wd = (np.minimum(O, C) - L) >= 0.5 * rng
    filt = {"wick": dict(kmin=0.1, kmax=2.0, R=1, f_hi=wu, f_lo=wd)}
    with np.errstate(invalid="ignore"):
        vs = V >= 2 * croll("V", 96, "mean")
    filt["vol"] = dict(kmin=0.1, kmax=2.0, R=4, f_hi=vs, f_lo=vs)
    filt["deep"] = dict(kmin=0.5, kmax=3.0, R=4)
    for fn, kw in filt.items():
        ts, es, tl, el = sweep_signal(LEVELS_ALL, **kw)
        p = dict(level="any", filt=fn, kmin=kw["kmin"], kmax=kw["kmax"], R=kw["R"])
        evaluate("sweep", -1, p, ts, ext=es)
        evaluate("sweep", +1, p, tl, ext=el)
        del ts, es, tl, el


# ================================================================= 2. equal highs / equal lows
def eq_pools(X, N, x, kmin=0.05, kmax=2.0, s=4, LMAX=48, min_sep=12):
    """X = H (equal highs) or -L (equal lows, mirrored). Pool = two swing points (fractal s+s bars, >= min_sep bars apart)
    within x of each other, nothing between them above the pool. It is known from the second point's confirmation bar on;
    the first bar after that which trades above the pool (and within N bars of the first point) is the sweep candidate.
    -> (sweep mask, pool level at sweep bars) in X units."""
    sw = np.zeros((NS, NT), bool)
    lev = np.full((NS, NT), np.nan, DT)
    rm = roll(X, 2 * s + 1, "max")                              # rm[i] = max X[i-2s .. i]  (centred on m = i - s)
    for k in range(NS):
        xk, rk = X[k].astype(np.float64), rm[k].astype(np.float64)
        with np.errstate(invalid="ignore"):
            m = np.flatnonzero(np.isfinite(rk[2 * s:]) & (xk[s:NT - s] >= rk[2 * s:])) + s
        n = len(m)
        if n < 2:
            continue
        h = xk[m]
        xs = np.where(np.isfinite(xk), xk, -np.inf)
        ST = [xs]                                               # sparse table: ST[q][i] = max xs[i .. i+2^q-1]
        for q in range(1, 10):
            hf = 1 << (q - 1)
            ST.append(np.maximum(ST[-1][:-hf], ST[-1][hf:]))
        starts, limits, lvls = [], [], []
        Mint = np.full(n, -np.inf)                              # max of the swing points strictly between the pair
        for lag in range(1, min(LMAX, n - 1) + 1):
            if lag > 1:
                Mint[lag:] = np.maximum(Mint[lag:], h[1:n - lag + 1])
            a = np.arange(lag, n)
            b = a - lag
            lv = np.maximum(h[a], h[b])
            st_ = m[a] + s + 1                                  # first bar after the confirmation of the 2nd point
            lim = np.minimum(m[b] + N, NT - 1)
            ok = ((m[a] - m[b] >= min_sep) & (np.abs(h[a] - h[b]) <= x * np.abs(lv)) & (Mint[lag:] <= lv)
                  & (st_ <= lim))
            if ok.any():
                starts.append(st_[ok])
                limits.append(lim[ok])
                lvls.append(lv[ok])
        if not starts:
            continue
        pos, lim, lv = np.concatenate(starts), np.concatenate(limits), np.concatenate(lvls)
        for q in range(9, -1, -1):                              # binary lifting: first bar with X > pool level
            stp = 1 << q
            can = pos + stp - 1 <= lim
            idx = np.where(can, pos, 0)
            jump = can & (ST[q][np.minimum(idx, len(ST[q]) - 1)] <= lv)
            pos = np.where(jump, pos + stp, pos)
        f = pos <= lim
        j, lv = pos[f], lv[f]
        a1 = A1[k, j].astype(np.float64)
        ok = (xs[j] > lv + kmin * a1) & (xs[j] <= lv + kmax * a1)
        j, lv = j[ok], lv[ok]
        best = np.full(NT, -np.inf)
        np.maximum.at(best, j, lv)                              # several pools swept by one bar -> the highest pool
        hit = best > -np.inf
        sw[k] = hit
        lev[k, hit] = best[hit]
    return sw, lev


def idea_eqhl():
    for N in (96, 288):
        for x in (0.001, 0.003):
            p = dict(N=N, x_pct=x * 100, swing=4, R=4)
            swu, levu = eq_pools(H, N, x)
            ts, es = reclaim(swu, levu, 4, -1, 2.0)
            del swu, levu
            evaluate("eqhl", -1, p, ts, ext=es)
            del ts, es
            swd, levd = eq_pools(-L, N, x)
            levd = -levd
            tl, el = reclaim(swd, levd, 4, +1, 2.0)
            del swd, levd
            evaluate("eqhl", +1, p, tl, ext=el)
            del tl, el


# ================================================================= 3. liquidation-backed sweeps (18-coin set)
def liq_spikes(q):
    key = ("liqspike", q)
    if key not in CACHE:
        if "liqmed" not in CACHE:
            CACHE["liqmed"] = (roll(SLQ, 2880, "median", minp=1440), roll(LLQ, 2880, "median", minp=1440))
        ms, ml = CACHE["liqmed"]
        qs, ql = roll(SLQ, 2880, "quantile", minp=1440, q=q), roll(LLQ, 2880, "quantile", minp=1440, q=q)
        with np.errstate(invalid="ignore"):
            CACHE[key] = ((SLQ >= qs) & (SLQ >= 2 * ms), (LLQ >= ql) & (LLQ >= 2 * ml))
    return CACHE[key]


def idea_liq():
    if not HAS_LIQ:
        log("liq: no liquidation data in this set -> idea 3 produces no entries (skipped)")
        return
    for q in (0.95, 0.99):
        sp_s, sp_l = liq_spikes(q)                              # up-sweep needs SHORT liquidations, down-sweep LONG ones
        ts, es, tl, el = sweep_signal(LEVELS_ALL, f_hi=sp_s & REAL, f_lo=sp_l & REAL)
        p = dict(level="any", liq_pctl_30d=q, liq_vs_median=2, kmin=0.1, kmax=2.0, R=4)
        evaluate("liq", -1, p, ts, start=START23, ext=es)
        evaluate("liq", +1, p, tl, start=START23, ext=el)
        del ts, es, tl, el


# ================================================================= 4. crowded positioning flush
def crowd_sides():
    """(longs crowded, shorts crowded) at bar i: funding if this set has it, else the 1 h basis premium."""
    if HAS_FUND:
        q80 = roll(FUND, 2880, "quantile", minp=1440, q=0.8)
        q20 = roll(FUND, 2880, "quantile", minp=1440, q=0.2)
        with np.errstate(invalid="ignore"):
            return (FUND >= 0.01) & (FUND >= q80), (FUND < 0.005) & (FUND <= q20), "funding"
    if BAS is None:
        return None, None, None
    b4 = roll(BAS, 4, "mean")
    mu, sd = roll(b4, 2880, "mean", minp=1440), roll(b4, 2880, "std", minp=1440)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (b4 - mu) / sd
        return (b4 > 0) & (z >= 1), (b4 < 0) & (z <= -1), "basis"


def idea_crowd():
    if not HAS_OI:
        log("crowd: no open interest in this set -> skipped")
        return
    with np.errstate(invalid="ignore", divide="ignore"):
        oi96 = np.log(OIc / shift(OIc, 96))
        r96 = np.log(C / shift(C, 96))
    s96 = roll(r96, 2880, "std", minp=1440)
    with np.errstate(invalid="ignore"):
        flat = np.abs(r96) <= 0.5 * s96
        up = C > croll("H", 96, "max")
        dn = C < croll("L", 96, "min")
    del r96, s96
    cl, cs, ckind = crowd_sides()
    start = None if WIDE else START23
    for x in (0.05, 0.10):
        with np.errstate(invalid="ignore"):
            setup = (oi96 >= x) & flat
        rec = shift(roll(setup.astype(np.float32), 8, "max") > 0)      # a setup bar in i-8 .. i-1
        for cf in ("none", "crowd"):
            if cf == "crowd" and cl is None:
                continue
            lm = rec & up & REAL & (cs if cf == "crowd" else True)    # break UP against crowded shorts
            sm = rec & dn & REAL & (cl if cf == "crowd" else True)    # break DOWN against crowded longs
            p = dict(oi_coins_24h=x, flat_sigma=0.5, crowd=(ckind if cf == "crowd" else "none"))
            evaluate("crowd", +1, p, lm, gap=96, start=start)
            evaluate("crowd", -1, p, sm, gap=96, start=start)
    del oi96, flat, up, dn


# ================================================================= 5. failed classic breakout = trap
def idea_trap():
    for N in (96, 288):
        lh, ll = croll("H", N, "max"), croll("L", N, "min")
        with np.errstate(invalid="ignore"):
            bu = (C > lh) & (shift(C) <= shift(lh))             # first close above the N-bar high
            bd = (C < ll) & (shift(C) >= shift(ll))
        for W in (4, 16):
            p = dict(kind=f"donch{N}", W=W)
            ts, es = reclaim(bu, lh, W + 1, -1, None)
            evaluate("trap", -1, p, ts, ext=es)
            del ts, es
            tl, el = reclaim(bd, ll, W + 1, +1, None)
            evaluate("trap", +1, p, tl, ext=el)
            del tl, el
    mid, sd = roll(C, 20, "mean"), roll(C, 20, "std")
    ub, lb = mid + 2 * sd, mid - 2 * sd
    with np.errstate(invalid="ignore", divide="ignore"):
        bbw = 4 * sd / mid
    del mid, sd
    rank = pd.DataFrame(np.asarray(bbw, np.float64).T).rolling(480, min_periods=480).rank(pct=True, method="max")
    rank = np.ascontiguousarray(rank.to_numpy().T, dtype=DT)
    del bbw
    with np.errstate(invalid="ignore"):
        sq = shift(roll((rank <= 0.2).astype(np.float32), 16, "max") > 0)
    del rank
    bh, bl = croll("H", 48, "max"), croll("L", 48, "min")
    with np.errstate(invalid="ignore"):
        bu = sq & (C > ub) & (shift(C) <= shift(ub)) & (C > bh)
        bd = sq & (C < lb) & (shift(C) >= shift(lb)) & (C < bl)
    del ub, lb, sq
    p = dict(kind="bbsqueeze_box48", W=8)
    ts, es = reclaim(bu, bh, 9, -1, None)
    evaluate("trap", -1, p, ts, ext=es)
    del ts, es
    tl, el = reclaim(bd, bl, 9, +1, None)
    evaluate("trap", +1, p, tl, ext=el)


# ================================================================= 6. round-number sweeps
def idea_round():
    P = shift(C).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        step = 10.0 ** (np.floor(np.log10(P)) - 1)
    del P
    M16, m16 = croll("H", 16, "max").astype(np.float64), croll("L", 16, "min").astype(np.float64)
    for mult, nm in ((1.0, "major"), (0.5, "half")):
        st = step * mult
        with np.errstate(invalid="ignore", divide="ignore"):
            lu = ((np.floor(M16 / st) + 1) * st).astype(DT)     # first round level above the last 4 h high
            ld = ((np.ceil(m16 / st) - 1) * st).astype(DT)
        up, dn = sweep_masks(lu, ld, 0.1, 2.0)
        p = dict(step=nm, kmin=0.1, kmax=2.0, R=4)
        ts, es = reclaim(up, lu, 4, -1, 2.0)
        evaluate("round", -1, p, ts, ext=es)
        del ts, es
        tl, el = reclaim(dn, ld, 4, +1, 2.0)
        evaluate("round", +1, p, tl, ext=el)
        del tl, el, lu, ld, up, dn


# ================================================================= 7. low-liquidity hours: fade at the session open
def us_open_bar(nd):
    """Per day segment: index of the 15m bar that CLOSES at 09:30 New York time (DST-aware)."""
    ny = ZoneInfo("America/New_York")
    out = np.empty(nd, np.int64)
    for d in range(nd):
        t = dt.datetime.fromtimestamp((int(DAYN[0]) + d) * 86400 + 12 * 3600, dt.timezone.utc)
        off_h = -int(t.astimezone(ny).utcoffset().total_seconds() // 3600)   # 4 (EDT) or 5 (EST)
        out[d] = (9 + off_h) * 4 + 2 - 1                        # 13:30 UTC (EDT) -> bar 53 closes at 13:30
    return out


def idea_night():
    Od, Cd = seg_reshape(O, 96, OFF_D, np.nan), seg_reshape(C, 96, OFF_D, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        s16 = roll(np.log(C / shift(C, 16)), 2880, "std", minp=1440)
        s96 = roll(np.log(C / shift(C, 96)), 2880, "std", minp=1440)
    S16d, S96d = seg_reshape(s16, 96, OFF_D, np.nan), seg_reshape(s96, 96, OFF_D, np.nan)
    del s16, s96
    nd = Od.shape[1]
    ar = np.arange(nd)
    dow = (DAYN[0] + ar + 3) % 7                                # 0 = Monday
    usb = us_open_bar(nd)
    sess = {"eu": np.full(nd, 27), "us": usb}                   # signal bar: closes at 07:00 UTC / 09:30 New York

    def to_mask(cond_d, sbar):
        M = np.zeros((NS, nd, 96), bool)
        M[:, ar, sbar] = cond_d
        return seg_unshape(M, OFF_D)

    with np.errstate(invalid="ignore", divide="ignore"):
        o00 = Od[:, :, 0]
        mv = np.log(Cd[:, :, 15] / o00)                         # 00:00 -> 04:00 UTC move
        z = mv / S16d[:, :, 15]
        wk_o = np.full_like(o00, np.nan)
        wk_o[:, 2:] = o00[:, :-2]                               # Saturday 00:00 open (for Mondays)
        wk_c = np.full_like(o00, np.nan)
        wk_c[:, 1:] = Cd[:, :-1, 95]                            # Sunday last close
        wk_s = np.full_like(o00, np.nan)
        wk_s[:, 1:] = S96d[:, :-1, 95]
        wmv = np.log(wk_c / wk_o)
        wz = wmv / (wk_s * np.sqrt(2.0))
    weekday = (dow <= 4)[None, :]
    monday = (dow == 0)[None, :]
    variants = [("night", 2.0, "eu"), ("night", 3.0, "eu"), ("night", 2.0, "us"), ("weekend", 1.5, "eu"),
                ("weekend", 1.5, "us")]
    for kind, k, ss in variants:
        sb = sess[ss]
        cs = Cd[:, ar, sb]
        with np.errstate(invalid="ignore", divide="ignore"):
            if kind == "night":
                ref, m_, zz, ok_day = o00, mv, z, weekday
            else:
                ref, m_, zz, ok_day = wk_o, wmv, wz, monday
            intact = np.log(cs / ref) * np.sign(m_) >= 0.5 * np.abs(m_)
            cond = ok_day & (np.abs(zz) >= k) & intact
        p = dict(kind=kind, k_sigma=k, session=ss)
        evaluate("night", +1, p, to_mask(cond & (m_ < 0), sb), gap=1)     # fade a DOWN move -> long
        evaluate("night", -1, p, to_mask(cond & (m_ > 0), sb), gap=1)


# ================================================================= 8. sweep with an open-interest flush / jump
def idea_oisweep():
    if not HAS_OI:
        log("oisweep: no open interest in this set -> skipped")
        return
    with np.errstate(invalid="ignore", divide="ignore"):
        d1 = np.log(OIc / shift(OIc, 1))
    sd = roll(d1, 2880, "std", minp=1440)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = d1 / sd
    del d1, sd
    start = None if WIDE else START23
    for mode in ("drop", "jump"):
        with np.errstate(invalid="ignore"):
            f = ((z <= -1.5) if mode == "drop" else (z >= 1.5)) & REAL
        ts, es, tl, el = sweep_signal(LEVELS_ALL, f_hi=f, f_lo=f)
        p = dict(level="any", oi_mode=mode, oi_z=1.5, kmin=0.1, kmax=2.0, R=4)
        evaluate("oisweep", -1, p, ts, start=start, ext=es)
        evaluate("oisweep", +1, p, tl, start=start, ext=el)
        del ts, es, tl, el, f
    del z


IDEA_FUNCS = {"sweep": idea_sweep, "eqhl": idea_eqhl, "liq": idea_liq, "crowd": idea_crowd, "trap": idea_trap,
              "round": idea_round, "night": idea_night, "oisweep": idea_oisweep}


# ================================================================= texts for the panel
def side_ru(s):
    return "лонг" if s > 0 else "шорт"


LEVEL_RU = {   # (above: genitive, below: genitive)
    "pd": ("вчерашнего максимума (UTC-сутки)", "вчерашнего минимума (UTC-сутки)"),
    "pw": ("максимума прошлой недели", "минимума прошлой недели"),
    "r3": ("максимума последних 3 суток", "минимума последних 3 суток"),
    "asia": ("максимума азиатской сессии 00–07 UTC (вынос во время Лондона, 07–13 UTC)",
             "минимума азиатской сессии 00–07 UTC (вынос во время Лондона, 07–13 UTC)"),
    "any": ("любого очевидного максимума (вчерашний день, прошлая неделя, последние 3 суток, азиатская сессия)",
            "любого очевидного минимума (вчерашний день, прошлая неделя, последние 3 суток, азиатская сессия)")}
LEVEL_SHORT_RU = {"pd": ("вчерашнего максимума", "вчерашнего минимума"), "pw": ("максимума недели", "минимума недели"),
                  "r3": ("максимума 3 суток", "минимума 3 суток"), "asia": ("максимума Азии", "минимума Азии"),
                  "any": ("очевидного уровня", "очевидного уровня")}


def candles(n):
    """'4 свечи' / '8 свечей' / '1 свечу' (after 'через')."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} свечу"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} свечи"
    return f"{n} свечей"


def days_ru(n_bars):
    return "сутки" if n_bars == 96 else f"{n_bars // 96} суток"


def fnum(x):
    return f"{x:g}".replace(".", ",")


def texts(r):
    p, s = r["params"], r["side"]
    ex = exit_ru(r["exit"], r["mb"])
    ex = ex[0].upper() + ex[1:]
    idea = r["idea"]
    beyond = "выше" if s < 0 else "ниже"
    back = "ниже" if s < 0 else "выше"
    stops = "над уровнем" if s < 0 else "под уровнем"
    entry = "вход на открытии следующей свечи 15м, не чаще раза в 4 ч по монете"
    if idea in ("sweep", "liq", "oisweep"):
        lv = p["level"]
        nm_lvl = LEVEL_SHORT_RU[lv][0 if s < 0 else 1]
        base = (f"{side_ru(s).capitalize()}: цена прошла {beyond} {LEVEL_RU[lv][0 if s < 0 else 1]}, который до этого не трогала, "
                f"на {fnum(p['kmin'])}–{fnum(p['kmax'])} ATR(15м) — собрала стопы {stops}, — и ")
        if p.get("R", 4) == 1:
            base += f"та же 15-минутная свеча закрылась обратно {back} уровня"
        else:
            base += (f"не позже чем через {candles(p['R'] - 1)} 15м после свечи выноса (или в ней самой) закрылась обратно "
                     f"{back} уровня")
        if idea == "sweep":
            name = f"Вынос {nm_lvl} и возврат ({side_ru(s)})"
            extra = {"base": "", "wick": f"; тень свечи выноса ≥ 50 % её размера",
                     "vol": "; объём свечи выноса ≥ 2× среднего за прошедшие сутки",
                     "deep": " (глубокий вынос)"}[p["filt"]]
            if p["filt"] != "base":
                name = f"Вынос уровня: {dict(wick='длинная тень', vol='всплеск объёма', deep='глубокий вынос')[p['filt']]} ({side_ru(s)})"
        elif idea == "liq":
            name = f"Вынос уровня со всплеском ликвидаций ({side_ru(s)})"
            who = "шортов" if s < 0 else "лонгов"
            extra = (f"; в свече выноса ликвидации {who} выше {fnum(p['liq_pctl_30d'] * 100)}-го перцентиля этой монеты за "
                     f"30 дней и ≥ 2× её медианы (данные с 2023 г.)")
        else:
            name = f"Вынос уровня с {'резким падением' if p['oi_mode'] == 'drop' else 'резким ростом'} ОИ ({side_ru(s)})"
            extra = ("; в свече выноса открытый интерес (в монетах) резко упал — сработали стопы/ликвидации"
                     if p["oi_mode"] == "drop" else
                     "; в свече выноса открытый интерес (в монетах) резко вырос — набежали «пробойщики», которые попали в ловушку")
            extra += " (изменение ≥ 1,5 типичных 15-минутных изменений за 30 дней)"
        rule = f"{base}{extra}; {entry}. {ex}."
    elif idea == "eqhl":
        what = "максимума" if s < 0 else "минимума"
        name = f"Вынос равных {'максимумов' if s < 0 else 'минимумов'} ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()}: за последние {days_ru(p['N'])} есть два локальных {what} "
                f"(разворотные точки, 4 свечи с каждой стороны) не ближе 3 ч друг к другу и отличающиеся не больше чем на "
                f"{fnum(p['x_pct'])} %, между ними цена их не пробивала — там копятся стопы. Цена выносит этот уровень "
                f"(на 0,05–2 ATR) и не позже чем через 3 свечи после выноса (или в той же свече) закрывается обратно {back} него; {entry}. {ex}.")
    elif idea == "crowd":
        name = f"Перегруз позиций → пробой против толпы ({side_ru(s)})"
        cr = {"none": "",
              "funding": (" и толпа стоит против пробоя: " + ("фандинг ≥ 0,01 % и в верхних 20 % за 30 дней (лонгисты переплачивают)"
                                                              if s < 0 else "фандинг < 0,005 % и в нижних 20 % за 30 дней (шортисты переплачивают)")),
              "basis": (" и толпа стоит против пробоя: " + ("фьючерс дороже спота сильнее обычного (лонгисты переплачивают)"
                                                            if s < 0 else "фьючерс дешевле спота сильнее обычного (шортисты переплачивают)"))}[p["crowd"]]
        rule = (f"{side_ru(s).capitalize()}: за сутки открытый интерес в монетах вырос на ≥ {fnum(p['oi_coins_24h'] * 100)} %, а цена "
                f"почти не сдвинулась (меньше половины обычного дневного хода) — позиции копятся; в ближайшие 2 ч цена закрывается "
                f"{'выше максимума' if s > 0 else 'ниже минимума'} последних суток{cr} — входим по пробою (выносит толпу); "
                f"вход на открытии следующей свечи, не чаще раза в сутки по монете. {ex}.")
    elif idea == "trap":
        if p["kind"].startswith("donch"):
            days = {96: "суток", 288: "3 суток"}[int(p["kind"][5:])]
            name = f"Ложный пробой канала {days} → против ({side_ru(s)})"
            rule = (f"{side_ru(s).capitalize()}: свеча 15м закрылась {'выше максимума' if s < 0 else 'ниже минимума'} последних {days} "
                    f"(классический пробой), но не позже чем через {candles(p['W'])} цена закрылась обратно {back} этого уровня — "
                    f"ловушка для {'покупателей' if s < 0 else 'продавцов'}; входим в обратную сторону; {entry}. {ex}.")
        else:
            name = f"Ложный пробой после сжатия Боллинджера ({side_ru(s)})"
            rule = (f"{side_ru(s).capitalize()}: после сжатия полос Боллинджера (ширина в нижних 20 % за 5 дней в последние 4 ч) свеча "
                    f"закрылась {'выше верхней' if s < 0 else 'ниже нижней'} полосы и за пределами 12-часового диапазона, но не позже "
                    f"чем через {candles(p['W'])} вернулась обратно в диапазон — входим против пробоя; {entry}. {ex}.")
    elif idea == "round":
        stp = "1000 / 100 / 10 / 0,01" if p["step"] == "major" else "500 / 50 / 5 / 0,005"
        name = f"Вынос круглого уровня ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()}: ближайший круглый уровень {beyond} цены (шаг по масштабу цены: BTC около 60 000 — {stp.split(' / ')[0]}, "
                f"ETH около 3000 — {stp.split(' / ')[1]}, SOL около 150 — {stp.split(' / ')[2]}, DOGE около 0,15 — {stp.split(' / ')[3]}), "
                f"который цена не трогала последние 4 ч; цена проходит его на 0,1–2 ATR и не позже чем через 3 свечи после выноса закрывается "
                f"обратно {back} — входим в разворот; {entry}. {ex}.")
    else:
        sess = "открытии Европы (07:00 UTC)" if p["session"] == "eu" else "открытии Нью-Йорка (09:30 по Нью-Йорку)"
        if p["kind"] == "night":
            name = f"Ночной рывок → против на {('открытии Европы' if p['session'] == 'eu' else 'открытии США')} ({side_ru(s)})"
            rule = (f"{side_ru(s).capitalize()}: в будний день с 00:00 до 04:00 UTC (тонкий рынок) цена {'упала' if s > 0 else 'выросла'} "
                    f"на ≥ {fnum(p['k_sigma'])} типичных 4-часовых движения этой монеты (за 30 дней) и к моменту входа держит ≥ половины "
                    f"хода — входим против рывка на {sess}. {ex}.")
        else:
            name = f"Рывок выходных → против в понедельник ({side_ru(s)})"
            rule = (f"{side_ru(s).capitalize()}: с субботы 00:00 до конца воскресенья (UTC) цена {'упала' if s > 0 else 'выросла'} на ≥ "
                    f"{fnum(p['k_sigma'])} типичных двухдневных движения монеты и в понедельник ещё держит ≥ половины хода — входим "
                    f"против рывка на {sess} в понедельник. {ex}.")
    return name, rule


# ================================================================= main
def pct(x, nd=2):
    return "—" if x is None else f"{x:+.{nd}f}".replace(".", ",")


def main():
    global TRIED
    set_arrays()
    log(f"data: {'wide top-' + str(HN.TOPN) if WIDE else '18 coins'}  coins={NS} bars={NT}  train years={TRAIN_YEARS}  "
        f"OI={HAS_OI} liq={HAS_LIQ} funding={HAS_FUND}")
    only = os.environ.get("MANIP_ONLY")
    for nm in IDEAS:
        if only and nm not in only.split(","):
            continue
        log(f"=== {nm}")
        IDEA_FUNCS[nm]()
        log(f"    maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB, tried so far {TRIED}")
    LEVELS.clear()
    CACHE.clear()
    log(f"grid done, tried = {TRIED}")

    # ---- choose per idea/side on TRAIN only
    chosen = []
    for idea in IDEAS:
        for side in (+1, -1):
            r = choose(idea, side)
            if r is not None:
                chosen.append(r)

    # ---- liquidation control: the same sweeps (union of levels, 2023+) WITHOUT the liquidation filter, same exit as the
    #      chosen liq variant -> does the liquidation spike add anything? (counted in tried, not a candidate)
    ctrl = {}
    if HAS_LIQ and any(r["idea"] == "liq" for r in chosen):
        ts, es, tl, el = sweep_signal(LEVELS_ALL, f_hi=REAL, f_lo=REAL)
        for r in chosen:
            if r["idea"] != "liq":
                continue
            m, e = (ts, es) if r["side"] < 0 else (tl, el)
            E = entries_from_mask(m, r["side"], r["gap"], START23)
            exa = np.array([e[k, i] for k, i, _ in E])
            T = run_struct(E, exa, r["ex"]) if "sl_rule" in r["ex"] else run(E, r["ex"])
            TRIED += 1
            st = stats(T)
            ctrl[r["side"]] = st
            log(f"liq control (no liquidation filter) {r['side']:+d} {r['exit']}: train {st['train'].get('avg_pct')} "
                f"(n {st['train'].get('n')}) test {st['test'].get('avg_pct')} (n {st['test'].get('n')}) pf5 {st['pf5'].get('test_pct')}")
        del ts, es, tl, el
        LEVELS.clear()
        CACHE.clear()

    # ---- exit league over every signal variant, separately for the sweep-type ideas (6 grid + 2 structural exits) and for
    #      ideas 4 and 7 (6 grid exits + wide_time), so every exit is compared on the same signals
    league = {}
    for grp, ids in (("sweep_type", SWEEP_IDEAS), ("crowd_night", ("crowd", "night"))):
        g = {}
        for en in list(exits(MB, grp == "sweep_type").keys()):
            rows = [r for r in REC if r["idea"] in ids and r["exit"] == en and (r["stats"].get("train") or {}).get("n", 0) >= 50]
            if not rows:
                continue
            tr = [r["stats"]["train"]["avg_pct"] for r in rows]
            te = [r["stats"]["test"]["avg_pct"] for r in rows if (r["stats"].get("test") or {}).get("n", 0) >= 20]
            p5 = [x for x in ((r["stats"].get("pf5") or {}).get("test_pct") for r in rows) if x is not None]
            g[en] = {"variants": len(rows), "train_mean": round(float(np.mean(tr)), 3),
                     "test_mean": round(float(np.mean(te)), 3) if te else None,
                     "train_pos_share": round(float(np.mean(np.array(tr) > 0)), 2),
                     "test_pos_share": round(float(np.mean(np.array(te) > 0)), 2) if te else None,
                     "pf5_test_pos_share": round(float(np.mean(np.array(p5) > 0)), 2) if p5 else None, "train_best_count": 0}
        sigs = {}
        for r in REC:
            if r["idea"] in ids:
                sigs.setdefault(pkey(r["idea"], r["side"], r["params"]), []).append(r)
        for rs_ in sigs.values():
            ok = [r for r in rs_ if (r["stats"].get("train") or {}).get("n", 0) >= 50]
            if ok:
                g[max(ok, key=lambda r: r["stats"]["train"]["avg_pct"])["exit"]]["train_best_count"] += 1
        league[grp] = g
        print(f"\nEXIT LEAGUE {grp} (mean avg_pct over its signal variants with train n >= 50)")
        for en, v in g.items():
            print(f"  {en:10s} {v}")
    # gross (before the 0.14 % costs) per-trade TEST average of the sweep-type ideas, all variants and exits
    te_sw = [r["stats"]["test"]["avg_pct"] for r in REC if r["idea"] in SWEEP_IDEAS and (r["stats"].get("test") or {}).get("n", 0) >= 30]
    gross_sw = round(float(np.mean(te_sw)) + HN.COST * 100, 3) if te_sw else None
    if te_sw:
        print(f"sweep-type ideas: mean TEST avg per trade {np.mean(te_sw):+.3f} % after costs -> {gross_sw:+.3f} % before costs")

    # ---- per idea: how the whole grid behaves (shown, not used to choose)
    grid = {}
    for idea in IDEAS:
        for side in (+1, -1):
            rows = [r for r in REC if r["idea"] == idea and r["side"] == side and (r["stats"].get("train") or {}).get("n", 0) >= 30]
            if not rows:
                continue
            te = [r for r in rows if (r["stats"].get("test") or {}).get("n", 0) >= 30]
            grid[(idea, side)] = {
                "variants": len(rows),
                "train_pos_share": round(float(np.mean([r["stats"]["train"]["avg_pct"] > 0 for r in rows])), 2),
                "test_pos_share": round(float(np.mean([r["stats"]["test"]["avg_pct"] > 0 for r in te])), 2) if te else None,
                "pf5_test_pos_share": round(float(np.mean([((r["stats"].get("pf5") or {}).get("test_pct") or -1) > 0 for r in te])), 2) if te else None,
                "pass_share": round(float(np.mean([passes(r["stats"]) for r in rows])), 3)}

    # ---- summary table
    print("\nCHOSEN (train-selected) per idea/side")
    print(f"{'idea':8s}{'side':>5s} {'params':56s}{'exit':11s}{'trn avg':>8s}{'trn n':>7s}{'tst avg':>8s}{'tst n':>7s}"
          f"{'pf5 te':>8s}{'2025':>7s}{'2026':>7s}{'ex3':>7s}{'maxDD':>7s}  pass")
    results = []
    for r in chosen:
        st = r["stats"]
        pf = st.get("pf5") or {}
        ys = pf.get("years") or {}
        print(f"{r['idea']:8s}{r['side']:+5d} {json.dumps(r['params']):56s}{r['exit']:11s}{st['train']['avg_pct']:8.3f}"
              f"{st['train']['n']:7d}{(st['test'].get('avg_pct') or 0):8.3f}{st['test'].get('n', 0):7d}"
              f"{(pf.get('test_pct') or 0):8.1f}{(ys.get(2025) or 0):7.1f}{(ys.get(2026) or 0):7.1f}"
              f"{(pf.get('test_ex3_pct') or 0):7.1f}{(pf.get('max_dd_pct') or 0):7.1f}  {'YES' if passes(st) else 'no'}")
        print(f"{'':14s}years per trade: " + " ".join(f"{y}:{v.get('avg_pct')}({v.get('n')})" for y, v in st["years"].items())
              + f" | pf5 years {ys} | grid {grid.get((r['idea'], r['side']))}")
        name, rule = texts(r)
        params = dict(r["params"], side=r["side"], gap=r["gap"], max_bars=r["mb"],
                      data_from=("2023-01" if (r["idea"] in DERIV_IDEAS and not WIDE) else str(TRAIN_YEARS[0])))
        sel = ("лучшее среднее на сделку за {tr} среди вариантов с ≥ {n} сделок; в приоритете варианты, у которых плюсовые почти все годы "
               "обучения, плюсовой 2024 г. и плюсовой счёт на 5 позиций на обучении").format(
            tr=("2023–2024" if (WIDE or r["idea"] in DERIV_IDEAS) else "2021–2024"), n=nmin(r["idea"]))
        res = {"id": f"manip_{r['idea']}_{'long' if r['side'] > 0 else 'short'}", "name_ru": name, "rule_ru": rule,
               "exit": dict(r["ex"], name=r["exit"]), "params": params, "selection_ru": sel,
               "idea_grid": grid.get((r["idea"], r["side"])), "top_test_days": top_test_days(r), "stats": st}
        if r["idea"] == "liq" and r["side"] in ctrl:
            cs_ = ctrl[r["side"]]
            res["control_without_liq_filter"] = {"train": cs_.get("train"), "test": cs_.get("test"), "pf5": cs_.get("pf5")}
        results.append(res)

    print("\nTOP-15 variants by TRAIN avg (train n >= NMIN)")
    top = sorted([r for r in REC if (r["stats"].get("train") or {}).get("n", 0) >= nmin(r["idea"])],
                 key=lambda r: -r["stats"]["train"]["avg_pct"])[:15]
    for r in top:
        st = r["stats"]
        print(f"  {r['idea']:8s}{r['side']:+3d} {json.dumps(r['params']):56s}{r['exit']:11s} train {st['train']['avg_pct']:7.3f} "
              f"(n {st['train']['n']:5d}) test {st['test'].get('avg_pct')} (n {st['test'].get('n')}) "
              f"pf5 test {(st.get('pf5') or {}).get('test_pct')} pass {passes(st)}")
    npass_all = sum(passes(r["stats"]) for r in REC)
    print(f"\nvariants that pass among ALL {len(REC)} evaluated (not a selection, just a count): {npass_all}")

    if only:
        log("MANIP_ONLY set (debug run) -> results not written")
        return
    notes_ru = make_notes(results, league, grid, ctrl, npass_all, gross_sw)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    report(os.path.join(HERE, "results", "manip.json"), "manip", results, TRIED, notes_ru)
    allv = [dict(idea=r["idea"], side=r["side"], params=r["params"], exit=r["exit"], train=r["stats"].get("train"),
                 test=r["stats"].get("test"), pos_years=r["stats"].get("pos_years"),
                 years={y: v.get("avg_pct") for y, v in r["stats"].get("years", {}).items()},
                 pf5={k: (r["stats"].get("pf5") or {}).get(k) for k in ("train_pct", "test_pct", "test_ex3_pct", "years", "max_dd_pct")},
                 test_ex3_avg=r["stats"].get("test_ex3_avg"), passes=passes(r["stats"])) for r in REC]
    with open(os.path.join(HERE, "results", f"_manip_all_variants{HN.SUFFIX}.json"), "w", encoding="utf-8") as f:
        json.dump({"tried": TRIED, "league": league, "gross_sweep_type_test": gross_sw, "variants": allv}, f, ensure_ascii=False, default=float)
    log(f"done, tried = {TRIED}, passes = {sum(passes(r['stats']) for r in results)}/{len(results)}, "
        f"maxrss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB")


MONTHS_RU = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def DATE_RU(d):
    y, m, dd = d.split("-")
    return f"{int(dd)} {MONTHS_RU[int(m) - 1]} {y}"


def make_notes(results, league, grid, ctrl, npass_all, gross_sw):
    """Plain-Russian summary for the owner; every number is filled from this run."""
    tr_p = ("2023–2024" if WIDE else
            "2021–2024 (идеи с ликвидациями, открытым интересом и фандингом — 2023–2024: раньше эти данные восстановлены, а не настоящие)")
    passed = [r for r in results if passes(r["stats"])]
    out = []
    out.append(
        "Что проверяли. Мысль владельца: классические фигуры больше не работают, потому что крупные игроки знают, где стоят стопы и "
        "ликвидации толпы, выносят цену чуть за очевидный уровень, собирают эту ликвидность и разворачивают. 8 идей, лонг и шорт отдельно: "
        "(1) вынос вчерашнего максимума/минимума, уровня прошлой недели, 3-дневного диапазона и азиатской сессии с возвратом за уровень "
        "за 1–4 свечи 15м (варианты: длинная тень, всплеск объёма, глубокий вынос); (2) вынос «равных вершин/днищ», где копятся стопы; "
        "(3) вынос уровня вместе со всплеском ликвидаций" + (" (в этом наборе ликвидаций нет — идея пропущена)" if not HAS_LIQ else "") +
        "; (4) перегруз позиций: открытый интерес растёт, цена стоит, затем пробой против толпы — идём по пробою; (5) ложный пробой канала "
        "1–3 суток или сжатия Боллинджера — торгуем против пробоя («фигура не сработала — переворачиваемся»); (6) вынос круглого уровня; "
        "(7) резкое движение ночью (00–04 UTC) или в выходные — входим против него на открытии Европы или США; (8) вынос уровня с резким "
        "падением или ростом открытого интереса (замена ликвидациям). "
        f"Всего проверено вариантов: {TRIED}. Вход — на открытии следующей свечи после сигнала, комиссии 0,14 % за сделку. Параметры "
        f"и выход выбирались только по {tr_p}; 2025–2026 при выборе не смотрели.")
    out.append(f"Итог: прошли все фильтры лаборатории {len(passed)} из {len(results)} лучших вариантов"
               + (": " + "; ".join(f"«{r['name_ru']}»" for r in passed) if passed else "")
               + f". Если не выбирать, а просто посчитать — проходят {npass_all} из {len(REC)} вариантов.")
    lucky = [r for r in REC if passes(r["stats"])]
    chosen_keys = {(r["id"], json.dumps(r["exit"], sort_keys=True, default=float)) for r in results}
    lucky = [r for r in lucky if (f"manip_{r['idea']}_{'long' if r['side'] > 0 else 'short'}",
                                  json.dumps(dict(r["ex"], name=r["exit"]), sort_keys=True, default=float)) not in chosen_keys]
    if lucky and len(lucky) <= 3:
        parts = []
        for r in lucky:
            st, pf = r["stats"], r["stats"].get("pf5") or {}
            ys = pf.get("years") or {}
            ch = [x for x in results if x["id"] == f"manip_{r['idea']}_{'long' if r['side'] > 0 else 'short'}"]
            parts.append(
                f"«{texts(r)[0]}», выход «{EXIT_SHORT_RU[r['exit']]}»"
                + (f" ({r['params'].get('kind') == 'weekend' and 'вход в понедельник на ' or ''}"
                   f"{'открытии Европы' if r['params'].get('session') == 'eu' else 'открытии США'})" if r["idea"] == "night" else "")
                + f": на 2025–26 {pct(st['test'].get('avg_pct'))} % на сделку (сделок {st['test'].get('n', 0)}), счёт на 5 позиций "
                f"2025 {pct(ys.get(2025), 1)} %, 2026 {pct(ys.get(2026), 1)} %, без 3 лучших дней {pct(pf.get('test_ex3_pct'), 1)} %; "
                f"на обучении {pct(st['train'].get('avg_pct'))} %"
                + (f" — меньше, чем у выбранного варианта этой идеи ({pct(ch[0]['stats']['train'].get('avg_pct'))} %), поэтому по "
                   "правилам он не выбран" if ch else ""))
        out.append("Не выбранные по обучению, но формально прошедшие варианты: " + "; ".join(parts) + ". Найти 1–3 «прошедших» среди "
                   f"{len(REC)} вариантов можно и случайно, а выбрать их можно, только посмотрев на 2025–26, — поэтому это кандидат "
                   "для проверки на новых данных (вперёд), а не готовое правило.")
    # sweep-type ideas
    sw = [r for r in REC if r["idea"] in SWEEP_IDEAS and (r["stats"].get("test") or {}).get("n", 0) >= 30]
    if sw and gross_sw is not None:
        pos = np.mean([r["stats"]["test"]["avg_pct"] > 0 for r in sw])
        pf_pos = np.mean([((r["stats"].get("pf5") or {}).get("test_pct") or -1) > 0 for r in sw])
        out.append(
            f"Выносы уровней (идеи 1, 2, 3, 5, 6, 8; вариантов с достаточным числом сделок: {len(sw)}): на 2025–26 в плюсе после "
            f"комиссий {pos:.0%} вариантов, счёт на 5 позиций в плюсе у {pf_pos:.0%}. До комиссий такие сделки в среднем дают "
            f"{pct(gross_sw, 2)} %" + (" — то есть после выноса и возврата цена дальше идёт в сторону разворота не чаще, чем в обратную. "
                                       if abs(gross_sw) < 0.05 else ". ") +
            "Сам «вынос стопов» на графике бывает постоянно (тысячи случаев), но по нему нельзя заранее сказать, развернётся цена или "
            "продолжит движение: когда уровень пробит по-настоящему, тот же рисунок заканчивается пробоем. А 0,14 % комиссии съедают "
            "сделку, у которой нет перевеса.")
    if ctrl:
        lr = {r["params"]["side"]: r for r in results if r["id"].startswith("manip_liq")}
        parts = []
        for sd_, v in ctrl.items():
            r = lr.get(sd_)
            if r is None:
                continue
            parts.append(f"{side_ru(sd_)}: со всплеском ликвидаций {pct(r['stats']['test'].get('avg_pct'))} %, без условия "
                         f"{pct((v.get('test') or {}).get('avg_pct'))} % на сделку на 2025–26")
        liq_pos = any((r["stats"]["test"].get("avg_pct") or -1) > 0 for r in lr.values())
        out.append(("Условие на всплеск ликвидаций сделку прибыльной не делает: " if not liq_pos else "Сравнение с ликвидациями и без: ")
                   + "; ".join(parts) + " (те же выносы с 2023 г., тот же выход).")
    # night / weekend
    nl = [r for r in results if r["id"] == "manip_night_long"]
    g = grid.get(("night", +1))
    if nl and g:
        st, pf = nl[0]["stats"], nl[0]["stats"].get("pf5") or {}
        ys = pf.get("years") or {}
        out.append(
            ("Единственное, что стабильно в плюсе, — " if g["test_pos_share"] >= 0.6 else "Отдельно: ")
            + f"покупка после резкого ночного или выходного обвала (идея 7, лонг): в плюсе на "
            f"2025–26 {g['test_pos_share']:.0%} вариантов этой сетки, выбранный вариант «{nl[0]['name_ru']}» — "
            f"{pct(st['train'].get('avg_pct'))} % на сделку на обучении и {pct(st['test'].get('avg_pct'))} % на 2025–26. Но сделок мало "
            f"({st['test'].get('n', 0)} за 2025–26), прибыль делают несколько дней общего обвала, после которых рынок отскочил "
            f"(лучшие дни: {', '.join(DATE_RU(x['day']) + ' — сделок ' + str(x['trades']) + ', в сумме ' + pct(x['sum_pct'], 0) + ' %' for x in nl[0]['top_test_days']['days'])}; "
            f"сумма всех сделок 2025–26 — {pct(nl[0]['top_test_days']['test_sum_pct'], 0)} %; счёт на 5 "
            f"позиций без 3 лучших дней {pct(pf.get('test_ex3_pct'), 1)} %), а 2026 год {'в минусе' if (ys.get(2026) or 0) <= 0 else 'в плюсе'} "
            f"({pct(ys.get(2026), 1)} %). Похоже, это не «манипуляция ночью», а отскок после паники"
            + (" — фильтры лаборатории он не проходит." if not passes(st) else " — фильтры лаборатории он проходит, но на малом числе сделок.")
            + (" Продавать ночной/выходной рост (шорт) не работает." if not any(passes(r["stats"]) for r in results if r["id"] == "manip_night_short") else ""))
    # exits
    st_l = league.get("sweep_type") or {}

    def win(en):
        w = [r["stats"]["test"]["win"] for r in sw if r["exit"] == en]
        return float(np.mean(w)) if w else None
    w1, w2, ws = win("1tp_1r"), win("1tp_2r"), win("struct_2r")
    win_txt = ""
    if None not in (w1, w2, ws):
        win_txt = (f"Почему не работает ни один выход: при цели, равной стопу (1R), цель берётся в {w1:.0%} сделок; при цели вдвое "
                   f"дальше стопа (2R) — в {w2:.0%}, а со стопом сразу за тенью выноса (классика «охоты за стопами») — в {ws:.0%}. "
                   "Ровно столько дала бы монетка (50 % и 33 % — это ноль до комиссий), поэтому любая схема целей теряет примерно "
                   "комиссию. Стоп за тенью выноса не даёт преимущества: цена возвращается за тень так же часто, как уходит к цели.")
    if st_l:
        rank = sorted(st_l.items(), key=lambda kv: -(kv[1].get("train_mean") or -9))
        best = rank[0]
        sr = [(en, v) for en, v in st_l.items() if en.startswith("struct")]
        out.append(
            "Выходы (для выносов уровней, среднее по всем вариантам): лучше всего — «" + EXIT_SHORT_RU[best[0]] + f"» (обучение "
            f"{pct(best[1]['train_mean'], 3)} %, 2025–26 {pct(best[1]['test_mean'], 3)} % на сделку)"
            + (", но и он в среднем не прибыльный; " if (best[1]["test_mean"] or 0) <= 0 else "; ")
            + "; ".join(f"«{EXIT_SHORT_RU[en]}» — {pct(v['train_mean'], 3)} % / {pct(v['test_mean'], 3)} %" for en, v in rank[1:])
            + ". " + win_txt)
    cn = league.get("crowd_night") or {}
    if cn:
        b2 = max(cn.items(), key=lambda kv: (kv[1].get("train_mean") or -9))
        out.append(f"Для идей 4 и 7 лучший выход — «{EXIT_SHORT_RU[b2[0]]}» ({pct(b2[1]['train_mean'], 3)} % / "
                   f"{pct(b2[1]['test_mean'], 3)} %): там сигналы редкие, а движение после них большое — узкие цели его обрезают.")
    out.append("Подробно по лучшему варианту каждой идеи (на сделку: обучение → 2025–26; счёт на 5 позиций: 2025 / 2026 / 2025–26 без 3 "
               "лучших дней): " + " | ".join(
                   f"«{r['name_ru']}» {pct(r['stats']['train'].get('avg_pct'))} → {pct(r['stats']['test'].get('avg_pct'))} % "
                   f"(сделок {r['stats']['test'].get('n', 0)}); {pct(((r['stats'].get('pf5') or {}).get('years') or {}).get(2025), 1)} / "
                   f"{pct(((r['stats'].get('pf5') or {}).get('years') or {}).get(2026), 1)} / "
                   f"{pct((r['stats'].get('pf5') or {}).get('test_ex3_pct'), 1)} %" for r in results) + ".")
    out.append("Оговорки: у идеи 4 (перегруз позиций) за 2023–2026 всего несколько десятков сигналов — вывод по ней ненадёжный; "
               "сделки по разным монетам в один момент сильно связаны (весь рынок двигается вместе), поэтому реальная надёжность ниже, "
               "чем кажется по числу сделок; данные 15-минутные — внутри свечи порядок «стоп или цель» неизвестен, считаем стоп первым.")
    return " ".join(out)


if __name__ == "__main__":
    main()
