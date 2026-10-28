"""Backlog #36 (28.10.2026): do the live desks' own rules hold on EVERY Binance USDT-M perpetual, delisted included?

Same archive and costs as scripts/voltbreak_all_study.py (data.binance.vision daily klines + funding, 2020-01 → 2026-09,
0.05 % taker + 0.02 % slippage per side, +0.15 % when 20-day turnover < $5M, real funding, coin tradable after 30 days and with
20-day turnover ≥ $1M). Three rules, copied from the live code and fixed BEFORE the run (no tuning, long only):

  M — «Решения» trend rule (cnq/terminal/report.py): close above the previous 20-day high and 28-day return > 0 → buy at the
      next open, hold 5 days (exit at the close of day 5), stop 2 ATR(14) from the entry. Size 10 % of equity, gross ≤ 100 %.
  P — own desk pullback (cnq/agents/own_desk.py): close above a rising 50-day average (vs 5 days ago), RSI(2) < 10, BTC «рост»
      (BTC above its 200-day average and above its close 60 days ago), signal-day dollar volume ≥ 1.5 × its 30-day average →
      buy at the next open; exit at the next open after a close above the 5-day average or after 10 days.
  C — own desk breakout: close above the previous 20-day high and above the 20-day average, BTC «рост», volume ≥ 1.5 × →
      buy at the next open; exit at the next open after a close 3 ATR below the best close, or after 14 days.
  P and C: stop 2.5 ATR(14) from the entry, at least 3 % and at most 15 %; size = 0.5 % of equity lost at the stop, notional ≤ 25 %,
  gross ≤ 200 % (as the live desk). A stop hit on a daily low fills at the stop (at the open if the day gaps through it).

Honesty: placebo = the same coins on random days (same regime filter where the rule has one), the same exits, 20 draws.
Variants: 3 (one per rule); luck hurdle N = 513 + 3 = 516. Also shown, not counted as variants: the 5 large «survivors»
(BTC ETH BNB SOL XRP) and liquid coins (20-day turnover ≥ $20M). Pass (fixed now): mean trade above the placebo p95, t by date
≥ 2, mean trade > 0 in both halves of the period. Public data only, no keys, no orders.
"""
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402

DAY = vb.DAY
SURV = set(vb.SURV)
N_LUCK = 513 + 3


def load(sym):
    """Like vb.load, plus highs and lows and the base volume."""
    kl = {}
    for m in [m for m in vb.s3_months(f"data/futures/um/monthly/klines/{sym}/1d/") if vb.M0 <= m <= vb.M1]:
        s, b = vb.get(f"{vb.VISION}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
        if s == 200:
            for r in vb.zrows(b):
                t = int(r[0])
                t = t // 1000 if t > 10 ** 14 else t
                kl[t // DAY] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
    if len(kl) < 70:
        return None
    fd = {}
    for m in [m for m in vb.s3_months(f"data/futures/um/monthly/fundingRate/{sym}/") if vb.M0 <= m <= vb.M1]:
        s, b = vb.get(f"{vb.VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
        if s == 200:
            for r in vb.zrows(b):
                d = int(r[0]) // DAY
                fd[d] = fd.get(d, 0.0) + float(r[2])
    days = np.array(sorted(kl), dtype=np.int64)
    v = np.array([kl[d] for d in days])
    return {"sym": sym, "d": days, "o": v[:, 0], "h": v[:, 1], "l": v[:, 2], "c": v[:, 3], "q": v[:, 4],
            "f": np.array([fd.get(int(d), 0.0) for d in days])}


def prep(x):
    c, h, l, q = x["c"], x["h"], x["l"], x["q"]
    n = len(c)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    x["atr"] = np.r_[np.full(14, np.nan), np.convolve(tr, np.ones(14) / 14, "valid")[1:]] if n > 14 else np.full(n, np.nan)
    def sma(a, k):
        out = np.full(n, np.nan)
        if n >= k:
            out[k - 1:] = np.convolve(a, np.ones(k) / k, "valid")
        return out
    x["s5"], x["s20"], x["s50"] = sma(c, 5), sma(c, 20), sma(c, 50)
    x["q20"] = np.r_[np.full(1, np.nan), sma(q, 20)[:-1]]          # previous 20 days
    x["q30"] = sma(q, 30)                                          # signal day included (as vol_surge)
    hh = np.full(n, np.nan)
    for i in range(20, n):
        hh[i] = h[i - 20:i].max()
    x["hh20"] = hh
    x["cont"] = np.r_[True, np.diff(x["d"]) == 1]
    return x


def rsi2(c, i):
    d = (c[i - 1] - c[i - 2], c[i] - c[i - 1])
    up = sum(v for v in d if v > 0) / 2
    dn = sum(-v for v in d if v < 0) / 2
    return 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def ok_day(x, i):
    return i >= 60 and x["cont"][i - 59:i + 2].all() and x["q20"][i] >= vb.MIN_Q and not np.isnan(x["atr"][i])


def sig(x, i, rule, bull):
    c = x["c"]
    if rule == "M":
        return c[i] > x["hh20"][i] and c[i] / c[i - 28] - 1 > 0
    if not bull:
        return False
    if x["q"][i] < 1.5 * x["q30"][i]:
        return False
    if rule == "P":
        return c[i] > x["s50"][i] and x["s50"][i] > x["s50"][i - 5] and rsi2(c, i) < 10
    return c[i] > x["hh20"][i] and c[i] > x["s20"][i]


def trade(x, i, rule):
    """Enter at the open of i+1. → (days, daily net returns, weight) or None."""
    o, h, l, c, f, d, atr = x["o"], x["h"], x["l"], x["c"], x["f"], x["d"], x["atr"]
    n = len(c)
    if i + 1 >= n or not x["cont"][i + 1]:
        return None
    e = o[i + 1]
    a = atr[i]
    if rule == "M":
        stop = e - 2 * a
        w = vb.SLOT
    else:
        dist = min(max(2.5 * a / e, 0.03), 0.15)
        stop = e * (1 - dist)
        w = min(0.25, 0.005 / dist)
    cost = vb.FEE + vb.SLIP + (vb.SLIP_SMALL if x["q20"][i] < vb.SMALL_Q else 0.0)
    hold = {"M": 5, "P": 10, "C": 14}[rule]
    days, rets = [], []
    best = e
    prev = e
    j = i + 1
    while j < n:
        if j > i + 1 and not x["cont"][j]:
            break
        if l[j] <= stop:                                  # stop on the day's low
            px = min(o[j], stop) if j > i + 1 else stop
            days.append(int(d[j])); rets.append(px / prev - 1 - f[j])
            break
        days.append(int(d[j])); rets.append(c[j] / prev - 1 - f[j])
        prev = c[j]
        best = max(best, c[j])
        k = j - i                                         # days held so far
        if rule == "M":
            if k >= hold:
                break
        else:
            out = k >= hold or (rule == "P" and c[j] > x["s5"][j]) or (rule == "C" and c[j] < best - 3 * atr[j])
            if out:                                       # exit at the next open
                if j + 1 < n and x["cont"][j + 1]:
                    days.append(int(d[j + 1])); rets.append(o[j + 1] / prev - 1)
                break
        j += 1
    if not rets:
        return None
    rets[0] -= cost
    rets[-1] -= cost
    return days, rets, w


def wport(trades, d0, d1, gross):
    span = np.arange(d0, d1 + 1)
    s = np.zeros(len(span))
    g = np.zeros(len(span))
    for days, rets, w in trades:
        for dd, rr in zip(days, rets):
            k = dd - d0
            if 0 <= k < len(span):
                s[k] += w * rr
                g[k] += w
    scale = np.where(g > gross, gross / np.maximum(g, 1e-12), 1.0)
    return span, s * scale


def tmean(tr):
    return round(float(np.mean([sum(t[1]) for t in tr])) * 100, 2) if tr else None


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [prep(x) for x in ex.map(load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("f36 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    bc = btc["c"]
    bull = {}
    for k in range(200, len(bc)):
        bull[int(btc["d"][k])] = bc[k] > bc[k - 199:k + 1].mean() and bc[k] > bc[k - 60]
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    mid = (d0 + d1) // 2
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(bc)):
        bpr[int(btc["d"][j]) - d0] = bc[j] / bc[j - 1] - 1
    bb = vb.stats(span, bpr)
    rng = np.random.default_rng(36)
    summary = {}
    for rule, gross in (("M", 1.0), ("P", 2.0), ("C", 2.0)):
        tr, dates, meta = [], [], []
        elig = []                                          # eligible days per coin for the placebo
        for k, x in enumerate(data):
            last = -1
            ek = []
            for i in range(60, len(x["c"]) - 1):
                if not ok_day(x, i):
                    continue
                b = bull.get(int(x["d"][i]), False)
                if rule == "M" or b:
                    ek.append(i)
                if i <= last or not sig(x, i, rule, b):
                    continue
                p = trade(x, i, rule)
                if p:
                    tr.append(p)
                    dates.append(int(x["d"][i]))
                    meta.append((x["sym"], x["q20"][i]))
                    last = i + len(p[0])                   # one position per coin and rule
            elig.append(ek)
        sp, pr = wport(tr, d0, d1, gross)
        st = vb.stats(sp, pr)
        pt = vb.per_trade([(a, b) for a, b, _ in tr], dates)
        h1 = [t for t, dd in zip(tr, dates) if dd < mid]
        h2 = [t for t, dd in zip(tr, dates) if dd >= mid]
        by_year = {}
        for t, dd in zip(tr, dates):
            y = 1970 + int(dd // 365.2425)
            by_year.setdefault(y, []).append(sum(t[1]))
        yrs = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())}
        surv = [t for t, m in zip(tr, meta) if m[0] in SURV]
        liq = [t for t, m in zip(tr, meta) if m[1] >= vb.LIQ_Q]
        # placebo: per coin, as many random eligible days as the rule traded there
        cnt = {}
        for m in meta:
            cnt[m[0]] = cnt.get(m[0], 0) + 1
        plac = []
        for rep in range(20):
            ptr = []
            for k, x in enumerate(data):
                nk = cnt.get(x["sym"], 0)
                if not nk or not elig[k]:
                    continue
                for j in rng.choice(elig[k], size=nk, replace=True):
                    p = trade(x, int(j), rule)
                    if p:
                        ptr.append(sum(p[1]))
            plac.append(np.mean(ptr) * 100 if ptr else 0.0)
        p95 = float(np.percentile(plac, 95))
        m1, m2 = tmean(h1), tmean(h2)
        ok = pt["mean_pct"] > p95 and pt["t_by_date"] >= 2 and (m1 or 0) > 0 and (m2 or 0) > 0
        row = {**st, "trades": pt, "half_means_pct": [m1, m2], "placebo_mean_pct": round(float(np.mean(plac)), 2),
               "placebo_p95_pct": round(p95, 2), "survivors": [tmean(surv), len(surv)], "liquid": [tmean(liq), len(liq)],
               "trade_years": yrs, "PASS": bool(ok)}
        summary[rule] = row
        vb.note(f"f36 · {rule}", f"{'PASS' if ok else 'no'} — trades {pt['n']} on {pt['dates']} days, mean {pt['mean_pct']}% "
                f"(median {pt['median_pct']}%, win {pt['win']}%, t by date {pt['t_by_date']}), halves {m1}% / {m2}%; placebo "
                f"{row['placebo_mean_pct']}% p95 {row['placebo_p95_pct']}%; 5 survivors {row['survivors'][0]}% on "
                f"{row['survivors'][1]}; liquid ≥$20M {row['liquid'][0]}% on {row['liquid'][1]}; account Sharpe {st['sharpe']} "
                f"(halves {st['halves'][0]}/{st['halves'][1]} vs BTC {bb['halves'][0]}/{bb['halves'][1]}), {st['cagr_pct']}%/yr, "
                f"DD {st['dd_pct']}%, hurdle {st['hurdle']}; trade mean by year {yrs}")
    vb.note("f36 · BTC", f"Sharpe {bb['sharpe']} halves {bb['halves']} {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%; "
            f"«рост» on {round(100 * sum(bull.values()) / max(1, len(bull)))}% of days; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
