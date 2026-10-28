"""Backlog #50 (28.10.2026, Dmytro's priority): a short rule that earns while BTC is NOT in «рост».

Same archive and costs as scripts/voltbreak_all_study.py / families36_study.py (data.binance.vision daily klines + funding,
2020-01 → 2026-09, every USDT-M perpetual, delisted included; 0.05 % taker + 0.02 % slippage per side, +0.15 % when 20-day
turnover < $5M; real funding — a short RECEIVES positive funding; coin tradable after 60 days with 20-day turnover ≥ $1M).
Four rules, fixed BEFORE the run (no tuning). «рост» = BTC above its 200-day average and above its close 60 days ago.

  A — breakdown: close below the previous 20-day low, signal-day dollar volume ≥ 1.5 × its 30-day average, BTC below its
      200-day average → short at the next open; hold 5 days (exit at the close of day 5), stop 2 ATR(14) above the entry.
  B — bounce short: coin in a downtrend (close below a falling 50-day average, falling 20-day average), the day's high
      reached the 20-day average and RSI(2) > 90, BTC not «рост» → short at the next open; exit at the next open after a
      close below the 5-day average, or after 10 days; stop 2.5 ATR.
  F — hot funding on alts (BTC, ETH excluded): the signal day's funding ≥ 0.09 % (= 0.03 % per 8 h) and the 3-day sum
      ≥ 0.24 %, BTC below its 50-day average → short at the next open; hold 5 days; stop 2.5 ATR. Open interest is NOT used
      (daily OI exists only as per-day files from 12.2021 — too heavy here); stated as a limit.
  D — mirror of rule C: close below the previous 20-day low and below the 20-day average, volume ≥ 1.5 ×, BTC not «рост» →
      short at the next open; exit at the next open after a close 3 ATR above the best (lowest) close, or after 14 days;
      stop 2.5 ATR.
  Stops: 2.5 ATR (A: 2 ATR) from the entry, clipped to 3 %…15 %; a stop hit on a daily high fills at the stop (at the open
  if the day gaps through it). Size = 0.5 % of equity lost at the stop, notional ≤ 25 %, gross ≤ 200 %.

Honesty: placebo = the same coins on random days in the same BTC filter, same exits, 20 draws. Variants: 4 → luck hurdle
N = 516 + 4 = 520, t threshold corrected for 4 variants: t by date ≥ 2.24 (one-sided 0.05 / 4).
PASS (fixed now): mean trade > placebo p95, t ≥ 2.24, both halves > 0, trade mean > 0 in 2022 and in 2025–26,
account max drawdown ≤ 25 %, and the 50/50 mix with the long rule C (own desk, «рост» only) has a higher Sharpe than C alone.
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

DAY = vb.DAY
SURV = set(vb.SURV)
N_LUCK = 516 + 4
T_MIN = 2.24
HOLD = {"A": 5, "B": 10, "F": 5, "D": 14}


def prep(x):
    x = f36.prep(x)
    l = x["l"]
    n = len(l)
    ll = np.full(n, np.nan)
    for i in range(20, n):
        ll[i] = l[i - 20:i].min()
    x["ll20"] = ll
    return x


def rsi2(c, i):
    return f36.rsi2(c, i)


def sig(x, i, rule, reg):
    """reg = (bull, below200, below50) for BTC on that day."""
    bull, b200, b50 = reg
    c, q = x["c"], x["q"]
    if rule == "A":
        return b200 and c[i] < x["ll20"][i] and q[i] >= 1.5 * x["q30"][i]
    if rule == "D":
        return (not bull) and c[i] < x["ll20"][i] and c[i] < x["s20"][i] and q[i] >= 1.5 * x["q30"][i]
    if rule == "B":
        return ((not bull) and c[i] < x["s50"][i] and x["s50"][i] < x["s50"][i - 5] and x["s20"][i] < x["s20"][i - 5]
                and x["h"][i] >= x["s20"][i] and rsi2(c, i) > 90)
    if rule == "F":
        return (b50 and x["sym"] not in ("BTCUSDT", "ETHUSDT") and x["f"][i] >= 0.0009
                and x["f"][i - 2:i + 1].sum() >= 0.0024)
    return False


def in_filter(rule, reg):
    bull, b200, b50 = reg
    return {"A": b200, "D": not bull, "B": not bull, "F": b50}[rule]


def trade(x, i, rule):
    """Short at the open of i+1 → (days, daily net returns, weight) or None."""
    o, h, c, f, d, atr = x["o"], x["h"], x["c"], x["f"], x["d"], x["atr"]
    n = len(c)
    if i + 1 >= n or not x["cont"][i + 1]:
        return None
    e = o[i + 1]
    a = atr[i]
    dist = min(max((2.0 if rule == "A" else 2.5) * a / e, 0.03), 0.15)
    stop = e * (1 + dist)
    w = min(0.25, 0.005 / dist)
    cost = vb.FEE + vb.SLIP + (vb.SLIP_SMALL if x["q20"][i] < vb.SMALL_Q else 0.0)
    hold = HOLD[rule]
    days, rets = [], []
    best = e
    prev = e
    j = i + 1
    while j < n:
        if j > i + 1 and not x["cont"][j]:
            break
        if h[j] >= stop:
            px = max(o[j], stop) if j > i + 1 else stop
            days.append(int(d[j])); rets.append(-(px / prev - 1) + f[j])
            break
        days.append(int(d[j])); rets.append(-(c[j] / prev - 1) + f[j])
        prev = c[j]
        best = min(best, c[j])
        k = j - i
        if rule in ("A", "F"):
            if k >= hold:
                break
        else:
            out = k >= hold or (rule == "B" and c[j] < x["s5"][j]) or (rule == "D" and c[j] > best + 3 * atr[j])
            if out:
                if j + 1 < n and x["cont"][j + 1]:
                    days.append(int(d[j + 1])); rets.append(-(o[j + 1] / prev - 1))
                break
        j += 1
    if not rets:
        return None
    rets[0] -= cost
    rets[-1] -= cost
    return days, rets, w


def year_of(dd):
    return 1970 + int(dd // 365.2425)


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("s50 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    btc = next(x for x in data if x["sym"] == "BTCUSDT")
    bc = btc["c"]
    reg = {}
    for k in range(200, len(bc)):
        s200 = bc[k - 199:k + 1].mean()
        s50 = bc[k - 49:k + 1].mean()
        reg[int(btc["d"][k])] = (bool(bc[k] > s200 and bc[k] > bc[k - 60]), bool(bc[k] < s200), bool(bc[k] < s50))
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    mid = (d0 + d1) // 2
    span = np.arange(d0, d1 + 1)
    bpr = np.zeros(len(span))
    for j in range(1, len(bc)):
        bpr[int(btc["d"][j]) - d0] = bc[j] / bc[j - 1] - 1
    bb = vb.stats(span, bpr)
    nb = sum(1 for v in reg.values() if not v[0])
    vb.note("s50 · BTC", f"Sharpe {bb['sharpe']} {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%; NOT «рост» on "
            f"{round(100 * nb / max(1, len(reg)))}% of days, below SMA200 on "
            f"{round(100 * sum(v[1] for v in reg.values()) / max(1, len(reg)))}%")

    # the long side for the mix: rule C of the own desk («рост» only), as in families36
    bull = {k: v[0] for k, v in reg.items()}
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
    vb.note("s50 · long C", f"{len(ctr)} trades, Sharpe {cst['sharpe']} halves {cst['halves']} {cst['cagr_pct']}%/yr "
            f"DD {cst['dd_pct']}%, years {cst['years']}")

    rng = np.random.default_rng(50)
    for rule in ("A", "B", "F", "D"):
        tr, dates, meta, elig = [], [], [], []
        for x in data:
            last = -1
            ek = []
            for i in range(60, len(x["c"]) - 1):
                if not f36.ok_day(x, i):
                    continue
                r = reg.get(int(x["d"][i]))
                if r is None:
                    continue
                if in_filter(rule, r):
                    ek.append(i)
                if i <= last or not sig(x, i, rule, r):
                    continue
                p = trade(x, i, rule)
                if p:
                    tr.append(p)
                    dates.append(int(x["d"][i]))
                    meta.append((x["sym"], x["q20"][i]))
                    last = i + len(p[0])
            elig.append(ek)
        if len(tr) < 10:
            vb.note(f"s50 · {rule}", f"too few trades ({len(tr)})")
            continue
        sp, pr = f36.wport(tr, d0, d1, 2.0)
        st = vb.stats(sp, pr)
        pt = vb.per_trade([(a, b) for a, b, _ in tr], dates)
        h1 = [t for t, dd in zip(tr, dates) if dd < mid]
        h2 = [t for t, dd in zip(tr, dates) if dd >= mid]
        by_year = {}
        for t, dd in zip(tr, dates):
            by_year.setdefault(year_of(dd), []).append(sum(t[1]))
        yrs = {y: f"{np.mean(v) * 100:+.2f}%/{len(v)}" for y, v in sorted(by_year.items())}
        y22 = np.mean(by_year.get(2022, [0])) * 100
        y2526 = by_year.get(2025, []) + by_year.get(2026, [])
        y2526m = np.mean(y2526) * 100 if y2526 else 0.0
        liq = [t for t, m in zip(tr, meta) if m[1] >= vb.LIQ_Q]
        surv = [t for t, m in zip(tr, meta) if m[0] in SURV]
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
        m1, m2 = f36.tmean(h1), f36.tmean(h2)
        mix = 0.5 * cpr + 0.5 * pr
        mst = vb.stats(span, mix)
        ok = (pt["mean_pct"] > p95 and pt["t_by_date"] >= T_MIN and (m1 or 0) > 0 and (m2 or 0) > 0 and y22 > 0
              and y2526m > 0 and st["dd_pct"] >= -25 and mst["sharpe"] > cst["sharpe"])
        vb.note(f"s50 · {rule}", f"{'PASS' if ok else 'no'} — trades {pt['n']} on {pt['dates']} days, mean {pt['mean_pct']}% "
                f"(median {pt['median_pct']}%, win {pt['win']}%, t by date {pt['t_by_date']} need {T_MIN}), halves "
                f"{m1}% / {m2}%; placebo {round(float(np.mean(plac)), 2)}% p95 {round(p95, 2)}%; 2022 {y22:+.2f}%, "
                f"2025–26 {y2526m:+.2f}%; liquid ≥$20M {f36.tmean(liq)}% on {len(liq)}; 5 large {f36.tmean(surv)}% on "
                f"{len(surv)}; account Sharpe {st['sharpe']} (halves {st['halves']}), {st['cagr_pct']}%/yr, DD {st['dd_pct']}%, "
                f"hurdle {st['hurdle']}, years {st['years']}; mix 50/50 with long C: Sharpe {mst['sharpe']} vs C "
                f"{cst['sharpe']}, DD {mst['dd_pct']}% vs {cst['dd_pct']}%; trade mean by year {yrs}")
    vb.note("s50 · done", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main())
