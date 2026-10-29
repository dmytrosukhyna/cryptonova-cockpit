"""Backlog #51 (29.10.2026): the «inverted D» — LONG the breakdown that rule D of #50 shorted (−4.24 %/trade).

Found by flipping a loser, so it is held to a stricter bar. Same archive, costs and funding as scripts/shorts50_study.py
(data.binance.vision daily klines + funding, 2020-01 → 2026-09, every USDT-M perpetual, delisted included; 0.05 % taker
+ 0.02 % slippage per side, +0.15 % when 20-day turnover < $5M; real funding — a long PAYS positive funding).
Rule, fixed BEFORE the run (one configuration, no tuning):
  signal — close below the previous 20-day low and below the 20-day average, signal-day dollar volume ≥ 1.5 × its 30-day
           average, BTC NOT in «рост» (BTC above its 200-day average and above its close 60 days ago);
  entry  — long at the next open; exit at the next open after a close above the 5-day average, or after 10 days;
  stop   — 2.5 ATR(14) below the entry, clipped to 3 %…15 %; a stop hit on a daily low fills at the stop (at the open if the
           day gaps through it). Size = 0.5 % of equity lost at the stop, notional ≤ 25 %, gross ≤ 200 %.
Honesty: placebo = the same coins on random days with BTC not «рост», same exits, 20 draws. Variants: 1, but found by
inversion → luck hurdle N = 524 and t by date ≥ 2.5 (stricter than 2.24 of #50).
PASS (fixed now): mean trade > placebo p95, t by date ≥ 2.5, both halves > 0, trade mean > 0 in 2022 and in 2025–26,
account max drawdown ≤ 25 %. Reported, not a gate: mix 50/50 with long rule C («рост» only) vs C alone; liquid ≥ $20M.
Public data only, no keys, no orders.
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402
import shorts50_study as s50  # noqa: E402

N_LUCK = 524
T_MIN = 2.5
HOLD = 10


def sig(x, i, bull):
    c, q = x["c"], x["q"]
    return (not bull) and c[i] < x["ll20"][i] and c[i] < x["s20"][i] and q[i] >= 1.5 * x["q30"][i]


def trade(x, i):
    """Long at the open of i+1 → (days, daily net returns, weight) or None."""
    o, l, c, f, d, atr = x["o"], x["l"], x["c"], x["f"], x["d"], x["atr"]
    n = len(c)
    if i + 1 >= n or not x["cont"][i + 1]:
        return None
    e = o[i + 1]
    dist = min(max(2.5 * atr[i] / e, 0.03), 0.15)
    stop = e * (1 - dist)
    w = min(0.25, 0.005 / dist)
    cost = vb.FEE + vb.SLIP + (vb.SLIP_SMALL if x["q20"][i] < vb.SMALL_Q else 0.0)
    days, rets = [], []
    prev = e
    j = i + 1
    while j < n:
        if j > i + 1 and not x["cont"][j]:
            break
        if l[j] <= stop:
            px = min(o[j], stop) if j > i + 1 else stop
            days.append(int(d[j])); rets.append(px / prev - 1 - f[j])
            break
        days.append(int(d[j])); rets.append(c[j] / prev - 1 - f[j])
        prev = c[j]
        k = j - i
        if k >= HOLD or c[j] > x["s5"][j]:
            if j + 1 < n and x["cont"][j + 1]:
                days.append(int(d[j + 1])); rets.append(o[j + 1] / prev - 1)
            break
        j += 1
    if not rets:
        return None
    rets[0] -= cost
    rets[-1] -= cost
    return days, rets, w


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [s50.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("d51 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    bc = btc["c"]
    bull = {}
    for k in range(200, len(bc)):
        bull[int(btc["d"][k])] = bool(bc[k] > bc[k - 199:k + 1].mean() and bc[k] > bc[k - 60])
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    mid = (d0 + d1) // 2
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(bc)):
        bpr[int(btc["d"][j]) - d0] = bc[j] / bc[j - 1] - 1
    bb = vb.stats(span, bpr)
    vb.note("d51 · BTC", f"Sharpe {bb['sharpe']} (halves {bb['halves']}) {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%")

    ctr = []
    for x in data:
        last = -1
        for i in range(60, len(x["c"]) - 1):
            if i <= last or not f36.ok_day(x, i):
                continue
            if not f36.sig(x, i, "C", bull.get(int(x["d"][i]), False)):
                continue
            p = f36.trade(x, i, "C")
            if p:
                ctr.append(p)
                last = i + len(p[0])
    _, cpr = f36.wport(ctr, d0, d1, 2.0)
    cst = vb.stats(span, cpr)

    tr, dates, meta, elig = [], [], [], []
    for x in data:
        last = -1
        ek = []
        for i in range(60, len(x["c"]) - 1):
            if not f36.ok_day(x, i):
                continue
            b = bull.get(int(x["d"][i]))
            if b is None:
                continue
            if not b:
                ek.append(i)
            if i <= last or not sig(x, i, b):
                continue
            p = trade(x, i)
            if p:
                tr.append(p)
                dates.append(int(x["d"][i]))
                meta.append((x["sym"], x["q20"][i]))
                last = i + len(p[0])
        elig.append(ek)
    if len(tr) < 10:
        vb.note("d51 · D-long", f"too few trades ({len(tr)})")
        return 0
    sp, pr = f36.wport(tr, d0, d1, 2.0)
    st = vb.stats(sp, pr)
    pt = vb.per_trade([(a, b) for a, b, _ in tr], dates)
    h1 = [t for t, dd in zip(tr, dates) if dd < mid]
    h2 = [t for t, dd in zip(tr, dates) if dd >= mid]
    by_year = {}
    for t, dd in zip(tr, dates):
        by_year.setdefault(s50.year_of(dd), []).append(sum(t[1]))
    yrs = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())}
    y22 = np.mean(by_year.get(2022, [0])) * 100
    y2526 = by_year.get(2025, []) + by_year.get(2026, [])
    y2526m = np.mean(y2526) * 100 if y2526 else 0.0
    liq = [t for t, m in zip(tr, meta) if m[1] >= vb.LIQ_Q]
    surv = [t for t, m in zip(tr, meta) if m[0] in s50.SURV]
    stops = sum(1 for t in tr if sum(t[1]) < -0.025) / len(tr) * 100
    cnt = {}
    for m in meta:
        cnt[m[0]] = cnt.get(m[0], 0) + 1
    rng = np.random.default_rng(51)
    plac = []
    for rep in range(20):
        ptr = []
        for k, x in enumerate(data):
            nk = cnt.get(x["sym"], 0)
            if not nk or not elig[k]:
                continue
            for j in rng.choice(elig[k], size=nk, replace=True):
                p = trade(x, int(j))
                if p:
                    ptr.append(sum(p[1]))
        plac.append(np.mean(ptr) * 100 if ptr else 0.0)
    p95 = float(np.percentile(plac, 95))
    m1, m2 = f36.tmean(h1), f36.tmean(h2)
    mst = vb.stats(span, 0.5 * cpr + 0.5 * pr)
    ok = (pt["mean_pct"] > p95 and pt["t_by_date"] >= T_MIN and (m1 or 0) > 0 and (m2 or 0) > 0 and y22 > 0
          and y2526m > 0 and st["dd_pct"] >= -25)
    vb.note("d51 · D-long", f"{'PASS' if ok else 'no'} — trades {pt['n']} on {pt['dates']} days, mean {pt['mean_pct']}% "
            f"(median {pt['median_pct']}%, win {pt['win']}%, t by date {pt['t_by_date']} need {T_MIN}), halves "
            f"{m1}% / {m2}%; placebo {round(float(np.mean(plac)), 2)}% p95 {round(p95, 2)}%; 2022 {y22:+.2f}%, "
            f"2025–26 {y2526m:+.2f}%; losers worse than −2.5% {stops:.0f}%; liquid ≥$20M {f36.tmean(liq)}% on {len(liq)}; "
            f"5 large {f36.tmean(surv)}% on {len(surv)}")
    vb.note("d51 · account", f"Sharpe {st['sharpe']} (halves {st['halves']}), {st['cagr_pct']}%/yr, DD {st['dd_pct']}%, "
            f"hurdle {st['hurdle']}, years {st['years']}; mix 50/50 with long C: Sharpe {mst['sharpe']} vs C "
            f"{cst['sharpe']}, DD {mst['dd_pct']}% vs {cst['dd_pct']}%; trade mean by year {yrs}")
    vb.note("d51 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
