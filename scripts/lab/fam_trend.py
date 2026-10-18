#!/usr/bin/env python3
"""Strategy lab — family "trend": breakouts, pullbacks, squeezes, BTC lead, daily ranges.

Ideas (long and short unless noted), 18 coins x 15m bars, harness.py for entries/exits/stats:
  1. donch    — Donchian breakout: close above the highest high (below the lowest low) of the previous N bars,
                N = 96 / 288 / 480 (1 / 3 / 5 days), optional volume filter (quote volume > 2x its 20-bar or 96-bar mean).
  2. pullback — 1h trend (EMA50 > EMA200 on 1h = EMA200 > EMA800 on 15m), price touched the 1h EMA20 / EMA50
                (EMA80 / EMA200 on 15m) within the last W bars and now resumes (green bar closing above the level and
                above the previous high / the 4-bar high). Mirror for shorts.
  3. squeeze  — Bollinger width (20, 2 sigma) in its lowest q of the last 480 bars (5 days) during the previous R bars,
                then the first close outside the band.
  4. btclead  — BTC 1h (4-bar) move >= thr, the alt moved less than f x BTC's move -> trade the alt in BTC's direction
                (f = None is the control without the "alt lags" condition).
  5. daily    — daily open-range breakout (first 4 h / 8 h of the UTC day) and previous-day high/low breakout,
                first breakout of the day only.
  6. regime   — idea 1 (N = 3 / 5 days, volume > 2x daily mean) with a market filter: BTC's direction (1h EMA50 vs
                EMA200, or BTC vs its ~50-day EMA) or breadth (>= 4 / 8 / 12 coins broke out in the last 24 h).
                NB: added after the first pass showed idea 1 longs losing in 2022 (parameters still chosen on TRAIN).
Exits: harness grid_exits(max_bars) + "wide_trail" (4 ATR stop, 30 % at 1R, then break-even + 4 ATR trail) and
"wide_time" (8 ATR disaster stop, time exit). Controls (every coin every day at 00:00 UTC, same exits) show how much of a
result is plain market drift; they are not strategies and not counted in "tried".

No look-ahead: every indicator at bar i uses bars <= i (rolling windows of the previous bars are shifted by one);
1h-equivalent EMAs are 4x periods on 15m; the harness enters at the next bar's open.
Choice of parameters AND exit: TRAIN 2021-2024 only (best train avg with train n >= 200, preferring >= 3 of 4 positive
train years). TEST 2025-01 -> 2026-09 and per-year numbers are only read afterwards.

Run: python3 fam_trend.py   (about 5-10 min)  ->  results/trend.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import D, entries_from_mask, run, stats, grid_exits, report, passes  # noqa: E402

T0 = time.time()
C, H, L, O, V = D.C, D.H, D.L, D.O, D.V
NS, NT = D.NS, D.T
SYMS = D.syms
FIRST = np.array([int(np.argmax(np.isfinite(C[k]))) for k in range(NS)])
AGE = np.arange(NT)[None, :] - FIRST[:, None]          # bars since the coin's first bar (warm-up masks)
TRAIN_YEARS = (2021, 2022, 2023, 2024)
NMIN_TRAIN = 200


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


# ---------------------------------------------------------------- vectorised, causal indicators
def roll(x, n, fn="max"):
    """Rolling over the last n bars INCLUDING bar i (NaN until the window is full)."""
    r = pd.DataFrame(x.T).rolling(n, min_periods=n)
    out = r.std(ddof=0) if fn == "std" else getattr(r, fn)()
    return out.values.T


def shift(x, k=1):
    """Value of bar i-k at bar i (so a rolling window of the PREVIOUS bars)."""
    if x.dtype == bool:
        out = np.zeros(x.shape, bool)
    else:
        out = np.full(x.shape, np.nan)
    out[:, k:] = x[:, :-k]
    return out


def ema(x, n):
    """Same recursion as harness D.ema (starts at the first finite value), but vectorised."""
    return pd.DataFrame(x.T).ewm(span=n, adjust=False).mean().values.T


def any_prev(mask, n):
    """True if mask was True in any of the previous n bars (i-n .. i-1)."""
    return shift(roll(mask.astype(float), n, "max") > 0)


def any_last(mask, n):
    """True if mask was True in any of the last n bars including bar i (i-n+1 .. i)."""
    return roll(mask.astype(float), n, "max") > 0


# ---------------------------------------------------------------- exits
def exits(mb):
    """Harness standard grid + two wide (trend-style) exits: wider stop, trailing after a small partial."""
    ex = dict(grid_exits(mb))
    ex["wide_trail"] = dict(sl_atr=4.0, tps=[(1.0, 0.3)], be_after_tp1=True, trail_atr=4.0, max_bars=mb * 2)
    ex["wide_time"] = dict(sl_atr=8.0, tps=[], max_bars=mb)
    return ex


def exit_ru(name, mb):
    h = lambda b: f"~{b / 4:g} ч ({b} × 15 мин)" if b < 96 else f"~{b / 96:g} сут ({b} × 15 мин)"  # noqa: E731
    return {
        "1tp_1r": f"стоп 2 ATR(15м); одна цель 1R (= 2 ATR); иначе выход по времени через {h(mb)}",
        "1tp_2r": f"стоп 2 ATR(15м); одна цель 2R (= 4 ATR); иначе выход по времени через {h(mb)}",
        "3tp_be": f"стоп 2 ATR(15м); 3 цели 1R/2R/3R по 1/3, после первой цели стоп в безубыток; максимум {h(mb)}",
        "5tp_be": f"стоп 2 ATR(15м); 5 целей 0,5–2,5R по 20 %, после первой цели стоп в безубыток; максимум {h(mb)}",
        "tp1_trail": f"стоп 2 ATR(15м); половина позиции на 1R, затем стоп в безубыток и трейлинг 2 ATR от лучшего "
                     f"закрытия; максимум {h(mb * 2)}",
        "time": f"стоп 4 ATR(15м), без целей; выход по времени через {h(mb)}",
        "wide_trail": f"стоп 4 ATR(15м); 30 % позиции на 1R (= 4 ATR), затем стоп в безубыток и трейлинг 4 ATR от "
                      f"лучшего закрытия; максимум {h(mb * 2)}",
        "wide_time": f"аварийный стоп 8 ATR(15м), без целей; выход по времени через {h(mb)}",
    }[name]


# ---------------------------------------------------------------- evaluation bookkeeping
TRIED = 0
REC: list[dict] = []          # one record per evaluated variant (signal params x side x exit)
ENTRIES: dict = {}            # (idea, side, params-json) -> entries list (to re-run the chosen ones)


def pkey(idea, side, params):
    return (idea, side, json.dumps(params, sort_keys=True))


def evaluate(idea, side, params, mask, gap, mb):
    global TRIED
    E = entries_from_mask(mask, side, gap)
    ENTRIES[pkey(idea, side, params)] = (E, gap, mb)
    for en, ex in exits(mb).items():
        st = stats(run(E, ex))
        TRIED += 1
        REC.append(dict(idea=idea, side=side, params=params, exit=en, ex=ex, gap=gap, mb=mb, stats=st))
    best = max((r for r in REC[-len(exits(mb)):]), key=lambda r: (r["stats"].get("train") or {}).get("avg_pct", -9))
    tr, te = best["stats"].get("train", {}), best["stats"].get("test", {})
    log(f"{idea:8s} {side:+d} {json.dumps(params):42s} n_sig={len(E):6d}  best-train exit {best['exit']:10s} "
        f"train {tr.get('avg_pct')} (n {tr.get('n')})  test {te.get('avg_pct')} (n {te.get('n')})")


def train_pos_years(st):
    ys = st.get("years", {})
    return sum(1 for y in TRAIN_YEARS if ys.get(y, {}).get("n", 0) >= 10 and ys[y]["avg_pct"] > 0)


def choose(idea, side):
    """Best variant of an idea/side by TRAIN only."""
    cand = [r for r in REC if r["idea"] == idea and r["side"] == side
            and (r["stats"].get("train") or {}).get("n", 0) >= NMIN_TRAIN]
    if not cand:
        return None
    good = [r for r in cand if train_pos_years(r["stats"]) >= 3]
    pool = good or cand
    return max(pool, key=lambda r: r["stats"]["train"]["avg_pct"])


# ================================================================= 1. Donchian breakout
def idea_donchian():
    v20 = shift(roll(V, 20, "mean"))
    v96 = shift(roll(V, 96, "mean"))
    volf = {"none": np.ones(C.shape, bool), "v20x2": V > 2 * v20, "v96x2": V > 2 * v96}
    for N in (96, 288, 480):
        hi = shift(roll(H, N, "max"))     # highest high of bars i-N .. i-1
        lo = shift(roll(L, N, "min"))
        up, dn = C > hi, C < lo
        for vn, vf in volf.items():
            p = dict(N=N, vol=vn)
            evaluate("donch", +1, p, up & vf, gap=96, mb=192)
            evaluate("donch", -1, p, dn & vf, gap=96, mb=192)


# ================================================================= 2. Trend pullback
def idea_pullback():
    e200, e800 = ema(C, 200), ema(C, 800)          # = EMA50 / EMA200 of 1h, updated every 15m bar
    warm = AGE >= 1600
    tup, tdn = (e200 > e800) & warm, (e200 < e800) & warm
    green, red = C > O, C < O
    trig = {"prevhigh": (C > shift(H), C < shift(L)),
            "hh4": (C > shift(roll(H, 4, "max")), C < shift(roll(L, 4, "min")))}
    for P in (80, 200):                             # 1h EMA20 / EMA50
        lev = e200 if P == 200 else ema(C, P)
        tl, ts = L <= lev, H >= lev
        for W in (4, 12):
            tlw, tsw = any_last(tl, W), any_last(ts, W)
            for tn, (rl, rs) in trig.items():
                p = dict(level_1h=f"EMA{P // 4}", W=W, trigger=tn)
                evaluate("pullback", +1, p, tup & tlw & (C > lev) & rl & green, gap=32, mb=96)
                evaluate("pullback", -1, p, tdn & tsw & (C < lev) & rs & red, gap=32, mb=96)


# ================================================================= 3. Volatility squeeze breakout
def idea_squeeze():
    mid, sd = roll(C, 20, "mean"), roll(C, 20, "std")
    up_b, lo_b = mid + 2 * sd, mid - 2 * sd
    bbw = 4 * sd / mid
    rank = pd.DataFrame(bbw.T).rolling(480, min_periods=480).rank(pct=True, method="max").values.T
    brk_up = (C > up_b) & (shift(C) <= shift(up_b))       # first close above the upper band
    brk_dn = (C < lo_b) & (shift(C) >= shift(lo_b))
    for q in (0.10, 0.20):
        sq = rank <= q
        for R in (4, 16):
            rec = any_prev(sq, R)
            p = dict(q=q, R=R)
            evaluate("squeeze", +1, p, rec & brk_up, gap=48, mb=96)
            evaluate("squeeze", -1, p, rec & brk_dn, gap=48, mb=96)


# ================================================================= 4. BTC lead / alt catch-up
def idea_btclead():
    b = SYMS.index("BTCUSDT")
    r4 = C / shift(C, 4) - 1                       # rolling 1h return, known at the close of bar i
    br = r4[b][None, :]
    alt = np.ones(C.shape, bool)
    alt[b] = False
    for thr in (0.01, 0.015, 0.02):
        for f in (0.25, 0.5, None):                # None = control: no "alt lags" condition at all
            p = dict(btc_1h=thr, alt_lt_frac=f)
            lag_up = (r4 <= f * br) if f is not None else True
            lag_dn = (r4 >= f * br) if f is not None else True
            up = alt & (br >= thr) & lag_up
            dn = alt & (br <= -thr) & lag_dn
            evaluate("btclead", +1, p, up, gap=16, mb=48)
            evaluate("btclead", -1, p, dn, gap=16, mb=48)


# ================================================================= 5. Daily open range / previous-day breakout
def idea_daily():
    day = (D.times // 86_400_000).astype(np.int64)
    day -= day[0]
    bod = ((D.times // 900_000) % 96).astype(int)
    nd = int(day[-1]) + 1
    pad = nd * 96 - NT

    def rs(x, fill):
        return np.concatenate([x, np.full((NS, pad), fill, dtype=x.dtype)], axis=1).reshape(NS, nd, 96)

    Hr, Lr = rs(H, np.nan), rs(L, np.nan)
    dh, dl = np.nanmax(Hr, axis=2), np.nanmin(Lr, axis=2)
    pdh = np.full_like(dh, np.nan)
    pdl = np.full_like(dl, np.nan)
    pdh[:, 1:], pdl[:, 1:] = dh[:, :-1], dl[:, :-1]
    levels = {}
    for m in (16, 32):
        orh, orl = np.nanmax(Hr[:, :, :m], axis=2), np.nanmin(Lr[:, :, :m], axis=2)
        after = (bod >= m)[None, :]
        levels[f"or{m // 4}h"] = (np.where(after, orh[:, day], np.nan), np.where(after, orl[:, day], np.nan))
    levels["prevday"] = (pdh[:, day], pdl[:, day])

    def first_of_day(cond):
        c = rs(cond, False)
        f = c & (np.cumsum(c, axis=2) == 1)
        return f.reshape(NS, nd * 96)[:, :NT]

    for name, (hi, lo) in levels.items():
        p = dict(range=name)
        evaluate("daily", +1, p, first_of_day(C > hi), gap=1, mb=96)
        evaluate("daily", -1, p, first_of_day(C < lo), gap=1, mb=96)


# ================================================================= 6. Donchian breakout only with the BTC regime
def idea_regime():
    """Idea 1 restricted by a market filter. vol = v96x2 fixed (best volume filter on TRAIN in idea 1).
    Filters: BTC trend (1h EMA50 vs EMA200, or BTC vs ~50-day EMA) or breadth — at least K coins (the coin itself
    included) closed beyond their own N-bar high (low) at some bar of the last 96 bars (24 h), counted at bar i."""
    b = SYMS.index("BTCUSDT")
    cb = C[b]
    e200, e800 = ema(cb[None, :], 200)[0], ema(cb[None, :], 800)[0]
    e4800 = ema(cb[None, :], 4800)[0]                       # ~ EMA50 of daily bars
    vf = V > 2 * shift(roll(V, 96, "mean"))
    for N in (288, 480):
        hi, lo = shift(roll(H, N, "max")), shift(roll(L, N, "min"))
        up, dn = C > hi, C < lo
        b_up = any_last(up, 96).sum(axis=0)                 # breadth: coins with a fresh breakout in the last 24 h
        b_dn = any_last(dn, 96).sum(axis=0)
        filters = {"btc_1h": (e200 > e800, e200 < e800), "btc_50d": (cb > e4800, cb < e4800)}
        for K in (4, 8, 12):
            filters[f"breadth{K}"] = (b_up >= K, b_dn >= K)
        for rn, (bull, bear) in filters.items():
            p = dict(N=N, vol="v96x2", regime=rn)
            evaluate("regime", +1, p, up & vf & bull[None, :], gap=96, mb=192)
            evaluate("regime", -1, p, dn & vf & bear[None, :], gap=96, mb=192)


def day_concentration(T):
    """How much of the result comes from a few days (market-wide breakout days)."""
    if not T:
        return {}
    r = np.array([t["ret"] for t in T])
    tt = np.array([D.times[t["e"]] for t in T])
    day = tt // 86_400_000
    from harness import TRAIN_START, TRAIN_END
    out = {}
    for lbl, m in (("train", (tt >= TRAIN_START) & (tt < TRAIN_END)), ("test", tt >= TRAIN_END)):
        rr, dd = r[m], day[m]
        if len(rr) < 10:
            continue
        ud, inv = np.unique(dd, return_inverse=True)
        dsum = np.bincount(inv, weights=rr)
        top = np.argsort(-dsum)
        o = {"entry_days": int(len(ud))}
        for k in (3, 10):
            keep = np.ones(len(ud), bool)
            keep[top[:k]] = False
            o[f"avg_pct_without_top{k}_days"] = round(float(rr[keep[inv]].mean() * 100), 3)
        out[lbl] = o
    return out


# ================================================================= controls (not strategies, not counted in "tried")
def controls():
    """Enter every coin every day at 00:00 UTC, same wide exits: how much of a result is just market drift (beta)."""
    bod = ((D.times // 900_000) % 96).astype(int)
    m = np.zeros(C.shape, bool)
    m[:, bod == 0] = True
    m &= np.isfinite(C)
    out = {}
    for side in (+1, -1):
        E = entries_from_mask(m, side, gap=1)
        for en in ("time", "wide_time"):
            st = stats(run(E, exits(192)[en]))
            out[f"{'long' if side > 0 else 'short'}_daily_{en}192"] = {
                "train": st["train"]["avg_pct"], "test": st["test"]["avg_pct"],
                "years": {y: v.get("avg_pct") for y, v in st["years"].items()}}
    return out


# ================================================================= texts for the panel
def side_ru(s):
    return "лонг" if s > 0 else "шорт"


def texts(r):
    p, s, ex = r["params"], r["side"], exit_ru(r["exit"], r["mb"])
    ex = ex[0].upper() + ex[1:]
    idea = r["idea"]
    if idea == "donch":
        days = {96: "1 дня", 288: "3 дней", 480: "5 дней"}[p["N"]]
        vol = {"none": "", "v20x2": " при объёме свечи > 2× среднего за 20 свечей",
               "v96x2": " при объёме свечи > 2× среднего за сутки"}[p["vol"]]
        name = f"Пробой канала {days} ({side_ru(s)})"
        lvl = "максимум" if s > 0 else "минимум"
        rule = (f"{side_ru(s).capitalize()}, когда 15-минутная свеча закрылась {'выше' if s > 0 else 'ниже'} "
                f"{lvl}а последних {days}{vol}; вход на открытии следующей свечи, не чаще раза в сутки по монете. {ex}.")
    elif idea == "pullback":
        name = f"Откат к {p['level_1h']} (1ч) по тренду ({side_ru(s)})"
        tr = "EMA50 выше EMA200" if s > 0 else "EMA50 ниже EMA200"
        trig = ("закрылась выше максимума предыдущей свечи" if s > 0 else "закрылась ниже минимума предыдущей свечи") \
            if p["trigger"] == "prevhigh" else \
            ("закрылась выше максимума 4 предыдущих свечей" if s > 0 else "закрылась ниже минимума 4 предыдущих свечей")
        rule = (f"{side_ru(s).capitalize()}: на часовом графике {tr}, цена за последние {p['W']} свечей 15м коснулась "
                f"{p['level_1h']} (1ч), а текущая {'зелёная' if s > 0 else 'красная'} свеча вернулась за линию и {trig}. "
                f"{ex}.")
    elif idea == "squeeze":
        name = f"Пробой после сжатия Боллинджера ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()}: ширина полос Боллинджера (20, 2σ) в последние {p['R']} свечей была в нижних "
                f"{int(p['q'] * 100)} % за 5 дней, и свеча впервые закрылась {'выше верхней' if s > 0 else 'ниже нижней'} "
                f"полосы. {ex}.")
    elif idea == "btclead":
        thr = p["btc_1h"] * 100
        lag = (f", а альт прошёл меньше {int(p['alt_lt_frac'] * 100)} % этого движения" if p["alt_lt_frac"] is not None
               else " (без условия, что альт отстал)")
        name = f"Альт догоняет BTC ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()} альткоина: BTC за последний час {'вырос' if s > 0 else 'упал'} на ≥ {thr:g} %"
                f"{lag} — входим в сторону BTC. {ex}.")
    elif idea == "regime":
        days = {288: "3 дней", 480: "5 дней"}[p["N"]]
        reg = {"btc_1h": ("на часовом графике BTC EMA50 выше EMA200" if s > 0 else
                          "на часовом графике BTC EMA50 ниже EMA200"),
               "btc_50d": ("BTC выше своей 50-дневной EMA" if s > 0 else "BTC ниже своей 50-дневной EMA")}.get(p["regime"])
        if reg is None:
            K = int(p["regime"].replace("breadth", ""))
            reg = (f"за последние сутки не меньше {K} монет из 18 тоже пробили свой "
                   f"{ {288: '3-дневный', 480: '5-дневный'}[p['N']] } "
                   f"{'максимум' if s > 0 else 'минимум'} (пробой всего рынка)")
            name = f"Пробой канала {days} вместе с рынком ({side_ru(s)})"
        else:
            name = f"Пробой канала {days} по тренду BTC ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()}, когда свеча 15м закрылась {'выше максимума' if s > 0 else 'ниже минимума'} "
                f"последних {days} при объёме > 2× среднего за сутки и {reg}; не чаще раза в сутки по монете. {ex}.")
    else:
        hl = "максимума" if s > 0 else "минимума"
        nm = {"or4h": f"{hl} первых 4 часов дня (UTC)", "or8h": f"{hl} первых 8 часов дня (UTC)",
              "prevday": f"вчерашнего {hl}"}[p["range"]]
        name = f"Пробой {nm} ({side_ru(s)})"
        rule = (f"{side_ru(s).capitalize()}: первая за сутки (UTC) 15-минутная свеча, закрывшаяся "
                f"{'выше' if s > 0 else 'ниже'} {nm}. {ex}.")
    return name, rule


# ================================================================= main
def main():
    global TRIED
    for fn in (idea_donchian, idea_pullback, idea_squeeze, idea_btclead, idea_daily, idea_regime):
        log("=== " + fn.__name__)
        fn()
    log(f"grid done, tried = {TRIED}")
    ctrl = controls()
    print("CONTROLS (every coin, every day 00:00 UTC):")
    for k, v in ctrl.items():
        print(f"  {k:28s} {v}")

    # ---- choose best per idea/side on TRAIN, then read TEST
    chosen = []
    for idea in ("donch", "pullback", "squeeze", "btclead", "daily", "regime"):
        for side in (+1, -1):
            r = choose(idea, side)
            if r is not None:
                chosen.append(r)

    # ---- sensitivity of the chosen ones to max hold (x0.5, x2), same exit type — reported, not used to choose
    sens, conc = {}, {}
    for r in chosen:
        E, gap, mb = ENTRIES[pkey(r["idea"], r["side"], r["params"])]
        conc[(r["idea"], r["side"])] = day_concentration(run(E, r["ex"]))   # same variant again, not a new one
        out = {}
        for mult in (0.5, 2):
            mb2 = int(mb * mult)
            st = stats(run(E, exits(mb2)[r["exit"]]))
            TRIED += 1
            out[f"mb{mb2}"] = {"train": st["train"].get("avg_pct"), "test": st["test"].get("avg_pct"),
                               "pos_years": st["pos_years"]}
        sens[(r["idea"], r["side"])] = out

    # ---- exit league table across every signal variant (TRAIN and TEST mean of avg_pct)
    exit_names = list(exits(96).keys())
    league = {}
    for en in exit_names:
        rows = [r for r in REC if r["exit"] == en and (r["stats"].get("train") or {}).get("n", 0) >= 50]
        tr = [r["stats"]["train"]["avg_pct"] for r in rows]
        te = [r["stats"]["test"]["avg_pct"] for r in rows if (r["stats"].get("test") or {}).get("n", 0) >= 20]
        league[en] = {"variants": len(rows), "train_mean": round(float(np.mean(tr)), 3),
                      "test_mean": round(float(np.mean(te)), 3), "train_pos_share": round(float(np.mean(np.array(tr) > 0)), 2),
                      "test_pos_share": round(float(np.mean(np.array(te) > 0)), 2)}
    # how often each exit is the train-best exit for a given signal
    bestcount = {en: 0 for en in exit_names}
    sigs = {}
    for r in REC:
        sigs.setdefault(pkey(r["idea"], r["side"], r["params"]), []).append(r)
    for rs_ in sigs.values():
        ok = [r for r in rs_ if (r["stats"].get("train") or {}).get("n", 0) >= 50]
        if ok:
            bestcount[max(ok, key=lambda r: r["stats"]["train"]["avg_pct"])["exit"]] += 1
    for en in exit_names:
        league[en]["train_best_count"] = bestcount[en]

    # ---- print summary
    print("\nEXIT LEAGUE (mean avg_pct over all signal variants)")
    for en, v in league.items():
        print(f"  {en:10s} {v}")
    print("\nCHOSEN (train-selected) per idea/side")
    hdr = f"{'idea':9s}{'side':>5s} {'params':44s}{'exit':11s}{'trn avg':>8s}{'trn n':>7s}{'tst avg':>8s}{'tst n':>7s}" \
          f"{'pos':>4s}{'top':>6s}  pass  years"
    print(hdr)
    results = []
    for r in chosen:
        st = r["stats"]
        yrs = " ".join(f"{y}:{v.get('avg_pct')}" for y, v in st["years"].items())
        print(f"{r['idea']:9s}{r['side']:+5d} {json.dumps(r['params']):44s}{r['exit']:11s}{st['train']['avg_pct']:8.3f}"
              f"{st['train']['n']:7d}{(st['test'].get('avg_pct') or 0):8.3f}{st['test'].get('n', 0):7d}{st['pos_years']:4d}"
              f"{(st['top_coin_share'] or 0):6.2f}  {'YES' if passes(st) else 'no ':4s}  {yrs}")
        print(f"{'':15s}hold sensitivity: {sens[(r['idea'], r['side'])]}")
        print(f"{'':15s}day concentration: {conc[(r['idea'], r['side'])]}")
        name, rule = texts(r)
        params = dict(r["params"], side=r["side"], gap=r["gap"], max_bars=r["mb"])
        results.append({"id": f"trend_{r['idea']}_{'long' if r['side'] > 0 else 'short'}", "name_ru": name,
                        "rule_ru": rule, "exit": dict(r["ex"], name=r["exit"]), "params": params,
                        "stats": dict(st, hold_sensitivity=sens[(r["idea"], r["side"])],
                                      day_concentration=conc[(r["idea"], r["side"])])})

    # ---- top-10 by TRAIN across the whole family (to show how train winners behave on test)
    print("\nTOP-15 variants by TRAIN avg (train n >= 200)")
    top = sorted([r for r in REC if (r["stats"].get("train") or {}).get("n", 0) >= NMIN_TRAIN],
                 key=lambda r: -r["stats"]["train"]["avg_pct"])[:15]
    for r in top:
        st = r["stats"]
        print(f"  {r['idea']:9s}{r['side']:+3d} {json.dumps(r['params']):44s}{r['exit']:11s} train {st['train']['avg_pct']:7.3f} "
              f"(n {st['train']['n']:5d}, +yrs {train_pos_years(st)})  test {st['test'].get('avg_pct')} (n {st['test'].get('n')}) "
              f"pos {st['pos_years']} pass {passes(st)}")

    npass = sum(passes(r["stats"]) for r in results)
    passed = [r["name_ru"] for r in results if passes(r["stats"])]
    lt, wt = league["time"], league["wide_time"]
    tight = [league[e]["train_mean"] for e in ("1tp_1r", "1tp_2r", "3tp_be", "5tp_be", "tp1_trail")]
    tight_pos = [league[e]["train_pos_share"] for e in ("1tp_1r", "1tp_2r", "3tp_be", "5tp_be", "tp1_trail")]
    cl, cs = ctrl["long_daily_wide_time192"], ctrl["short_daily_wide_time192"]
    tops = "; ".join(f"«{r['name_ru']}» {r['stats']['day_concentration'].get('test', {}).get('avg_pct_without_top3_days')} %"
                     for r in results if passes(r["stats"]))
    notes_ru = (
        f"Семейство «тренд»: 6 идей — пробой канала 1/3/5 дней (с фильтром объёма и без), откат к EMA по часовому "
        f"тренду, пробой после сжатия Боллинджера, альт догоняет BTC, пробой дневного диапазона/вчерашнего дня, "
        f"пробой канала с фильтром рынка (тренд BTC или «пробой всего рынка»); лонг и шорт отдельно. Проверено "
        f"{TRIED} вариантов (сигнал × сторона × выход + проверка длины удержания). Параметры и выход выбирались только "
        f"на 2021–2024 (лучшее среднее на сделку при n ≥ {NMIN_TRAIN} и ≥ 3 из 4 плюсовых лет). Прошли критерий: "
        f"{npass} из {len(results)}{(': ' + '; '.join(passed)) if passed else ''}. "
        f"НО прошедшие варианты держатся на нескольких днях, когда рвануло почти всё сразу: без 3 лучших дней теста среднее "
        f"на сделку — {tops}. Фильтр «пробой всего рынка», посчитанный честно (по уже случившимся пробоям за сутки), "
        f"этот эффект не ловит. Идея 6 (фильтр рынка) добавлена после того, как увидели, что простой пробой вверх "
        f"теряет в 2022-м, — параметры выбраны на трейне, но сам выбор идеи уже не полностью «слепой». "
        f"Выходы: узкий стоп 2 ATR(15м) с целями 1–3R и трейлинг после первой цели почти никогда не дают плюса на "
        f"трейне (плюсовых вариантов {min(tight_pos):.0%}–{max(tight_pos):.0%}; среднее от {min(tight):+.2f} до "
        f"{max(tight):+.2f} % на сделку) — 15-минутный шум выбивает стоп раньше, чем развивается движение, а комиссия "
        f"0,14 % съедает маленькие цели. Лучше всего — выход по времени с широким стопом: стоп 4 ATR "
        f"(плюсовых вариантов на трейне {lt['train_pos_share']:.0%}) и аварийный стоп 8 ATR "
        f"({wt['train_pos_share']:.0%}, выбран лучшим в {wt['train_best_count']} сигналах из {sum(v['train_best_count'] for v in league.values())}). "
        f"Важно: большая часть плюса лонгов — просто рост рынка. Контроль «покупать каждую монету каждый день в 00:00 "
        f"UTC, стоп 8 ATR, выход через 2 суток» даёт трейн {cl['train']:+.2f} %, тест {cl['test']:+.2f} %; шорт тем же "
        f"способом — трейн {cs['train']:+.2f} %, тест {cs['test']:+.2f} %. Пробои вверх лучше этого контроля (импульс "
        f"есть), но в 2022-м всё равно теряют; пробои вниз, сжатия и шорты после падения BTC хуже случайного шорта — на "
        f"15м после резкого пробоя вниз цена чаще откатывает (материал для семейства fade). У «альт догоняет BTC» "
        f"условие «альт отстал» ничего не добавляет: просто лонг всех альтов после часа роста BTC ≥ 2 % даёт похожий "
        f"результат на большем числе сделок. Сделки по одной монете могут перекрываться (харнесс считает каждую "
        f"отдельно), так что сумма процентов — не доходность счёта.")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    report(os.path.join(HERE, "results", "trend.json"), "trend", results, TRIED, notes_ru)

    # full variant table for the analyst (outside the lab folder)
    dump = os.environ.get("TREND_DUMP")
    if dump:
        with open(dump, "w") as f:
            json.dump({"league": league, "rec": [{k: v for k, v in r.items() if k != "ex"} for r in REC]}, f,
                      default=float)
    log(f"done, tried = {TRIED}, passes = {npass}/{len(results)}")


if __name__ == "__main__":
    main()
