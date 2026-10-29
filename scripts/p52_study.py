"""Backlog #52 (29.10.2026): market-neutral funding carry on Binance USDT-M perpetuals.

Public idea: Vine 2026 (https://bryanvine.github.io/alpha-research/paper2.html, 30 perps 2023–05.2026) — out of sample
Sharpe 0.39 against 0.71 in sample, probability of overfitting 0.81, drawdown −35 %. We expect a weak result and test it
honestly on a much wider universe.
Rule, fixed BEFORE the run (one configuration, no tuning):
  every 7 days, at the daily close: universe = every perpetual with 20-day average turnover ≥ $20M, 8 clean days and a
  funding record; rank by the sum of funding over the last 7 days; LONG the third with the lowest funding, SHORT the
  third with the highest, equal weights, 0.5× gross per side (1× gross, net 0). Positions drift inside the week.
  Returns: close-to-close price move plus real funding (a long PAYS positive funding, a short receives it).
  Costs: 0.05 % taker + 0.02 % slippage on every unit of turnover at each rebalance. A coin whose data ends (delisted)
  is closed at its last close.
Data: data.binance.vision 1d klines + fundingRate, 2020-01 → 2026-09, every USDT-M perpetual, delisted included.
Honesty: placebo = the same universe each week, random thirds long/short, 20 draws. Variants: 1 → luck hurdle N = 525.
PASS (fixed now): account Sharpe > the luck hurdle AND > placebo p95, both halves Sharpe > 0, t of weekly returns ≥ 2,
max drawdown ≥ −25 %. Reported, not a gate: price part vs funding part of the result, legs separately, 2023+ only
(the public paper's window), years.
Public data only, no keys, no orders.
"""
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voltbreak_all_study as vb  # noqa: E402
import families36_study as f36  # noqa: E402

N_LUCK = 525
STEP = 7
LOOK = 7
LIQ = 20e6
COST = vb.FEE + vb.SLIP


def grid(data, d0, d1):
    """→ price-return R, funding F, turnover Q20 and a «has bar» mask, all (symbols × days)."""
    n, m = len(data), d1 - d0 + 1
    R = np.zeros((n, m)); F = np.zeros((n, m)); Q = np.zeros((n, m)); H = np.zeros((n, m), bool)
    FR = np.zeros((n, m), bool)
    for k, x in enumerate(data):
        idx = x["d"] - d0
        H[k, idx] = True
        c = x["c"]
        r = np.r_[0.0, c[1:] / c[:-1] - 1]
        r[~x["cont"]] = 0.0
        R[k, idx] = r
        F[k, idx] = x["f"]
        FR[k, idx] = x["f"] != 0
        Q[k, idx] = np.nan_to_num(x["q20"])
    return R, F, Q, H, FR


def run(R, F, Q, H, FR, rng=None, legs=(1, 1)):
    n, m = R.shape
    w = np.zeros(n)
    pr = np.zeros(m); price = np.zeros(m); fund = np.zeros(m); cost = np.zeros(m)
    weekly = []
    wk = 0.0
    sizes = []
    for t in range(LOOK + 20, m - 1):
        # daily P&L of positions held from close t-1 to close t (drifted weights)
        if t > LOOK + 20:
            alive = H[:, t]
            w[~alive] = 0.0                     # data ended → closed at the last close
            pp = float(w @ R[:, t]); ff = float(-(w @ F[:, t]))
            day = pp + ff
            pr[t] += day; price[t] += pp; fund[t] += ff
            wk = (1 + wk) * (1 + day) - 1
            w = w * (1 + R[:, t]) / (1 + day) if 1 + day > 0 else w * 0
        if (t - LOOK - 20) % STEP == 0:
            if t > LOOK + 20:
                weekly.append(wk)
            wk = 0.0
            ok = (Q[:, t] >= LIQ) & H[:, t - LOOK:t + 2].all(axis=1) & FR[:, t - LOOK + 1:t + 1].any(axis=1)
            idx = np.flatnonzero(ok)
            nw = np.zeros(n)
            if len(idx) >= 9:
                k3 = len(idx) // 3
                if rng is None:
                    s = F[idx, t - LOOK + 1:t + 1].sum(axis=1)
                    order = idx[np.argsort(s, kind="stable")]
                else:
                    order = rng.permutation(idx)
                lo, hi = order[:k3], order[-k3:]
                nw[lo] = 0.5 / k3 * legs[0]
                nw[hi] = -0.5 / k3 * legs[1]
                sizes.append(len(idx))
            c = COST * float(np.abs(nw - w).sum())
            pr[t] -= c; cost[t] += c
            w = nw
    return pr, price, fund, cost, np.array(weekly), sizes


def sharpe(p):
    return round(p.mean() / p.std() * math.sqrt(365), 2) if p.std() > 0 else 0.0


def main():
    vb.N_LUCK = N_LUCK
    t0 = time.time()
    syms = vb.list_symbols()
    with ThreadPoolExecutor(24) as ex:
        data = [f36.prep(x) for x in ex.map(f36.load, syms) if x is not None]
    live_end = max(int(x["d"][-1]) for x in data)
    dead = sum(1 for x in data if x["d"][-1] < live_end - 3)
    vb.note("p52 · loaded", f"{len(syms)} symbols, {len(data)} usable, {dead} delisted; {time.time() - t0:.0f}s")
    d0 = min(int(x["d"][0]) for x in data)
    d1 = live_end
    span = np.arange(d0, d1 + 1)
    R, F, Q, H, FR = grid(data, d0, d1)
    btc = next(k for k, x in enumerate(data) if x["sym"] == "BTCUSDT")
    bb = vb.stats(span, R[btc])
    vb.note("p52 · BTC", f"Sharpe {bb['sharpe']} (halves {bb['halves']}) {bb['cagr_pct']}%/yr DD {bb['dd_pct']}%")

    pr, price, fund, cost, weekly, sizes = run(R, F, Q, H, FR)
    first = np.flatnonzero(pr != 0)
    a = int(first[0]) if len(first) else 0
    sp, p = span[a:], pr[a:]
    st = vb.stats(sp, p)
    tw = weekly.mean() / (weekly.std(ddof=1) / math.sqrt(len(weekly))) if len(weekly) > 2 else 0.0
    yrs = len(p) / 365
    plac = []
    rng = np.random.default_rng(52)
    for _ in range(20):
        q, *_ = run(R, F, Q, H, FR, rng=rng)
        plac.append(sharpe(q[a:]))
    p95 = float(np.percentile(plac, 95))
    lo_leg, *_r = run(R, F, Q, H, FR, legs=(1, 0))
    hi_leg, *_r = run(R, F, Q, H, FR, legs=(0, 1))
    m23 = sp >= int(np.datetime64("2023-01-01").astype("datetime64[D]").astype(np.int64))
    s23 = sharpe(p[m23]) if m23.any() else 0.0
    ok = st["sharpe"] > st["hurdle"] and st["sharpe"] > p95 and min(st["halves"]) > 0 and tw >= 2 and st["dd_pct"] >= -25
    vb.note("p52 · carry", f"{'PASS' if ok else 'no'} — Sharpe {st['sharpe']} (halves {st['halves']}, hurdle {st['hurdle']}), "
            f"{st['cagr_pct']}%/yr, DD {st['dd_pct']}%, weeks {len(weekly)} t {tw:.2f}; placebo Sharpe mean "
            f"{np.mean(plac):.2f} p95 {p95:.2f}; universe per week median {int(np.median(sizes)) if sizes else 0} "
            f"(min {min(sizes) if sizes else 0}, max {max(sizes) if sizes else 0}); 2023+ Sharpe {s23}")
    vb.note("p52 · parts", f"per year: price {price[a:].sum() / yrs * 100:+.1f}%, funding {fund[a:].sum() / yrs * 100:+.1f}%, "
            f"costs {-cost[a:].sum() / yrs * 100:.1f}%; long-low-funding leg alone Sharpe {sharpe(lo_leg[a:])} "
            f"({vb.stats(sp, lo_leg[a:])['cagr_pct']}%/yr), short-high-funding leg alone Sharpe {sharpe(hi_leg[a:])} "
            f"({vb.stats(sp, hi_leg[a:])['cagr_pct']}%/yr); years {st['years']}")
    vb.note("p52 · done", f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
